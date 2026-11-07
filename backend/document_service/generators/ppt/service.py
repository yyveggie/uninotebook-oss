import logging
import uuid
import shutil
import asyncio
from pathlib import Path
from typing import List, Optional
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import UploadFile, HTTPException, BackgroundTasks
import os
import aiofiles
import time
from database import AsyncSessionLocal
from db_models import User, Document, DocumentPPT
from document_service.service import DocumentService
from config import settings
from document_service.engines.paper2slides.core import run_pipeline, get_base_dir, get_config_dir, detect_start_stage
from document_service.engines.paper2slides.core.state import STAGES, create_state, save_state
from document_service.engines.paper2slides.utils.path_utils import get_project_name
from utils.cos_service import COSService
from runtime.paths import data_path

logger = logging.getLogger(__name__)

# Directory config
UPLOAD_DIR = data_path("ppt_uploads")
OUTPUT_DIR = data_path("ppt_output")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

class SessionManager:
    """Simple session manager for cancellation"""
    def __init__(self):
        self.cancelled_sessions = set()
    
    def is_cancelled(self, session_id: str) -> bool:
        return session_id in self.cancelled_sessions
        
    def cancel_session(self, session_id: str):
        self.cancelled_sessions.add(session_id)
        logger.info(f"Session {session_id} marked for cancellation")

session_manager = SessionManager()


def parse_ppt_style(style_value: Optional[str]):
    """解析 DocumentPPT.style（存储格式: style|language|mode|output_type）"""
    style_parts = (style_value or "academic").split('|')
    style = style_parts[0]
    language = style_parts[1] if len(style_parts) > 1 else "zh"
    mode = style_parts[2] if len(style_parts) > 2 else "paper"
    output_type = style_parts[3] if len(style_parts) > 3 else "slides"
    return style, language, mode, output_type


def build_ppt_pipeline_config(ppt_task: DocumentPPT, input_path: Optional[str] = None) -> dict:
    """
    构建流水线配置。worker（run_ppt_generation_task）与状态查询（get_ppt_status）必须共用，
    否则两边算出的 config_dir 不同，状态查询读不到 worker 写的 state.json，进度永远不同步。
    """
    style, language, mode, output_type = parse_ppt_style(ppt_task.style)
    config = {
        "content_type": mode,  # "paper" or "general"
        "output_type": output_type,  # "slides" or "poster"
        "style": style,
        "language": language,
        "slides_length": "medium",
        "fast_mode": True,  # Enable Fast Mode (Skip Indexing)
        "max_workers": 4,  # Increase concurrency for image generation
    }
    if input_path:
        config["input_path"] = input_path  # Use specific file path to avoid processing other files in dir
        config["pdf_paths"] = [input_path]  # paper2slides expects a list of paths
    return config


async def _enqueue_ppt_task(db: AsyncSession, ppt_task: DocumentPPT) -> None:
    """提交 Celery 任务；提交失败时把记录标记为 failed，避免永远停在 pending。"""
    from tasks.ppt_tasks import generate_ppt
    try:
        generate_ppt.delay(ppt_task.id)
    except Exception as e:
        logger.error(f"Failed to enqueue PPT task {ppt_task.id}: {e}")
        try:
            ppt_task.status = "failed"
            ppt_task.error_message = "任务提交失败，请稍后重试"
            await db.commit()
        except Exception:
            await db.rollback()
        raise e


def cleanup_session_data(session_id: str):
    """Delete heavy intermediate files (RAG storage/output) to save disk space.
    Called during pipeline execution, before final cleanup."""
    try:
        base_dir = OUTPUT_DIR / f"session_{session_id[:8]}" / "paper"
        heavy_dirs = [
            base_dir / "rag_storage",
            base_dir / "rag_output"
        ]
        cleanup_count = 0
        for d in heavy_dirs:
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                cleanup_count += 1
        
        if cleanup_count > 0:
            logger.info(f"Cleaned up {cleanup_count} heavy directories for session {session_id}")
    except Exception as e:
        logger.warning(f"Failed to clean up session data for {session_id}: {e}")


def cleanup_all_session_files(session_id: str, source_file_path: str = None):
    """
    Full cleanup: delete ALL local files for a session after COS upload is confirmed.
    
    Removes:
    - Entire session output directory: data/ppt_output/session_{id[:8]}/
    - Source upload file: data/ppt_uploads/{file} or data/ppt_uploads/{session_id}/
    """
    cleaned = []
    
    try:
        # 1. Delete entire session output directory
        session_output_dir = OUTPUT_DIR / f"session_{session_id[:8]}"
        if session_output_dir.exists():
            shutil.rmtree(session_output_dir, ignore_errors=True)
            cleaned.append(f"output dir: {session_output_dir}")
        
        # 2. Delete source upload file
        if source_file_path:
            source_path = Path(source_file_path)
            if source_path.exists():
                if source_path.is_file():
                    source_path.unlink(missing_ok=True)
                    cleaned.append(f"source file: {source_path.name}")
                    # Also remove parent dir if it's an empty session upload dir
                    parent = source_path.parent
                    if parent != UPLOAD_DIR and parent.exists():
                        try:
                            parent.rmdir()  # Only removes if empty
                            cleaned.append(f"upload dir: {parent.name}")
                        except OSError:
                            pass  # Not empty, skip
        
        if cleaned:
            logger.info(f"🧹 Full cleanup for session {session_id[:8]}: {', '.join(cleaned)}")
        else:
            logger.info(f"🧹 No local files to clean for session {session_id[:8]}")
    except Exception as e:
        logger.warning(f"Failed full cleanup for session {session_id[:8]}: {e}")

async def create_ppt_task_with_upload(
    db: AsyncSession,
    user_id: int,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    document_id: Optional[int] = None, # Optional: link to existing doc if known
    style: str = "academic",
    language: str = "zh",
    mode: str = "paper",
    output_type: str = "slides",
    title: str = None
) -> DocumentPPT:
    """
    Create a PPT generation task from an uploaded file.
    Saves file to disk and creates DB record.
    """
    from runtime.features import require_capability
    require_capability("ppt")
    # Allow non-PDF files for flexibility, but pipeline might only support PDF well.
    # if not file.filename.lower().endswith('.pdf'):
    #     raise HTTPException(status_code=400, detail="Only PDF files are supported")

    # Generate session ID (used as folder name)
    session_id = str(uuid.uuid4())
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    
    # Save file（只取文件名部分，防止 "../" 之类的路径穿越写到上传目录之外）
    safe_filename = Path(file.filename or "upload.pdf").name or "upload.pdf"
    file_path = session_dir / safe_filename
    max_bytes = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        async with aiofiles.open(file_path, "wb") as buffer:
            # UploadFile.read() is async? fastAPI UploadFile has async methods.
            # But file.file is the python file object.
            # Better to use file.read() which is async in FastAPI if we await it.
            # Or assume we can read chunks.
            # Let's read it all for now or chunks.
            while content := await file.read(1024 * 1024): # 1MB chunks
                written += len(content)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大：单个文件不能超过 {settings.max_upload_mb} MB，请压缩或拆分后重试",
                    )
                await buffer.write(content)
    except HTTPException:
        shutil.rmtree(session_dir, ignore_errors=True)
        raise
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail="Failed to save file")
    
    # Determine title
    ppt_title = title or safe_filename
    
    # If document_id is not provided, we might want to create a dummy document record or handle it?
    # In `DocumentPPT` model, `document_id` is foreign key and nullable=False?
    # Wait, `document_id` is nullable=False in my model definition.
    # So I MUST have a document. 
    # If user just uploads a file, I should probably CREATE a Document record for it first?
    # Or I should have made `document_id` nullable.
    # For now, let's assume we link it to a document.
    # If `document_id` is missing, we create a placeholder Document?
    
    if not document_id:
        # Create a placeholder document
        from db_models import Document
        new_doc = Document(
            user_id=user_id,
            filename=safe_filename,
            file_type='pdf', # Assuming PDF for now, or derive from file.filename
            original_text="Placeholder for PPT generation",
            vector_store_id=f"ppt_{session_id}",
            processing_status='completed' # Skip processing
        )
        db.add(new_doc)
        await db.commit()
        await db.refresh(new_doc)
        document_id = new_doc.id

    # Create Task Record
    # Store session_id in `current_stage` field temporarily or add a new field?
    # I didn't add `session_id` to DocumentPPT.
    # I can use `ppt_file_path` to store the input path or output path.
    # Let's use `ppt_file_path` for user access later.
    # We can rely on `id` -> `session` mapping if needed, or store it.
    # Actually, let's just store session_id in `current_stage`? No that's hacky.
    # I'll modify the DB model to add `session_id` or just pass it to background task.
    # Wait, I already added `session_id` to `DocumentChatHistory`. `DocumentPPT` doesn't have it.
    # I'll rely on passing it to the background function.
    
    style_val = f"{style}|{language}|{mode}|{output_type}" # Store output_type
    ppt_task = DocumentPPT(
        document_id=document_id,
        user_id=user_id,
        title=ppt_title,
        style=style_val,
        status="pending",
        session_id=session_id, # Assuming this field exists in DocumentPPT
        source_file_path=str(file_path), # Store the path to the uploaded file
        file_size=file.size
    )
    db.add(ppt_task)
    await db.commit()
    await db.refresh(ppt_task)

    # 🚀 使用 Celery 任务替代 BackgroundTasks
    await _enqueue_ppt_task(db, ppt_task)

    return ppt_task

async def create_ppt_task_from_document(
    document_id: int,
    user_id: int,
    title: str,
    style: str,
    language: str,
    mode: str,
    output_type: str,
    db: AsyncSession,
    background_tasks: BackgroundTasks
) -> DocumentPPT:
    """Create a PPT generation task from an existing document."""
    from runtime.features import require_capability
    require_capability("ppt")
    
    # 1. Fetch document（🛡️ 必须属于当前用户，否则按不存在处理，避免 IDOR）
    doc_service = DocumentService(db)
    document = await doc_service.get_document_by_id(document_id)
    if not document or document.user_id != user_id:
        raise HTTPException(status_code=404, detail="Document not found")

    # 2. Use existing file hash or calculate if missing
    if not document.original_text:
        raise HTTPException(status_code=400, detail="Document has no content")

    content_hash = document.file_hash
    if not content_hash:
        import hashlib
        content_hash = hashlib.md5(document.original_text.encode('utf-8')).hexdigest()
        logger.warning(f"Document {document.id} missing file_hash, calculated: {content_hash}")
    
    safe_filename = f"{document.id}_{content_hash}.txt"
    file_path = UPLOAD_DIR / safe_filename
    
    # Ensure upload dir exists
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if not file_path.exists():
        async with aiofiles.open(file_path, 'w', encoding='utf-8') as f:
            await f.write(document.original_text)
        logger.info(f"Created new content snapshot: {safe_filename}")
    else:
        logger.info(f"Reusing existing content snapshot: {safe_filename}")

    # 3. Create PPT task record
    
    # --- REUSE LOGIC ---
    # Check if there's an existing PPT task for this document/user to reuse session_id.
    # This allows skipping RAG/Summary/Plan stages if they are already done.
    stmt = select(DocumentPPT).where(
        DocumentPPT.document_id == document_id,
        DocumentPPT.user_id == user_id,
        DocumentPPT.session_id.isnot(None)
    ).order_by(DocumentPPT.id.desc()).limit(1)
    
    result = await db.execute(stmt)
    existing_task = result.scalar_one_or_none()
    
    if existing_task:
        session_id = existing_task.session_id
        logger.info(f"Reusing existing session_id {session_id} for doc {document_id}")
    else:
        session_id = str(uuid.uuid4()) # Generate a new session ID for this task
    # -------------------

    ppt_task = DocumentPPT(
        user_id=user_id,
        document_id=document.id,
        title=title or document.filename,
        style=f"{style}|{language}|{mode}|{output_type}", # Store output_type
        status="pending",
        session_id=session_id, # Store the generated/reused session ID
        source_file_path=str(file_path), # Store the path to the temporary file
        file_size=len(document.original_text) if document.original_text else 0
    )
    
    db.add(ppt_task)
    await db.commit()
    await db.refresh(ppt_task)

    # 🚀 使用 Celery 任务替代 BackgroundTasks
    await _enqueue_ppt_task(db, ppt_task)

    return ppt_task

async def get_ppt_status(db: AsyncSession, ppt_id: int, user_id: int) -> DocumentPPT:
    """Get status of a PPT task"""
    result = await db.execute(
        select(DocumentPPT).where(DocumentPPT.id == ppt_id, DocumentPPT.user_id == user_id)
    )
    ppt = result.scalar_one_or_none()
    if not ppt:
        raise HTTPException(status_code=404, detail="PPT task not found")
        
    # --- Sync Status from State File ---
    # Sync if processing OR if completed but missing file path (retry to find it)
    if (ppt.status == "processing" or (ppt.status == "completed" and not ppt.ppt_file_path)) and ppt.session_id:
        try:
            # Try to find state.json to get real progress
            project_name = f"session_{ppt.session_id[:8]}"
            # We don't know content_type easily? Default to "paper" or check both.
            # Usually it's "paper".
            base_dir = get_base_dir(str(OUTPUT_DIR), project_name, "paper")
            
            # Find any state.json in subdirs
            state_file = None
            # Reconstruct config to find the exact directory
            # 与 run_ppt_generation_task 共用同一个构建函数（style 字段是 "style|language|mode|output_type"）
            config = build_ppt_pipeline_config(ppt)
            config_dir = get_config_dir(base_dir, config)
            
            # Check for state.json specifically in this config dir
            possible_state = config_dir / "state.json"
            if possible_state.exists():
                state_file = possible_state
            
            if state_file:
                import json
                async with aiofiles.open(state_file, 'r') as f:
                    content = await f.read()
                    state = json.loads(content)
                
                stages = state.get("stages", {})
                
                # --- LOGGING FOR USER VISIBILITY (Debounced) ---
                # Use a simple module-level dict to track last logged state
                global _last_logged_status
                if '_last_logged_status' not in globals():
                    _last_logged_status = {}
                
                status_str = str(stages)
                if _last_logged_status.get(ppt.id) != status_str:
                    logger.info(f"🔄 [Task {ppt.id}] Status Sync: {stages}")
                    _last_logged_status[ppt.id] = status_str
                # -----------------------------------
                
                # --- SYNC PLAN TO DB (Intermediate Result) ---
                if stages.get("plan") == "completed" and (not ppt.outline):
                     try:
                         # Attempt to load plan.json
                         config_dir = state_file.parent
                         plan_file = config_dir / "plan.json"
                         if plan_file.exists():
                             async with aiofiles.open(plan_file, 'r') as pf:
                                 plan_content = await pf.read()
                                 ppt.outline = json.loads(plan_content)
                                 logger.info(f"✅ [Task {ppt.id}] Saved Plan to DB Outline")
                     except Exception as plan_err:
                         logger.error(f"Failed to sync plan to DB for task {ppt.id}: {plan_err}")
                # ---------------------------------------------
                
                # Update status
                if any(s == "failed" for s in stages.values()):
                    ppt.status = "failed"
                    ppt.error_message = state.get("error", "Unknown error")
                elif all(s == "completed" for s in stages.values()):
                    ppt.status = "completed"
                    ppt.progress = 100
                    # preview_images will be populated by background task from COS
                    # No need to search for PDF anymore

                else:
                    # Check for granular RAG progress (Fast Mode)
                    rag_progress_file = base_dir / "rag_output" / "progress.json"
                    rag_percent = 0
                    if rag_progress_file.exists():
                        try:
                            import json
                            async with aiofiles.open(rag_progress_file, 'r') as rf:
                                r_content = await rf.read()
                                r_data = json.loads(r_content)
                                rag_percent = r_data.get("percent", 0)
                                # Map RAG percent (0-100 of stage 1) to global progress
                                # Stage 1 is approx 40% of total work in fast mode? 
                                # Let's say:
                                # Parsing/RAG triggers ~ 5-40%
                                # Summary ~ 40-50%
                                # Plan ~ 50-60%
                                # Generate ~ 60-100%
                                if rag_percent > 0:
                                    logger.debug(f"Read RAG progress: {rag_percent}%")
                        except Exception:
                            pass

                    # Calculate progress
                    total_stages = len(STAGES)
                    completed = sum(1 for s in stages.values() if s == "completed")
                    current = next((k for k, v in stages.items() if v == "running"), "processing")
                    
                    ppt.current_stage = current
                    
                    # Base progress from completed stages
                    base_progress = int((completed / total_stages) * 100)
                    
                    # Add current stage progress
                    added_progress = 0
                    if current == 'rag' and rag_percent > 0:
                         # RAG stage is 1/4th roughly = 25%?
                         # Map 0-100 rag_percent to 0-24%
                         added_progress = int(rag_percent * 0.24)
                    elif current == 'generate' and 'generate_progress' in state:
                        # ... existing generate logic ...
                        gen_prog = state['generate_progress']
                        if isinstance(gen_prog, dict) and gen_prog.get('total', 0) > 0:
                            current_img = gen_prog.get('current', 0)
                            total_img = gen_prog.get('total', 1)
                            added_progress = int((current_img / total_img) * 24) # 24% for generate

                    ppt.progress = min(99, base_progress + added_progress)
                
                await db.commit()
                await db.refresh(ppt)
                await db.commit()
                await db.refresh(ppt)
        except Exception as e:
            logger.error(f"Failed to sync state for task {ppt.id}: {e}")

    # --- Self-Healing: Missing Images ---
    # If completed but missing preview_images, try to load from COS
    if ppt.status == "completed" and (not ppt.preview_images or len(ppt.preview_images) == 0) and ppt.session_id:
        try:
            from utils.cos_service import COSService
            cos = COSService()
            prefix = f"ppt_output/{ppt.session_id}/"
            # List files from COS
            existing_files = cos.list_files_with_prefix(prefix, max_keys=50)
            if existing_files:
                # Sort to ensure order
                existing_files.sort()
                cos_image_urls = [cos.get_file_url(f) for f in existing_files]
                
                ppt.preview_images = cos_image_urls
                if not ppt.ppt_file_path and cos_image_urls:
                    ppt.ppt_file_path = cos_image_urls[0]
                
                # Check for uploaded PPT file if available? (Usually handled separately)
                
                logger.info(f"Self-healed task {ppt.id}: Loaded {len(cos_image_urls)} slides from COS")
                await db.commit()
                await db.refresh(ppt)
        except Exception as e:
            logger.warning(f"Failed to self-heal images for task {ppt.id}: {e}")


    return ppt

async def run_ppt_generation_task(
    ppt_task_id: int, 
    db_session_maker
) -> Optional[str]:
    """
    Background task to run the generation pipeline.

    Returns:
        None 表示成功；失败时返回错误信息（记录已标记为 failed）。
        流水线失败不抛异常（避免 Celery 自动重试整条昂贵流水线），
        由调用方（tasks/ppt_tasks.py）据此退款，确保失败一定会退且只退一次。
    """
    logger.info(f"Starting PPT generation for task {ppt_task_id}")
    
    async with db_session_maker() as db:
        # 1. Fetch the PPT task details
        result = await db.execute(select(DocumentPPT).where(DocumentPPT.id == ppt_task_id))
        ppt_task = result.scalar_one_or_none()
        if not ppt_task: 
            logger.error(f"PPT task {ppt_task_id} not found for background processing.")
            return f"PPT task {ppt_task_id} not found"
        
        session_id = ppt_task.session_id
        input_file_path = ppt_task.source_file_path
        
        logger.info(f"PPT generation task {ppt_task_id} (Session: {session_id}) using file: {input_file_path}")

        # 2. Validate source file path
        source_path = Path(input_file_path)
        if not source_path.exists():
            # Try to handle the case where we might have a relative path or issue
            if not source_path.is_absolute():
                 # Maybe it's relative to root or something? 
                 # In our upload logic we used UPLOAD_DIR / filename, stored as absolute? 
                 # create_ppt_task_with_upload stores `str(file_path)` which is absolute path object made from UPLOAD_DIR.
                 pass
            
            if not source_path.exists():
                ppt_task.status = "failed"
                ppt_task.error_message = f"Source file not found: {source_path}"
                await db.commit()
                return ppt_task.error_message

        # 3. Prepare Pipeline Config
        project_name = f"session_{session_id[:8]}"

        # Parse style string: style|language|mode|output_type（与 get_ppt_status 共用同一构建函数）
        config = build_ppt_pipeline_config(ppt_task, input_path=str(source_path))
        from llm_service import get_model_config
        from llm_service.llm_config import FEATURE_MODEL_MAP

        # 文本 / RAG / 规划阶段的模型（两种部署统一走功能映射，不再写死 Kimi）
        pipeline_profile = get_model_config(FEATURE_MODEL_MAP["paper_pipeline"])
        config.update({key: pipeline_profile[key] for key in ("api_key", "base_url", "model")})
        
        ppt_task.status = "processing"
        await db.commit()
        
        # Prepare paths variables outside try block for finally access
        config_dir = None
        real_output_dir = None
        session_id = ppt_task.session_id # ensure session_id key
        pipeline_error = None
        
        try:
            # Preparing paths
            base_dir = get_base_dir(str(OUTPUT_DIR), project_name, "paper")
            config_dir = get_config_dir(base_dir, config)
            
            # Re-import get_output_dir to be safe or just construct it
            from document_service.engines.paper2slides.core.paths import get_output_dir
            real_output_dir = get_output_dir(config_dir)

            # --- State Initialization (Replicating Paper2Slides logic) ---
            # Detect start stage
            from_stage = detect_start_stage(base_dir, config_dir, config)
            logger.info(f"Task {ppt_task_id}: Starting from stage {from_stage}")

            # Create and save initial state
            initial_state = create_state(config)
            initial_state["session_id"] = session_id
            
            # Mark prior stages as completed
            start_idx = STAGES.index(from_stage)
            for i in range(start_idx):
                initial_state["stages"][STAGES[i]] = "completed"
            
            # Run blocking I/O in executor
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, save_state, config_dir, initial_state)
            
            logger.info(f"Task {ppt_task_id}: Initial state saved.")
            
            # --- Enforce Model Configuration (Kimi for Text Stages) ---
            # Paper2Slides pipeline uses os.getenv to pick up these values.
            # We explicitly set them to the Moonshot configuration.
            original_llm_model = os.environ.get("LLM_MODEL")
            original_rag_base_url = os.environ.get("RAG_LLM_BASE_URL")
            
            # FORCE Unstructured Parser (Disable Mineru)
            os.environ["PARSER"] = "unstructured"
            
            # Text / RAG / Plan 阶段的模型（与上面 config 使用同一个功能映射）
            if pipeline_profile.get("api_key") and not settings.is_local_mode:
                os.environ["LLM_MODEL"] = pipeline_profile["model"]
                os.environ["RAG_LLM_API_KEY"] = pipeline_profile["api_key"]
                os.environ["RAG_LLM_BASE_URL"] = pipeline_profile["base_url"]
                logger.info(f"Configured Pipeline to use: {pipeline_profile['model']}")
            elif not settings.is_local_mode:
                logger.warning("PPT 流水线模型未配置 API Key，回退到默认环境变量")

            # Image generation uses `settings.image_gen_model` directly in `image_generator.py`
            # so we don't need to change that here.
            
            # Start Pipeline
            await run_pipeline(
                base_dir, 
                config_dir, 
                config, 
                from_stage=from_stage, # Use detected stage
                session_id=session_id, 
                session_manager=session_manager
            )
            
            # Restore env vars (optional, but good practice if threads share env, though os.environ is global process)
            if original_llm_model:
                os.environ["LLM_MODEL"] = original_llm_model
            else:
                os.environ.pop("LLM_MODEL", None)
                
            if original_rag_base_url:
                os.environ["RAG_LLM_BASE_URL"] = original_rag_base_url
            else:
               os.environ.pop("RAG_LLM_BASE_URL", None)

            # Success
            ppt_task.progress = 100
            
            # Cleanup heavy RAG files after success
            cleanup_session_data(session_id)
            
            
        except Exception as e:
            logger.error(f"PPT Generation failed: {e}", exc_info=True)
            ppt_task.status = "failed"
            ppt_task.error_message = str(e)
            pipeline_error = str(e) or type(e).__name__
            
        finally:
            # Always attempt to upload generated images, even if failed partially
            cos_upload_success = False
            if real_output_dir and real_output_dir.exists():
                logger.info(f"Scanning for images to upload in {real_output_dir}")
                # Find all images
                image_extensions = {".png", ".jpg", ".jpeg", ".webp"}
                # Sort by filename
                images = sorted([f for f in real_output_dir.iterdir() if f.suffix.lower() in image_extensions])
                
                cos_image_urls = []
                try:
                    cos = COSService()
                    
                    for img_file in images:
                        rel_path = img_file.relative_to(OUTPUT_DIR)
                        cos_key = f"ppt_output/{session_id}/{img_file.name}"
                        cos_url = cos.upload_file(str(img_file), cos_key)
                        if cos_url:
                            cos_image_urls.append(cos_url)
                            logger.info(f"Uploaded slide {img_file.name} to COS: {cos_url}")
                    
                    if cos_image_urls:
                        ppt_task.preview_images = cos_image_urls
                        if not ppt_task.ppt_file_path or not ppt_task.ppt_file_path.startswith('http'):
                             ppt_task.ppt_file_path = cos_image_urls[0]
                        logger.info(f"Task {ppt_task_id} sync completed with {len(cos_image_urls)} slides uploaded.")
                        cos_upload_success = True
                    else:
                         if ppt_task.status == 'completed':
                             logger.warning(f"Task {ppt_task_id} completed but no local images found to upload.")
                             # If completed but no local files, try loading from COS directly
                             try:
                                 prefix = f"ppt_output/{session_id}/"
                                 existing_files = cos.list_files_with_prefix(prefix, max_keys=50)
                                 if existing_files:
                                     cos_image_urls = [cos.get_file_url(f) for f in sorted(existing_files)]
                                     ppt_task.preview_images = cos_image_urls
                                     if not ppt_task.ppt_file_path:
                                         ppt_task.ppt_file_path = cos_image_urls[0]
                                     logger.info(f"Task {ppt_task_id} loaded {len(cos_image_urls)} existing slides from COS")
                                     cos_upload_success = True  # COS has images, safe to clean
                                 else:
                                     logger.error(f"Task {ppt_task_id} completed but no images in COS either!")
                             except Exception as load_err:
                                 logger.error(f"Failed to load existing COS images: {load_err}")


                except ImportError:
                     logger.error(f"Failed to import COSService. Ensure backend/utils/__init__.py exists and python path is correct.")
                except Exception as e:
                    logger.error(f"COS Upload failed for task {ppt_task_id}: {e}", exc_info=True)
                    if ppt_task.status == 'completed':
                        ppt_task.error_message = f"COS Upload Failed: {str(e)}"
            else:
                # No local output dir - images may already be in COS (generate_stage deleted them)
                if ppt_task.status == 'completed' or (ppt_task.preview_images and len(ppt_task.preview_images) > 0):
                    cos_upload_success = True
            
            # 🧹 Full cleanup: delete ALL local files after COS upload confirmed
            if cos_upload_success and session_id:
                cleanup_all_session_files(session_id, source_file_path=input_file_path)
            
            await db.commit()

        # 失败要让调用方感知（以便退款），见函数文档
        return pipeline_error

async def get_ppt_history_by_doc(db: AsyncSession, document_id: int, user_id: int) -> List[DocumentPPT]:
    """Get all PPT generation history for a document."""
    result = await db.execute(
        select(DocumentPPT)
        .where(DocumentPPT.document_id == document_id, DocumentPPT.user_id == user_id)
        .order_by(DocumentPPT.created_at.desc())
    )
    return result.scalars().all()

async def get_ppt_detail(db: AsyncSession, ppt_id: int, user_id: int) -> Optional[DocumentPPT]:
    """Get specific PPT detail."""
    result = await db.execute(
        select(DocumentPPT)
        .where(DocumentPPT.id == ppt_id, DocumentPPT.user_id == user_id)
    )
    return result.scalar_one_or_none()

async def delete_ppt(db: AsyncSession, ppt_id: int, user_id: int) -> bool:
    """Delete a PPT task."""
    result = await db.execute(
        select(DocumentPPT).where(
            DocumentPPT.id == ppt_id,
            DocumentPPT.user_id == user_id
        )
    )
    ppt = result.scalar_one_or_none()
    
    if ppt:
        await db.delete(ppt)
        await db.commit()
        return True
    return False

async def cancel_all_ppt_tasks(db: AsyncSession, user_id: int) -> int:
    """
    Cancel all pending/processing PPT tasks for the user.
    Returns the number of tasks cancelled.
    """
    # 1. Find active tasks
    stmt = select(DocumentPPT).where(
        DocumentPPT.user_id == user_id,
        DocumentPPT.status.in_(["pending", "processing"])
    )
    result = await db.execute(stmt)
    tasks = result.scalars().all()
    
    count = 0
    for task in tasks:
        # Cancel session if running
        if task.session_id:
            session_manager.cancel_session(task.session_id)
        
        # Update DB status
        task.status = "failed"
        task.error_message = "User interrupted"
        count += 1
        
    if count > 0:
        await db.commit()
        
    return count


async def check_ppt_eligibility(
    db: AsyncSession,
    user_id: int,
    document_id: Optional[int]
) -> dict:
    """
    Check if user is eligible to generate PPT (已移除所有限制).
    """
    return {"allowed": True}

async def rename_ppt_task(
    db: AsyncSession,
    task_id: int,
    user_id: int,
    new_title: str
) -> Optional[DocumentPPT]:
    """
    Rename a PPT task (update its title).
    """
    stmt = select(DocumentPPT).where(DocumentPPT.id == task_id, DocumentPPT.user_id == user_id)
    result = await db.execute(stmt)
    ppt = result.scalars().first()
    
    if ppt:
        ppt.title = new_title
        await db.commit()
        await db.refresh(ppt)
        return ppt
    return None

async def generate_ppt_zip(db: AsyncSession, ppt_id: int, user_id: int) -> Optional[str]:
    """
    Generate a ZIP file containing all slides and a compiled PDF.
    Returns the path to the ZIP file.
    """
    # 1. Fetch PPT task
    ppt = await get_ppt_detail(db, ppt_id, user_id)
    if not ppt or not ppt.session_id:
        return None
        
    session_id = ppt.session_id
    
    # 2. Setup paths
    base_tmp_dir = data_path("tmp_downloads") / session_id
    zip_path = data_path("ppt_downloads") / f"{session_id}.zip"
    
    # Clean/Create dirs
    if base_tmp_dir.exists():
        shutil.rmtree(base_tmp_dir)
    base_tmp_dir.mkdir(parents=True, exist_ok=True)
    
    data_path("ppt_downloads").mkdir(parents=True, exist_ok=True)

    try:
        # 3. List and Download images from COS
        cos = COSService()
        prefix = f"ppt_output/{session_id}/"
        files = cos.list_files_with_prefix(prefix)
        
        if not files:
            logger.warning(f"No files found in COS for PPT {ppt_id}")
            return None
            
        slides_dir = base_tmp_dir / "slides"
        slides_dir.mkdir(exist_ok=True)
        
        downloaded_images = []
        for key in files:
            filename = Path(key).name
            if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                local_file = slides_dir / filename
                if cos.download_file(key, str(local_file)):
                    downloaded_images.append(local_file)
        
        if not downloaded_images:
            return None
            
        # Sort images by filename to ensure correct order
        downloaded_images.sort(key=lambda x: x.name)
        
        # 4. Generate PDF
        from PIL import Image
        pdf_path = base_tmp_dir / f"{ppt.title or 'presentation'}.pdf"
        
        pil_images = []
        for img_path in downloaded_images:
            try:
                img = Image.open(img_path)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                pil_images.append(img)
            except Exception as e:
                logger.error(f"Failed to load image {img_path} for PDF: {e}")
        
        if pil_images:
            pil_images[0].save(
                pdf_path, 
                "PDF", 
                resolution=100.0, 
                save_all=True, 
                append_images=pil_images[1:]
            )
            logger.info(f"Generated PDF at {pdf_path}")
            
        # 5. Create ZIP
        import zipfile
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # Add images
            for img_path in downloaded_images:
                zipf.write(img_path, arcname=f"slides/{img_path.name}")
            
            # Add PDF
            if pdf_path.exists():
                zipf.write(pdf_path, arcname=pdf_path.name)
                
        return str(zip_path)
        
    except Exception as e:
        logger.error(f"Failed to generate PPT zip package: {e}", exc_info=True)
        return None
    finally:
        # Cleanup temp files
        if base_tmp_dir.exists():
            shutil.rmtree(base_tmp_dir)
