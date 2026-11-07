
import logging
import json
import uuid
import os
import aiofiles
from pathlib import Path
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import BackgroundTasks
from db_models import DocumentPoster, Document, User
from database import AsyncSessionLocal
from config import settings
from .rag_queries import POSTER_RAG_QUERIES
from document_service.engines.paper2slides.core import run_pipeline, get_base_dir, get_config_dir, detect_start_stage
from document_service.engines.paper2slides.core.state import STAGES, create_state, save_state
from utils.cos_service import COSService
from runtime.paths import data_path
from runtime.credentials import credential_store

logger = logging.getLogger(__name__)

OUTPUT_DIR = data_path("poster_output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _cleanup_local_poster_images(config_dir: Path) -> int:
    """删除本地海报图片产物，保留 checkpoint/state 等轻量文件。"""
    if not config_dir.exists():
        return 0

    removed = 0
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp"}

    for file_path in config_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in image_suffixes:
            try:
                file_path.unlink()
                removed += 1
            except Exception as e:
                logger.warning(f"Failed to delete local poster image {file_path}: {e}")

    # 清理空目录（不删除 config_dir 本身）
    for dir_path in sorted([p for p in config_dir.rglob("*") if p.is_dir()], key=lambda x: len(x.parts), reverse=True):
        try:
            next(dir_path.iterdir())
        except StopIteration:
            try:
                dir_path.rmdir()
            except Exception:
                pass

    return removed

async def create_poster_task(
    db: AsyncSession,
    user_id: int,
    document_id: int,
    title: str,
    style: str,
    density: str
) -> DocumentPoster:
    """Create a new Poster generation task."""
    from runtime.features import require_capability
    require_capability("poster")
    
    # Check for existing document
    doc = await db.execute(select(Document).where(Document.id == document_id))
    doc = doc.scalar_one_or_none()
    
    # 1. Prepare Session
    session_id = str(uuid.uuid4())
    
    # 2. Check for Text Content (Reuse logic maybe?)
    # For now assume doc is parsed. If not, pipeline handles it.
    
    # 3. Create DB Record
    new_poster = DocumentPoster(
        document_id=document_id,
        user_id=user_id,
        title=title or "海报",  # 默认标题为"海报"
        style=style,
        status="pending",
        session_id=session_id,
        current_stage="msg_queue", 
    )
    db.add(new_poster)
    await db.commit()
    await db.refresh(new_poster)
    
    # 4. Trigger Background Task
    # We need to wrap it to inject the custom configuration for POSTER
    from fastapi import BackgroundTasks # Need to pass this in from API, or just use asyncio.create_task? 
    # API passes it? No, service.py usually doesn't take BackgroundTasks if not injected. 
    # Wait, api.py didn't pass BackgroundTasks to create_poster_task. I need to update api.py.
    # For now, let's use a workaround or fix api.py. 
    # Better: Fix api.py to inject BackgroundTasks.
    
    return new_poster

async def run_poster_pipeline(task_id: int) -> Optional[str]:
    """
    Background worker for Poster Generation.

    Returns:
        None 表示成功；流水线跑完但拿不到可访问海报时返回错误信息（记录已标记为 failed），
        由调用方（tasks/ppt_tasks.py）据此退款。流水线异常仍会抛出。
    """
    async with AsyncSessionLocal() as db:
        stmt = select(DocumentPoster).where(DocumentPoster.id == task_id)
        result = await db.execute(stmt)
        task = result.scalar_one_or_none()
        
        if not task:
            logger.error(f"Task {task_id} not found in background worker")
            return f"Poster task {task_id} not found"
            
        logger.info(f"🚀 Starting Poster Background Task {task_id} (Session {task.session_id})")

        try:
            # Update status
            task.status = "processing"
            task.current_stage = "initializing"
            await db.commit()
            
            # 1. Get Source Document
            doc_stmt = select(Document).where(Document.id == task.document_id)
            doc_res = await db.execute(doc_stmt)
            doc = doc_res.scalar_one_or_none()
            
            # Write tmp file (use document_id for stable naming, enables RAG/Summary checkpoint reuse)
            upload_dir = data_path("poster_uploads")
            upload_dir.mkdir(parents=True, exist_ok=True)
            safe_filename = f"doc_{task.document_id}.txt"
            input_path = upload_dir / safe_filename
            
            # Only write if file doesn't exist (allows reuse of RAG checkpoints)
            if not input_path.exists():
                async with aiofiles.open(input_path, 'w') as f:
                    await f.write(doc.original_text or "")
                logger.info(f"Created input file: {input_path}")
            else:
                logger.info(f"Reusing existing input file: {input_path}")

            # 2. Config & Paths
            session_id = task.session_id
            project_name = f"doc_{task.document_id}"
            
            # Injection
            from .prompts import PAPER_POSTER_PLANNING_PROMPT_CN, PAPER_POSTER_DENSITY_GUIDELINES_CN
            from document_service.engines.paper2slides.generator.config import StyleType

            # 校验样式：如果是预定义样式直接使用，否则转为 custom 并传入 custom_style
            style_value = task.style
            custom_style = None
            
            # 获取所有合法的 StyleType 值
            valid_styles = [s.value for s in StyleType]
            
            if style_value not in valid_styles:
                # 用户输入的自定义风格（如"赛博朋克"）
                custom_style = style_value
                style_value = "custom"  # 强制转为 valid enum value

            pipeline_config = {
                "input_path": str(input_path),
                "content_type": "paper",
                "output_type": "poster",  # rag_stage 会检查这个，使用独立的 chunked RAG
                "style": style_value,
                "custom_style": custom_style,
                "language": "zh",
                "poster_density": task.outline.get("density", "medium") if task.outline else "medium",
                "rag_queries": POSTER_RAG_QUERIES,
                "custom_planning_prompt": PAPER_POSTER_PLANNING_PROMPT_CN,
                "custom_density_guidelines": PAPER_POSTER_DENSITY_GUIDELINES_CN,
                # fast_mode=True 用于解析文档，rag_stage 会检查 output_type 使用独立的 poster_rag
                "fast_mode": True
            }
            # RAG 所用模型：两种部署统一走功能映射（与论文转 PPT 相同），不再写死 Kimi
            from llm_service import get_model_config
            from llm_service.llm_config import FEATURE_MODEL_MAP

            profile = get_model_config(FEATURE_MODEL_MAP["paper_pipeline"])
            pipeline_config.update({key: profile[key] for key in ("api_key", "base_url", "model")})
            
            base_dir = get_base_dir(str(OUTPUT_DIR), project_name, "paper")
            config_dir = get_config_dir(base_dir, pipeline_config)
            
            # 3. Initialize State
            from_stage = detect_start_stage(base_dir, config_dir, pipeline_config)
            
            initial_state = create_state(pipeline_config)
            initial_state["session_id"] = session_id
            # Save state (blocking)
            import  asyncio
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, save_state, config_dir, initial_state)

            # 4. Run Pipeline without callback
            await run_pipeline(
                base_dir, 
                config_dir, 
                pipeline_config, 
                from_stage=from_stage, 
                session_id=session_id
            )
            
            # 5. Success
            task.status = "completed"
            task.progress = 100
            
            # Delete input file on success
            try:
                if input_path.exists():
                     input_path.unlink()
                     logger.info(f"Deleted input file after success: {input_path}")
            except Exception as e:
                logger.warning(f"Failed to delete input file: {e}")
            
            # 图片已经被 generate_stage.py 上传到 COS 并删除了本地文件
            # 所以我们需要从 COS 获取已上传的图片 URL
            cos = COSService()
            
            # 先尝试从本地目录递归查找（生成产物通常在时间戳子目录）
            images = sorted([
                f for f in config_dir.rglob("*")
                if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            ])
            if images:
                # 本地有图片，上传到 COS
                cos_key = f"poster_output/{session_id}/{images[0].name}"
                url = cos.upload_file(str(images[0]), cos_key)
                if url:
                    task.poster_file_path = url
                    task.preview_image = url
                    logger.info(f"Poster uploaded from local: {url}")
            else:
                # 本地没有图片，从 COS 查找（generate_stage 已经上传了）
                logger.info(f"No local images found, scanning COS for session {session_id}...")

                # 优先从 state.json 读取生成阶段已记录的 COS URL，避免依赖 list_objects
                try:
                    state_file = config_dir / "state.json"
                    if state_file.exists():
                        async with aiofiles.open(state_file, 'r') as sf:
                            state_content = await sf.read()
                            state_data = json.loads(state_content)
                        generated_urls = state_data.get("generated_image_urls")
                        if isinstance(generated_urls, dict):
                            poster_url = generated_urls.get("0") or next(iter(generated_urls.values()), None)
                            if poster_url:
                                task.poster_file_path = poster_url
                                task.preview_image = poster_url
                                logger.info(f"Recovered poster URL from state: {poster_url}")
                except Exception as state_err:
                    logger.warning(f"Failed to recover poster URL from state: {state_err}")
                
                # 尝试两个路径：新路径 poster_output 和旧路径 ppt_output
                poster_url = task.poster_file_path
                if not poster_url:
                    for prefix_type in ["poster_output", "ppt_output"]:
                        prefix = f"{prefix_type}/{session_id}/"
                        try:
                            cos_files = cos.list_files_with_prefix(prefix, max_keys=10)
                            poster_files = [f for f in cos_files if 'poster' in f.lower()]
                            
                            if poster_files:
                                poster_url = cos.get_file_url(poster_files[0])
                                task.poster_file_path = poster_url
                                task.preview_image = poster_url
                                logger.info(f"Found poster in {prefix_type}: {poster_url}")
                                break
                        except Exception as cos_err:
                            logger.error(f"Failed to scan COS {prefix_type}: {cos_err}")
                
                if not poster_url:
                    logger.warning(f"No poster files found in COS for session {session_id}")

            # 若仍无可展示 URL，避免前端无限加载
            if not task.poster_file_path:
                # 最终兜底：在整个 base_dir 里找最新图片并上传 COS
                try:
                    fallback_images = sorted(
                        [
                            f for f in base_dir.rglob("*")
                            if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                        ],
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    )
                    if fallback_images:
                        latest_image = fallback_images[0]
                        fallback_key = f"poster_output/{session_id}/{latest_image.name}"
                        fallback_url = cos.upload_file(str(latest_image), fallback_key)
                        if fallback_url:
                            task.poster_file_path = fallback_url
                            task.preview_image = fallback_url
                            logger.info(f"Recovered poster URL from fallback local image: {fallback_url}")
                            removed = _cleanup_local_poster_images(config_dir)
                            if removed:
                                logger.info(f"Cleaned local poster images after fallback sync: {removed} files")
                except Exception as fallback_err:
                    logger.warning(f"Fallback local image upload failed for task {task_id}: {fallback_err}")

            if not task.poster_file_path:
                task.status = "failed"
                task.error_message = "海报已生成但未找到可访问图片链接，请重试"
                logger.warning(f"Poster task {task_id} missing poster URL after pipeline completion")
            else:
                removed = _cleanup_local_poster_images(config_dir)
                if removed:
                    logger.info(f"Cleaned local poster images after COS sync: {removed} files")
            
            await db.commit()
            # 失败要让调用方感知（以便退款），见函数文档
            return task.error_message if task.status == "failed" else None
            
        except Exception as e:
            logger.error(f"Poster generation failed: {e}", exc_info=True)
            task.status = "failed"
            task.error_message = str(e)
            await db.commit()
            raise

async def sync_poster_status(db: AsyncSession, task: DocumentPoster) -> DocumentPoster:
    """
    Sync logic to update DB status from pipeline state file.
    Critically needed because checking DB alone is not enough during execution.
    """
    # 如果已完成且有 poster_file_path，直接返回
    if task.status in ["completed", "failed"] and task.poster_file_path:
        return task
    
    # 如果已完成但没有 poster_file_path，尝试从 COS 恢复
    if task.status == "completed" and not task.poster_file_path and task.session_id:
        try:
            from utils.cos_service import COSService
            cos = COSService()

            # 先从 state.json 中恢复（生成阶段已记录上传 URL）
            try:
                session_id = task.session_id
                project_name = f"doc_{task.document_id}"
                base_dir = get_base_dir(str(OUTPUT_DIR), project_name, "paper")
                if base_dir.exists():
                    for content_mode in ["normal", "fast"]:
                        mode_dir = base_dir / content_mode
                        if not mode_dir.exists():
                            continue
                        for d in mode_dir.iterdir():
                            if not d.is_dir() or not (d / "state.json").exists():
                                continue
                            try:
                                async with aiofiles.open(d / "state.json", 'r') as sf:
                                    content = await sf.read()
                                state_data = json.loads(content)
                                if state_data.get("session_id") != session_id:
                                    continue
                                generated_urls = state_data.get("generated_image_urls")
                                if isinstance(generated_urls, dict):
                                    poster_url = generated_urls.get("0") or next(iter(generated_urls.values()), None)
                                    if poster_url:
                                        task.poster_file_path = poster_url
                                        task.preview_image = poster_url
                                        await db.commit()
                                        logger.info(f"Recovered poster URL from state: {poster_url}")
                                        return task
                            except Exception:
                                continue
            except Exception as state_err:
                logger.warning(f"State-based poster URL recovery failed: {state_err}")
            
            # 尝试两个路径：新路径 poster_output 和旧路径 ppt_output
            poster_url = None
            for prefix_type in ["poster_output", "ppt_output"]:
                prefix = f"{prefix_type}/{task.session_id}/"
                cos_files = cos.list_files_with_prefix(prefix, max_keys=10)
                poster_files = [f for f in cos_files if 'poster' in f.lower()]
                
                if poster_files:
                    poster_url = cos.get_file_url(poster_files[0])
                    logger.info(f"Found poster in {prefix_type}: {poster_url}")
                    break
            
            if poster_url:
                task.poster_file_path = poster_url
                task.preview_image = poster_url
                await db.commit()
                logger.info(f"Recovered poster URL from COS: {poster_url}")
        except Exception as e:
            logger.error(f"Failed to recover poster URL: {e}")
        return task
    
    if task.status == "failed":
        return task

    # REFRESH STATUS FIRST!
    # API might hold a stale "pending" object while Worker set it to "processing".
    # Without refresh, we might overwrite "processing" back to "pending" on commit.
    await db.refresh(task)

    if not task.session_id:
        return task
        
    try:
        session_id = task.session_id
        # Use document_id to find project dir
        project_name = f"doc_{task.document_id}"
        logger.info(f"Syncing status for Task {task.id} (Session {session_id}, Status: {task.status})")

        
        # Reconstruct Config to find Directory
        from .prompts import PAPER_POSTER_PLANNING_PROMPT_CN, PAPER_POSTER_DENSITY_GUIDELINES_CN
        from config import settings
        
        # Note: We need minimal config to match the directory hash
        # The key factors are usually style, output_type, etc.
        pipeline_config = {
            "content_type": "paper",
            "output_type": "poster", 
            "style": task.style,
            "language": "zh",
            "poster_density": task.outline.get("density", "medium") if task.outline else "medium", # might be empty initially
             # "custom_..." keys might affect hash?? Checked paper2slides/core/paths.py?
             # Usually get_config_dir hashes sorted keys.
             # If I passed custom prompts during run, I MUST pass them here too to match hash.
            "custom_planning_prompt": PAPER_POSTER_PLANNING_PROMPT_CN,
            "custom_density_guidelines": PAPER_POSTER_DENSITY_GUIDELINES_CN,
            "api_key": credential_store.api_key("openai") or settings.openai_api_key,
            "base_url": credential_store.base_url("openai") or settings.openai_base_url,
            "model": settings.openai_model,
            "fast_mode": False # Match run_poster_pipeline (使用完整RAG)
        }
        
        # We need input_path in config for hash? 
        # In run_poster_pipeline we set "input_path".
        # If hash depends on it, we are in trouble unless we know the filename.
        # Check get_config_name in core/paths.py usually ignores sensitive keys or maybe specific keys.
        # But safest is to try to find the directory.
        
        base_dir = get_base_dir(str(OUTPUT_DIR), project_name, "paper")
        
        # Search for state.json that matches THIS session_id
        state_file = None
        
        if base_dir.exists():
            for content_mode in ["normal", "fast"]: 
                mode_dir = base_dir / content_mode
                if mode_dir.exists():
                    for d in mode_dir.iterdir():
                        if d.is_dir() and (d / "state.json").exists():
                             # Check if this state belongs to our session
                             try:
                                 potential_state_file = d / "state.json"
                                 # We need to read it to check session_id
                                 # Blocking read is okay here for simplicity or use async
                                 async with aiofiles.open(potential_state_file, 'r') as f:
                                     content = await f.read()
                                     state = json.loads(content)
                                     
                                 if state.get("session_id") == session_id:
                                     state_file = potential_state_file
                                     break
                             except Exception:
                                 continue
                if state_file: break
        
        if state_file:
            async with aiofiles.open(state_file, 'r') as f:
                content = await f.read()
                state = json.loads(content)
            
            stages = state.get("stages", {})
            
            # --- SYNC PLAN ---
            if stages.get("plan") == "completed" and (not task.outline):
                 try:
                     config_dir = state_file.parent
                     plan_file = config_dir / "plan.json"
                     if plan_file.exists():
                         async with aiofiles.open(plan_file, 'r') as pf:
                             plan_content = await pf.read()
                             task.outline = json.loads(plan_content)
                 except Exception:
                     pass

            # Update status
            if any(s == "failed" for s in stages.values()):
                task.status = "failed"
                task.error_message = state.get("error", "Unknown error")
            elif all(s == "completed" for s in stages.values()):
                task.status = "completed"
                task.progress = 100
                # Try to find images if not set
                if not task.poster_file_path:
                    config_dir = state_file.parent
                    images = sorted([
                        f for f in config_dir.rglob("*")
                        if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                    ])
                    if images:
                        cos = COSService()
                        cos_key = f"poster_output/{session_id}/{images[0].name}"
                        # Check/Upload
                        url = cos.upload_file(str(images[0]), cos_key)
                        if url:
                            task.poster_file_path = url
                            task.preview_image = url
                            removed = _cleanup_local_poster_images(config_dir)
                            if removed:
                                logger.info(f"Cleaned local poster images during status sync: {removed} files")
            else:
                # Calculate Progress
                total_stages = len(STAGES)
                completed = sum(1 for s in stages.values() if s == "completed")
                
                # Base progress
                base_progress = int((completed / total_stages) * 100)
                # Cap at 99
                task.progress = min(99, max(20, base_progress))
                task.current_stage = next((k for k, v in stages.items() if v == "running"), "processing")

            await db.commit()
            await db.refresh(task)

    except Exception as e:
        logger.error(f"Sync status failed for task {task.id}: {e}")
        
    return task
