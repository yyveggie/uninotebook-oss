"""
文档管理路由

包含: 文档上传(文件/URL/文本)、极速上传、文档列表、重命名、删除、
      状态查询、内容获取、段落获取、学习路径生成、文档历史、touch
"""
import os
import tempfile
from pathlib import Path
import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Optional as Opt

from fastapi import APIRouter, HTTPException, Depends, Body, Form, UploadFile, File, Request, Response
from starlette.background import BackgroundTasks
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

import db_models
import auth
from database import get_db
from config import settings
from credit_service import CreditService, CreditCost
from utils.logger import log_info, log_error, get_logger
from document_service import DocumentService, get_document_service
from document_service.utils import update_document_last_accessed
from routes.error_codes import ErrorCode
from cache_service import (
    get_user_document_history,
    set_user_document_history,
    invalidate_user_document_history,
    get_document_data_cache,
    set_document_data_cache,
    invalidate_document_data,
    get_document_content_cache,
    set_document_content_cache,
    invalidate_document_content,
    get_document_list_cache,
    set_document_list_cache,
    invalidate_document_list_cache,
    invalidate_document_history,
    invalidate_chat_history_cache,
)
from routes._errors import raise_http_error
from routes._helpers import (
    build_structured_elements_from_text,
    apply_cache_headers,
    build_weak_etag,
    is_not_modified,
    latest_datetime,
)
from routes._idempotency import begin_idempotency, commit_idempotency, rollback_idempotency

logger = get_logger(__name__)

router = APIRouter()


async def _ensure_upload_credit_eligibility(db: AsyncSession, user_id: int) -> None:
    """上传前积分门槛检查（仅拦截，不在此处扣费）。"""
    credit_service = CreditService(db)
    balance = await credit_service.get_balance(user_id)
    required = CreditCost.DOCUMENT_UPLOAD
    if balance < required:
        raise HTTPException(
            status_code=402,
            detail=f"积分不足：上传文档至少需要 {required} 积分，当前余额 {balance} 积分"
        )


# 粘贴文本 / 文本来源的长度上限（字符数）
MAX_PASTE_TEXT_CHARS = 500_000
# 文档停留在 pending/processing 且无任何进展超过该时长，视为后台任务已丢失
# 文档任务硬时限为 10 分钟（celery task_time_limit），处理中超过 15 分钟无进展必然已中断；
# 排队中留更长余量，覆盖批量上传时的正常排队
STALE_PROCESSING_TIMEOUT = timedelta(minutes=15)
STALE_PENDING_TIMEOUT = timedelta(minutes=30)
# 文档完成后摘要仍在补写的最长窗口（摘要总时限 60 秒，留足余量）；超过即视为没有摘要
SUMMARY_PENDING_WINDOW = timedelta(minutes=5)
_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


async def read_upload_with_limit(file: UploadFile) -> bytes:
    """分块读取上传文件，超过 settings.max_upload_mb 立即 413，避免把超大文件整个读进内存。"""
    limit = settings.max_upload_mb * 1024 * 1024
    too_large = HTTPException(
        status_code=413,
        detail=f"文件过大：单个文件不能超过 {settings.max_upload_mb} MB，请压缩或拆分后重试",
    )
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > limit:
        raise too_large

    chunks = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def ensure_text_length(text: Opt[str], label: str = "文本") -> None:
    """粘贴文本长度上限检查（413）。"""
    if text and len(text) > MAX_PASTE_TEXT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"{label}过长：最多支持 {MAX_PASTE_TEXT_CHARS} 字，请拆分后分批添加，或以文件形式上传",
        )


def build_content_disposition(filename: Opt[str], disposition: str = "attachment") -> str:
    """
    生成 RFC 6266 / RFC 5987 兼容的 Content-Disposition。

    非 ASCII 文件名直接写进 header 会在 latin-1 编码时抛 UnicodeEncodeError（500），
    这里同时给出 ASCII 兜底名 filename= 与 UTF-8 百分号编码的 filename*=。
    """
    from urllib.parse import quote

    name = "".join(ch for ch in (filename or "") if ch >= " " and ch != "\x7f").strip() or "download"
    stem, ext = os.path.splitext(name)
    ascii_ext = "".join(ch for ch in ext if ch.isascii() and (ch.isalnum() or ch == "."))
    ascii_stem = "".join(ch for ch in stem if ch.isascii() and ch not in '"\\').strip()
    fallback = f"{ascii_stem or 'download'}{ascii_ext}"
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


async def _ensure_fetchable_url(url: str) -> None:
    """入队前做一次 SSRF 校验：内网/本机等地址直接 400（真正抓取时还会逐跳再校验）。"""
    from utils.safe_fetch import UnsafeURLError, validate_url

    try:
        await run_in_threadpool(validate_url, url)
    except UnsafeURLError as exc:
        raise_http_error(status_code=400, code=ErrorCode.DOCUMENT_400_MESSAGE, message=str(exc))


# ==================== 后台处理函数 ====================


async def _create_pending_url_document_and_enqueue(
    *,
    url: str,
    user_id: int,
    db: AsyncSession,
):
    import time

    timestamp = int(time.time())
    document = db_models.Document(
        user_id=user_id,
        filename=f"url_{timestamp}.md",
        display_name=url,
        file_type="url",
        original_text="",
        vector_store_id=f"doc_pending_{user_id}_{timestamp}",
        file_size=0,
        processing_status='pending',
        processing_progress=0,
    )

    db.add(document)
    await db.commit()
    await db.refresh(document)
    log_info(f"✅ URL 文档记录已创建: {document.id} -> {url}")

    from tasks.document_tasks import process_url

    task = process_url.delay(
        document_id=document.id,
        url=url,
        user_id=user_id,
    )

    await invalidate_document_list_cache(user_id)
    return document, str(task.id)


async def background_process_document(
    document_id: int,
    structured_elements: list,
    db_session_maker
):
    """
    后台任务：处理文档向量化
    """
    try:
        async with db_session_maker() as db:
            service = get_document_service(db)
            await service.processor.process_document_async(
                document_id=document_id,
                structured_elements=structured_elements
            )
            document = await db.get(db_models.Document, document_id)
            if document:
                await invalidate_document_data(document.user_id, document_id, names=["status"])
    except Exception as e:
        logger.error(f"❌ 后台处理失败: {e}")
        import traceback
        traceback.print_exc()


async def background_full_process_document(
    file_content: bytes,
    filename: str,
    file_type: str,
    user_id: int,
    db_session_maker,
    document_id: int = None  # 可选：已创建的文档ID
):
    """
    🚀 后台任务：完整处理文档（解析 + 向量化 + 摘要）
    """
    try:
        async with db_session_maker() as db:
            service = get_document_service(db)

            # 1. 解析文档
            logger.info(f"📄 [后台] 开始解析文档: {filename}")
            structured_elements = await service.extract_structured_elements_from_file(
                file_content, filename
            )
            text_content = "\n\n".join([elem['text'] for elem in structured_elements])
            # 🛡️ 全局清洗：去除 PostgreSQL 不支持的 NULL 字节
            if text_content:
                text_content = text_content.replace('\x00', '')

            # 2. 计算哈希
            from document_service.utils import calculate_file_hash
            file_hash = await run_in_threadpool(calculate_file_hash, text_content) if file_type != 'txt' else None

            # 3. 获取或创建文档记录
            if document_id:
                document = None
                for retry in range(3):
                    document = await db.get(db_models.Document, document_id)
                    if document:
                        break
                    logger.warning(f"⚠️ [后台] 未找到文档 {document_id}，重试 {retry + 1}/3...")
                    await asyncio.sleep(0.5)

                if not document:
                    logger.error(f"❌ [后台] 文档不存在: {document_id} (已重试3次)")
                    return

                logger.info(f"✅ [后台] 成功获取文档记录: ID={document.id}, 状态={document.processing_status}")

                # 更新文档内容
                document.original_text = text_content
                document.file_hash = file_hash
                document.processing_status = 'processing'
                document.processing_progress = 10
                await db.commit()
            else:
                document = await service.processor.create_document_record(
                    filename=filename,
                    content=text_content,
                    file_type=file_type,
                    user_id=user_id,
                    file_hash=file_hash
                )

            # 4. 处理向量化和摘要
            if document.processing_status in ('pending', 'processing'):
                await service.processor.process_document_async(
                    document_id=document.id,
                    structured_elements=structured_elements
                )

            # 5. 失效缓存
            await invalidate_document_data(user_id, document.id, names=["status"])
            await invalidate_document_list_cache(user_id)

            logger.info(f"✅ [后台] 文档处理完成: {filename}, ID={document.id}")

    except Exception as e:
        logger.error(f"❌ [后台] 完整处理失败: {e}")
        import traceback
        traceback.print_exc()
        if document_id:
            try:
                async with db_session_maker() as db:
                    document = await db.get(db_models.Document, document_id)
                    if document:
                        document.processing_status = 'failed'
                        document.processing_error = str(e)[:500]
                        await db.commit()
                        await invalidate_document_data(user_id, document_id, names=["status"])
            except Exception as update_err:
                logger.error(f"❌ [后台] 更新失败状态时出错: {update_err}")


async def background_process_url(
    url: str,
    user_id: int,
    db_session_maker,
    document_id: int
):
    """
    🚀 后台任务：处理 URL
    """
    try:
        try:
            raise ImportError("content-core not installed")
        except ImportError:
            import trafilatura

        async with db_session_maker() as db:
            logger.info(f"🌐 [后台] 开始抓取 URL: {url}")

            from utils.safe_fetch import fetch_html
            downloaded = await run_in_threadpool(fetch_html, url)

            if not downloaded:
                raise ValueError(
                    "🌱 抱歉，UniNotebook 暂时无法触达该网页所在的星球。\n\n"
                    "💡 建议：您可以直接复制网页正文内容，粘贴到输入框中进行分析。"
                )

            result = await run_in_threadpool(
                trafilatura.extract,
                downloaded,
                include_comments=False,
                include_tables=True,
                include_images=False,
                include_formatting=True,
                include_links=True,
                output_format="markdown"
            )

            if not result:
                raise ValueError("网页提取内容为空")

            metadata = await run_in_threadpool(trafilatura.extract_metadata, downloaded)
            title = metadata.title if metadata and metadata.title else "未命名网页"
            author = metadata.author if metadata and metadata.author else None

            logger.info(f"✅ [后台] URL 抓取成功: {title} ({len(result)} chars)")

            document = None
            for retry in range(3):
                document = await db.get(db_models.Document, document_id)
                if document:
                    break
                await asyncio.sleep(0.5)

            if not document:
                logger.error(f"❌ [后台] 文档不存在: {document_id}")
                return

            if document.filename.startswith("url_") and title != "未命名网页":
                safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
                if len(safe_title) > 50:
                    safe_title = safe_title[:50] + "..."
                if safe_title:
                    document.filename = f"{safe_title}.md"
                    document.display_name = title

            document.original_text = result
            document.processing_status = 'processing'
            document.processing_progress = 20

            from document_service.utils import calculate_file_hash
            file_hash = await run_in_threadpool(calculate_file_hash, result)
            document.file_hash = file_hash

            await db.commit()

            structured_elements = [{
                "text": result,
                "category": "NarrativeText",
                "type": "text",
                "metadata": {
                    "source": url,
                    "title": title,
                    "author": author,
                    "url": url
                },
                "element_id": "url_content_0",
                "position": 0.0
            }]

            service = get_document_service(db)
            await service.processor.process_document_async(
                document_id=document.id,
                structured_elements=structured_elements
            )

            await invalidate_document_list_cache(user_id)
            logger.info(f"✅ [后台] URL 文档处理完成: {url}")

    except Exception as e:
        logger.error(f"❌ [后台] URL 处理失败: {e}")
        try:
            async with db_session_maker() as db:
                if document_id:
                    doc = await db.get(db_models.Document, document_id)
                    if doc:
                        doc.processing_status = 'failed'
                        doc.processing_error = str(e)
                        await db.commit()
                        await invalidate_document_data(user_id, document_id, names=["status"])
        except:
            pass


# ==================== 文档上传 API ====================


@router.post("/api/document/upload-url", tags=["文档学习"])
async def upload_document_url(
    url: str = Body(..., embed=True),
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    上传 URL 并进行后台处理 (Celery + Trafilatura)
    """
    from runtime.features import require_capability
    require_capability("document_ingest")
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope="upload_url",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        if not url:
            raise_http_error(status_code=400, code=ErrorCode.DOCUMENT_400_MESSAGE, message="URL 不能为空")

        await _ensure_fetchable_url(url)
        await _ensure_upload_credit_eligibility(db, current_user.id)

        document, task_id = await _create_pending_url_document_and_enqueue(
            url=url,
            user_id=current_user.id,
            db=db,
        )

        response_payload = {
            "success": True,
            "message": "URL 已提交处理",
            "data": {
                "document_id": document.id,
                "task_id": task_id,
                "filename": document.filename,
                "display_name": document.display_name,
                "processing_status": "pending"
            }
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise


@router.post("/api/document/upload", tags=["文档学习"])
async def upload_document(
    file: Opt[UploadFile] = File(None),
    text: Opt[str] = Form(None),
    filename: Opt[str] = Form(None),
    team_id: Opt[int] = Form(None),  # 团队ID（可选，上传到团队）
    background_tasks: BackgroundTasks = None,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):

    """
    上传文档或粘贴文本（Celery 异步处理版本）
    
    ✨ 优化：立即返回文档ID，Celery Worker 后台处理向量化
    
    支持的文件格式：
    - PDF (.pdf)
    - Word (.docx, .doc)
    - Markdown (.md)
    - 纯文本 (.txt)
    - HTML (.html, .htm)
    - EPUB (.epub)
    - PowerPoint (.pptx, .ppt)
    - 直接粘贴文本
    """
    idempotency_ctx = None
    
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"upload_document:{team_id or 0}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        from runtime.features import require_capability
        require_capability("chat")
        if file or len(text or "") > 4000:
            require_capability("document_ingest")
        service = get_document_service(db)

        await _ensure_upload_credit_eligibility(db, current_user.id)
        
        # 支持的文件格式
        SUPPORTED_EXTENSIONS = {
            '.pdf', '.docx', '.doc', '.txt', '.md', 
            '.html', '.htm', '.epub', '.pptx', '.ppt'
        }
        
        # 处理文件上传
        if file:
            from runtime.features import require_document_parser
            require_document_parser(file.filename or "")
            # 检查文件格式
            file_ext = os.path.splitext(file.filename)[1].lower()
            if file_ext not in SUPPORTED_EXTENSIONS:
                supported_list = ', '.join(sorted(SUPPORTED_EXTENSIONS))
                raise HTTPException(
                    status_code=400, 
                    detail=f"不支持的文件格式 {file_ext}，支持的格式：{supported_list}"
                )
            
            # 读取文件内容（分块读取，超过 settings.max_upload_mb 直接 413）
            file_content = await read_upload_with_limit(file)
            detected_file_type = file_ext[1:] if file_ext else "unknown"
            
            # 🚀 优化：先创建文档记录，立即返回，解析+向量化全部后台执行
            # 这样可以避免 HTTP/2 PING 超时导致连接断开
            
            # 创建占位文档记录（状态为 pending）
            document_kwargs = {
                "user_id": current_user.id,
                "filename": file.filename,
                "file_type": detected_file_type,
                "original_text": "",  # 后台填充
                "vector_store_id": f"doc_pending_{current_user.id}_{int(datetime.utcnow().timestamp())}",
                "file_size": len(file_content),
                "file_hash": None,  # 后台计算
                "processing_status": 'pending',
                "processing_progress": 0,
            }

            # 兼容不同数据库模型版本：仅在 Document 定义了 team_id 时写入
            if team_id is not None and hasattr(db_models.Document, "team_id"):
                document_kwargs["team_id"] = team_id

            document = db_models.Document(**document_kwargs)
            db.add(document)

            await db.commit()
            await db.refresh(document)
            log_info(f"✅ 文档记录已创建（占位）: document_id={document.id}")
            
            # 🚀 使用 Celery 任务替代 BackgroundTasks
            # 性能优化：避免把整文件 base64 放入消息队列（大消息会显著拖慢）
            if settings.is_local_mode:
                from runtime.storage import local_storage

                original = await run_in_threadpool(
                    local_storage.save_document, current_user.id, document.id,
                    file.filename, file_content,
                )
                temp_file_path = str(original)
            else:
                tmp_dir = Path(tempfile.gettempdir()) / "uninotebook_uploads"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                suffix = Path(file.filename or "upload.bin").suffix or ".bin"
                with tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=suffix, delete=False) as tmp_file:
                    tmp_file.write(file_content)
                    temp_file_path = tmp_file.name

            from tasks.document_tasks import process_document_full
            process_document_full.delay(
                document_id=document.id,
                file_path=temp_file_path,
                filename=file.filename,
                file_type=detected_file_type,
                user_id=current_user.id
            )
            log_info(f"✅ Celery 任务已提交: document_id={document.id}")
            
            # 文件类型显示名称映射
            file_type_names = {
                'pdf': 'PDF', 'docx': 'Word', 'doc': 'Word',
                'txt': '文本', 'md': 'Markdown', 'html': 'HTML',
                'htm': 'HTML', 'epub': 'EPUB', 'pptx': 'PowerPoint',
                'ppt': 'PowerPoint'
            }
            file_type_display = file_type_names.get(detected_file_type, detected_file_type.upper())
            
            await invalidate_user_document_history(current_user.id)
            await invalidate_document_list_cache(current_user.id)  # 失效文档列表缓存
            
            response_payload = {
                "success": True,
                "message": f"{file_type_display}文档上传成功，正在后台处理...",
                "data": {
                    "document_id": document.id,
                    "filename": document.filename,
                    "display_name": document.display_name,  # 自定义显示名称
                    "chunk_count": document.chunk_count,
                    "file_size": document.file_size,
                    "file_type": document.file_type,
                    "text_length": len(document.original_text),
                    "summary": document.summary,  # 可能为空（后台生成中）
                    "processing_status": document.processing_status,  # 新增：处理状态
                    "processing_progress": document.processing_progress  # 新增：处理进度
                }
            }
            await set_document_data_cache(
                current_user.id,
                document.id,
                "status",
                {
                    "success": True,
                    "data": {
                        "document_id": document.id,
                        "filename": document.filename,
                        "processing_status": document.processing_status,
                        "processing_progress": document.processing_progress,
                        "processing_error": document.processing_error,
                        "summary": document.summary,
                        "chunk_count": document.chunk_count,
                        "created_at": document.created_at.isoformat() if document.created_at else None,
                    },
                },
                ttl=3,
            )
            return await commit_idempotency(idempotency_ctx, response_payload)
        
        # 处理文本粘贴 / 智能识别 URL
        elif text:
            ensure_text_length(text, "粘贴的文本")
            # 去掉首尾空白，用于判断是否是 URL
            raw_text = text.strip()
            is_single_line = "\n" not in raw_text and "\r" not in raw_text
            is_url_like = raw_text.startswith("http://") or raw_text.startswith("https://")

            # 🔍 如果是单行 URL，则走 URL 抓取逻辑，而不是当普通文本
            if is_single_line and is_url_like:
                log_info(f"🔗 检测到粘贴的是 URL，优先尝试网页抓取模式: {raw_text[:80]}...")
                await _ensure_fetchable_url(raw_text)

                try:
                    # 统一走 Celery 异步 URL 抓取，避免请求阻塞
                    document, task_id = await _create_pending_url_document_and_enqueue(
                        url=raw_text,
                        user_id=current_user.id,
                        db=db,
                    )

                    await invalidate_user_document_history(current_user.id)

                    response_payload = {
                        "success": True,
                        "message": "URL 已提交处理，正在后台抓取与解析...",
                        "data": {
                            "document_id": document.id,
                            "task_id": task_id,
                            "filename": document.filename,
                            "display_name": document.display_name,
                            "chunk_count": document.chunk_count,
                            "file_size": document.file_size,
                            "file_type": document.file_type,
                            "text_length": len(document.original_text or ""),
                            "summary": document.summary,
                            "processing_status": document.processing_status,
                            "processing_progress": document.processing_progress,
                        },
                    }

                    # 直接写入初始状态缓存
                    await set_document_data_cache(
                        current_user.id,
                        document.id,
                        "status",
                        {
                            "success": True,
                            "data": {
                                "document_id": document.id,
                                "filename": document.filename,
                                "processing_status": document.processing_status,
                                "processing_progress": document.processing_progress,
                                "processing_error": document.processing_error,
                                "summary": document.summary,
                                "chunk_count": document.chunk_count,
                                "created_at": document.created_at.isoformat() if document.created_at else None,
                            },
                        },
                        ttl=3,
                    )
                    return await commit_idempotency(idempotency_ctx, response_payload)
                except ValueError as ve:
                    # URL 抓取失败（内容过短或为空等），直接返回 400，不创建文档
                    log_info(f"⚠️ URL 抓取失败，不创建文档: {ve}")
                    raise_http_error(status_code=400, code=ErrorCode.DOCUMENT_400_EXCEPTION, message="请求失败", details=str(ve))

            # ⚪ 否则，按普通文本处理（保持原有逻辑）
            if not filename:
                filename = "粘贴的文本.txt"
            
            # 计算文件哈希
            from document_service.utils import calculate_file_hash
            file_hash = None
            structured_elements = await run_in_threadpool(build_structured_elements_from_text, text)
            
            # ⚡ 新：立即创建文档记录
            document = await service.processor.create_document_record(
                filename=filename,
                content=text,
                file_type="text",
                user_id=current_user.id,
                file_hash=file_hash
            )
            
            # ⚡ 添加后台任务（文本粘贴通常较短，也可以同步处理）
            if document.processing_status == 'pending':
                from database import get_async_session_maker
                background_tasks.add_task(
                    background_process_document,
                    document_id=document.id,
                    structured_elements=structured_elements,
                    db_session_maker=get_async_session_maker()
                )
                log_info(f"✅ 后台任务已添加: document_id={document.id}")
            
            await invalidate_user_document_history(current_user.id)
            await invalidate_document_list_cache(current_user.id)  # 失效文档列表缓存
            
            response_payload = {
                "success": True,
                "message": "文本上传成功，正在后台处理...",
                "data": {
                    "document_id": document.id,
                    "filename": document.filename,
                    "display_name": document.display_name,
                    "chunk_count": document.chunk_count,
                    "file_size": document.file_size,
                    "file_type": document.file_type,
                    "text_length": len(document.original_text),
                    "summary": document.summary,
                    "processing_status": document.processing_status,
                    "processing_progress": document.processing_progress
                }
            }
            await set_document_data_cache(
                current_user.id,
                document.id,
                "status",
                {
                    "success": True,
                    "data": {
                        "document_id": document.id,
                        "filename": document.filename,
                        "processing_status": document.processing_status,
                        "processing_progress": document.processing_progress,
                        "processing_error": document.processing_error,
                        "summary": document.summary,
                        "chunk_count": document.chunk_count,
                        "created_at": document.created_at.isoformat() if document.created_at else None,
                    },
                },
                ttl=3,
            )
            return await commit_idempotency(idempotency_ctx, response_payload)
        
        else:
            raise_http_error(
                status_code=400,
                code=ErrorCode.DOCUMENT_UPLOAD_INVALID_INPUT,
                message="请提供文件或文本内容",
            )
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception as e:
        await rollback_idempotency(idempotency_ctx)
        logger.error(f"❌ 上传失败: {e}")
        raise_http_error(
            status_code=500,
            code=ErrorCode.DOCUMENT_UPLOAD_INTERNAL_ERROR,
            message="上传失败",
            details=str(e),
        )


# ==================== 文档学习路径 ====================


@router.post("/api/document/{document_id}/generate-path", tags=["文档学习"])
async def generate_document_learning_path(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """基于文档生成学习路径并写入专用历史表。"""
    try:
        service = get_document_service(db)
        
        # 获取文档
        document = await service.get_document_by_id(document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.DOCUMENT_404_MESSAGE, message="文档不存在")
        
        # 🛡️ 权限检查 (IDOR修复)
        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.DOCUMENT_403_MESSAGE, message="无权访问此文档")
        
        # 生成学习路径
        learning_path = await service.generate_learning_path_from_document(
            document=document,
            user_id=current_user.id
        )
        
        version_query = await db.execute(
            select(func.coalesce(func.max(db_models.DocumentLearningPath.version), 0)).filter(
                db_models.DocumentLearningPath.document_id == document_id,
                db_models.DocumentLearningPath.user_id == current_user.id
            )
        )
        next_version = (version_query.scalar() or 0) + 1

        payload = learning_path.dict() if hasattr(learning_path, "dict") else learning_path
        title = "学习路径"
        if isinstance(payload, dict):
            title = payload.get("topic") or title
        record = db_models.DocumentLearningPath(
            document_id=document_id,
            user_id=current_user.id,
            title=title,
            learning_path_data=payload,
            node_count=len(payload.get("nodes", [])) if isinstance(payload, dict) else 0,
            version=next_version,
            is_current=True
        )
        db.add(record)
        await db.flush()
        await db.execute(
            update(db_models.DocumentLearningPath)
            .where(
                db_models.DocumentLearningPath.document_id == document_id,
                db_models.DocumentLearningPath.user_id == current_user.id,
                db_models.DocumentLearningPath.id != record.id
            )
            .values(is_current=False)
        )
        await db.commit()
        await db.refresh(record)

        # 更新文档最后访问时间

        await invalidate_document_history(current_user.id, document_id, names=["flowchart"])
        
        return {
            "success": True,
            "message": "学习路径生成成功",
            "data": learning_path,
            "learning_path_id": record.id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 生成学习路径失败: {e}")
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="生成失败", details=str(e))


# ==================== 文档状态/列表/CRUD ====================


@router.get("/api/document/{document_id}/status", tags=["文档学习"])
async def get_document_processing_status(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    查询文档处理状态
    
    Returns:
        processing_status: pending | processing | completed | failed
        processing_progress: 0-100
        processing_error: 错误信息（如果失败）
    """
    try:
        cached = await get_document_data_cache(current_user.id, document_id, "status")
        if cached:
            return cached

        # 查找文档
        result = await db.execute(
            select(db_models.Document).filter(
                db_models.Document.id == document_id,
                db_models.Document.user_id == current_user.id
            )
        )
        document = result.scalar_one_or_none()

        if not document:
            raise_http_error(status_code=404, code=ErrorCode.DOCUMENT_404_MESSAGE, message="文档不存在")

        # 🩹 SaaS：Celery worker 中途崩溃会让文档永远停在 pending/processing。
        # 行长时间没有任何更新（进度更新会刷新 last_accessed_at）则判定任务已丢失，标记失败。
        if settings.is_saas_mode and document.processing_status in ("pending", "processing"):
            last_progress_at = latest_datetime([document.created_at, document.last_accessed_at])
            stale_after = STALE_PENDING_TIMEOUT if document.processing_status == "pending" else STALE_PROCESSING_TIMEOUT
            if last_progress_at and datetime.now(timezone.utc) - last_progress_at > stale_after:
                logger.warning(f"⏰ 文档 {document.id} {document.processing_status} 超过 {stale_after} 无进展，标记为失败")
                document.processing_error = (
                    "排队超时：后台处理服务可能未运行，请稍后重新上传"
                    if document.processing_status == "pending"
                    else "处理超时：后台任务可能已中断，请重新上传"
                )
                document.processing_status = "failed"
                await db.commit()
                await invalidate_document_list_cache(current_user.id)

        # 文档已可用、摘要仍在补写（见 DocumentProcessor.process_document_async）：前端据此继续轮询
        updated_at = document.last_accessed_at
        if updated_at is not None and updated_at.tzinfo is None:
            # SQLite 不保存时区，存入的 CURRENT_TIMESTAMP 即 UTC
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        summary_pending = bool(
            document.processing_status == "completed"
            and not document.summary
            and updated_at
            and datetime.now(timezone.utc) - updated_at < SUMMARY_PENDING_WINDOW
        )

        response_payload = {
            "success": True,
            "data": {
                "document_id": document.id,
                "filename": document.filename,
                "display_name": document.display_name,
                "processing_status": document.processing_status,
                "processing_progress": document.processing_progress,
                "processing_error": document.processing_error,
                "summary": document.summary,
                "summary_pending": summary_pending,
                "chunk_count": document.chunk_count,
                "created_at": document.created_at.isoformat() if document.created_at else None
            }
        }
        if document.processing_status in ("completed", "failed") and not summary_pending:
            await set_document_data_cache(current_user.id, document.id, "status", response_payload, ttl=30)
        else:
            await invalidate_document_data(current_user.id, document.id, names=["status"])
        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 查询状态失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="查询失败", details=str(e))


@router.get("/api/document/list", tags=["文档学习"])
async def list_documents(
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    列出所有文档
    """
    try:
        # 尝试从缓存获取
        cached = await get_document_list_cache(current_user.id)
        if cached:
            return cached
        
        service = get_document_service(db)
        # 🛡️ 必须按当前用户过滤（否则会返回所有用户的文档，并写入当前用户的缓存）
        documents = await service.list_documents(user_id=current_user.id, limit=50)
        
        response = {
            "success": True,
            "data": [
                {
                    "id": doc.id,
                    "filename": doc.filename,
                    "display_name": doc.display_name,
                    "file_type": doc.file_type,
                    "chunk_count": doc.chunk_count,
                    "processing_status": doc.processing_status,
                    "processing_progress": doc.processing_progress,
                    "created_at": doc.created_at.isoformat() if doc.created_at else None
                }
                for doc in documents
            ]
        }
        
        # 写入缓存
        await set_document_list_cache(current_user.id, response)
        
        return response
    except Exception as e:
        logger.error(f"❌ 列表获取失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="获取失败", details=str(e))


@router.put("/api/document/{document_id}/rename", tags=["文档学习"])
async def rename_document(
    document_id: int,
    new_name: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    重命名文档（修改显示名称，不影响原始文件名）
    """
    try:
        # 查找文档
        result = await db.execute(
            select(db_models.Document).filter(
            db_models.Document.id == document_id,
            db_models.Document.user_id == current_user.id
        
            )
        )
        document = result.scalar_one_or_none()
        
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.DOCUMENT_404_MESSAGE, message="文档不存在")
        
        # 更新显示名称（不修改原始文件名）
        document.display_name = new_name
        await db.commit()
        
        logger.info(f"✅ 文档显示名称修改成功: {document_id} -> {new_name}")

        await invalidate_user_document_history(current_user.id)
        await invalidate_document_data(current_user.id, document_id, names=["status", "similar"])
        
        return {
            "success": True,
            "message": "重命名成功",
            "data": {
                "document_id": document.id,
                "display_name": document.display_name,
                "filename": document.filename
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 重命名失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="重命名失败", details=str(e))


@router.put("/api/document/{document_id}/rename-file", tags=["文档学习"])
async def rename_document_file(
    document_id: int,
    new_filename: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    修改文档原始文件名
    """
    try:
        # 查找文档
        result = await db.execute(
            select(db_models.Document).filter(
            db_models.Document.id == document_id,
            db_models.Document.user_id == current_user.id
        
            )
        )
        document = result.scalar_one_or_none()
        
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.DOCUMENT_404_MESSAGE, message="文档不存在")
        
        # 更新原始文件名
        document.filename = new_filename
        await db.commit()
        
        logger.info(f"✅ 文档文件名修改成功: {document_id} -> {new_filename}")

        await invalidate_user_document_history(current_user.id)
        
        return {
            "success": True,
            "message": "文件名修改成功",
            "data": {
                "document_id": document.id,
                "filename": document.filename
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 修改文件名失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="修改失败", details=str(e))


@router.get("/api/document/{document_id}/content", tags=["文档学习"])
async def get_document_content(
    document_id: int,
    limit: Opt[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档/网页的原始内容（用于段落批注等）。"""
    try:
        # 先查文档，判断类型后再决定是否使用缓存
        result = await db.execute(
            select(db_models.Document).filter(
                db_models.Document.id == document_id,
                db_models.Document.user_id == current_user.id,
            )
        )
        document = result.scalar_one_or_none()

        if not document:
            raise_http_error(status_code=404, code=ErrorCode.DOCUMENT_404_MESSAGE, message="文档不存在")

        content = document.original_text or ""

        # 只要 original_text 本身像 URL，就把它当作网页来源处理（兼容旧数据 file_type 仍为 text 的情况）
        text_is_url = content.strip().startswith("http://") or content.strip().startswith("https://")
        is_url_doc = (document.file_type or "").lower() == "url" or text_is_url

        # 普通文档仍然可以使用缓存（仅当 limit 为空时使用全量缓存）
        # URL 文档或文本形态 URL 每次实时抓取正文，避免缓存住一行 URL
        if not is_url_doc and limit is None:
            cached = await get_document_content_cache(current_user.id, document_id)
            if cached:
                return cached

        # 🆕 对 URL/文本型 URL 做兼容处理：如果 original_text 看起来只是一个 URL，
        # 尝试实时抓取网页正文，避免段落批注里只看到一行 URL。
        if text_is_url:
            try:
                import trafilatura
                from utils.safe_fetch import fetch_html

                downloaded = await run_in_threadpool(fetch_html, content.strip())
                if downloaded:
                    extracted = await run_in_threadpool(
                        trafilatura.extract,
                        downloaded,
                        include_comments=False,
                        include_tables=True,
                    )
                    if extracted and len(extracted.strip()) > 0:
                        content = extracted
            except Exception as te:
                logger.warning(f"⚠️ URL 正文抓取失败（content 接口降级为原始文本）: {te}")
        
        # 截断处理
        if limit is not None and content:
            content = content[:limit]

        response_payload = {
            "success": True,
            "data": {
                "content": content,
                "filename": document.filename,
                "file_type": document.file_type,
            },
        }

        # 只有全量请求才写入缓存
        if not is_url_doc and limit is None:
             await set_document_content_cache(current_user.id, document_id, response_payload)
             
        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取文档内容失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="获取失败", details=str(e))


@router.get("/api/document/{document_id}/paragraphs", tags=["文档学习"])
async def get_document_paragraphs(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    获取文档的段落列表（从向量数据库获取chunks）
    """
    try:
        # 查找文档
        result = await db.execute(
            select(db_models.Document).filter(
                db_models.Document.id == document_id,
                db_models.Document.user_id == current_user.id
            )
        )
        document = result.scalar_one_or_none()
        
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.DOCUMENT_404_MESSAGE, message="文档不存在")
        
        # 从ChromaDB获取所有chunks
        from document_service.vector_store import vector_store_manager
        
        try:
            def _read_all_chunks():
                collection = vector_store_manager.client.get_collection(document.vector_store_id)
                return collection.get(include=["documents", "metadatas"])

            # 获取所有文档（Chroma 同步调用放线程池，避免阻塞事件循环）
            all_data = await vector_store_manager._run_in_executor(_read_all_chunks)
            
            # 构建段落列表
            paragraphs = []
            for i, (doc_text, metadata) in enumerate(zip(all_data['documents'], all_data['metadatas'])):
                paragraphs.append({
                    'index': i,
                    'text': doc_text,
                    'category': metadata.get('category', 'unknown'),
                    'position': metadata.get('position', 0),
                    'length': len(doc_text)
                })
            
            # 按position排序（保持文档顺序）
            paragraphs.sort(key=lambda x: x['position'])
            
            logger.info(f"📄 获取文档段落: {len(paragraphs)} 个")
            
            return {
                "success": True,
                "data": {
                    "paragraphs": paragraphs,
                    "total": len(paragraphs)
                }
            }
            
        except Exception as e:
            logger.error(f"❌ 从向量库获取段落失败: {e}")
            # 如果失败，回退到按文本分割
            text = document.original_text
            # 简单按双换行分割
            paras = [p.strip() for p in text.split('\n\n') if len(p.strip()) > 100]
            
            return {
                "success": True,
                "data": {
                    "paragraphs": [
                        {"index": i, "text": p, "category": "text", "position": i, "length": len(p)}
                        for i, p in enumerate(paras)
                    ],
                    "total": len(paras)
                }
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取段落失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="获取失败", details=str(e))


@router.delete("/api/document/{document_id}", tags=["文档学习"])
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    删除文档（包含向量数据、学习记录等关联资源）
    """
    try:
        service = get_document_service(db)
        await service.delete_document(document_id=document_id, user_id=current_user.id)

        await invalidate_user_document_history(current_user.id)
        await invalidate_document_data(current_user.id, document_id)
        await invalidate_document_history(current_user.id, document_id)
        await invalidate_document_list_cache(current_user.id)  # 失效文档列表缓存
        await invalidate_chat_history_cache(current_user.id, document_id)  # 失效聊天历史缓存

        return {
            "success": True,
            "message": "文档已删除"
        }
    except ValueError:
        # 文档在 DB 中已不存在（可能是之前删除已 commit 但缓存未清理的"幽灵文档"）
        # 仍然执行缓存失效，确保前端不再看到此文档
        logger.info(f"📋 文档 {document_id} 在 DB 中不存在，清理残留缓存")
        await invalidate_user_document_history(current_user.id)
        await invalidate_document_data(current_user.id, document_id)
        await invalidate_document_history(current_user.id, document_id)
        await invalidate_document_list_cache(current_user.id)
        await invalidate_chat_history_cache(current_user.id, document_id)
        return {
            "success": True,
            "message": "文档已删除"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 删除失败: {e}")
        # 即使删除过程异常，也尝试清理缓存（文档可能已在 DB 层被删除）
        try:
            await invalidate_user_document_history(current_user.id)
            await invalidate_document_data(current_user.id, document_id)
            await invalidate_document_history(current_user.id, document_id)
            await invalidate_document_list_cache(current_user.id)
            await invalidate_chat_history_cache(current_user.id, document_id)
        except Exception:
            pass
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="删除失败", details=str(e))


# ==================== 历史记录 ====================


@router.post("/api/document/{document_id}/touch", tags=["文档管理"])
async def touch_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    更新文档的最后访问时间 (点击即更新)
    """
    await update_document_last_accessed(db, document_id)
    
    # 立即失效相关缓存，确保列表排序更新
    await invalidate_user_document_history(current_user.id)
    
    return {"success": True}


@router.get("/api/history/documents", tags=["历史记录"])
async def get_document_history(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
    limit: int = 20
):
    """
    获取用户的文档学习历史
    """
    try:
        cached_response = await get_user_document_history(current_user.id, limit)
        if cached_response:
            etag_cached = build_weak_etag(cached_response)
            cache_control = "private, no-cache"
            apply_cache_headers(response.headers, etag_cached, None, cache_control)
            if is_not_modified(request.headers, etag_cached, None):
                return Response(status_code=304, headers=dict(response.headers))
            return cached_response

        result = await db.execute(
            select(db_models.Document).filter(
                db_models.Document.user_id == current_user.id,
                # 🐛 [2026-02-05] 修复: 只显示已完成解析的文档
                # 未完成的文档 (pending/processing/failed) 不显示在历史列表中
                db_models.Document.processing_status == 'completed'
        ).order_by(
            db_models.Document.last_accessed_at.desc()
            ).limit(limit)
        )
        documents = result.scalars().all()
        
        response_payload = {
            "success": True,
            "data": [
                {
                    "document_id": d.id,
                    "filename": d.filename,
                    "display_name": d.display_name,
                    "file_type": d.file_type,
                    "file_size": d.file_size,
                    "summary": d.summary,
                    "text_length": len(d.original_text) if d.original_text else 0,
                    "chunk_count": d.chunk_count,
                    "created_at": d.created_at.isoformat(),
                    "last_accessed_at": d.last_accessed_at.isoformat()
                }
                for d in documents
            ]
        }

        last_modified = latest_datetime([
            *[getattr(d, "last_accessed_at", None) for d in documents],
            *[getattr(d, "created_at", None) for d in documents],
            getattr(current_user, "updated_at", None),
        ])
        etag = build_weak_etag(response_payload)
        cache_control = "private, no-cache"
        apply_cache_headers(response.headers, etag, last_modified, cache_control)

        if is_not_modified(request.headers, etag, last_modified):
            return Response(status_code=304, headers=dict(response.headers))

        await set_user_document_history(current_user.id, limit, response_payload, ttl=120)
        return response_payload
    except Exception as e:
        raise_http_error(status_code=500, code=ErrorCode.DOCUMENT_500_EXCEPTION, message="获取历史失败", details=str(e))


# ==================== 文件预览 ====================


@router.get("/api/document/{document_id}/file/info", tags=["文档学习"])
async def get_document_file_info(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """查询文档是否有可预览的原始文件。"""
    try:
        result = await db.execute(
            select(db_models.Document).filter(
                db_models.Document.id == document_id,
                db_models.Document.user_id == current_user.id,
            )
        )
        document = result.scalar_one_or_none()
    except Exception:
        # 迁移未执行导致列不存在时优雅降级
        return {"success": True, "data": {"available": False, "file_type": None, "file_size": None}}

    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")

    cos_key = getattr(document, "original_file_cos_key", None)
    if settings.is_local_mode:
        from runtime.storage import local_storage

        available = local_storage.original_path(document).is_file() and document.file_type == "pdf"
    else:
        available = bool(cos_key) and document.file_type == "pdf"
    return {
        "success": True,
        "data": {
            "available": available,
            "file_type": document.file_type,
            "file_size": document.file_size,
        },
    }


@router.get("/api/document/{document_id}/file/url", tags=["文档学习"])
async def get_document_file_url(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """返回后端代理 URL（避免 CORS 问题）。"""
    result = await db.execute(
        select(db_models.Document).filter(
            db_models.Document.id == document_id,
            db_models.Document.user_id == current_user.id,
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")

    cos_key = getattr(document, "original_file_cos_key", None)
    from runtime.storage import local_storage
    local_available = settings.is_local_mode and local_storage.original_path(document).is_file()
    if not cos_key and not local_available:
        raise HTTPException(status_code=404, detail="该文档无原始文件预览")

    proxy_url = f"/api/document/{document_id}/file/proxy"
    return {"success": True, "data": {"url": proxy_url, "expires_in": 0}}


@router.get("/api/document/{document_id}/file/proxy", tags=["文档学习"])
async def proxy_document_file(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """流式代理 COS 文件，避免前端直接跨域请求。"""
    from fastapi.responses import StreamingResponse
    import tempfile as _tempfile

    result = await db.execute(
        select(db_models.Document).filter(
            db_models.Document.id == document_id,
            db_models.Document.user_id == current_user.id,
        )
    )
    document = result.scalar_one_or_none()
    if not document:
        raise HTTPException(status_code=404, detail="文档不存在")

    cos_key = getattr(document, "original_file_cos_key", None)
    if settings.is_local_mode:
        from fastapi.responses import FileResponse
        from runtime.storage import local_storage

        path = local_storage.original_path(document)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="该文档无原始文件预览")
        return FileResponse(path, media_type="application/pdf", filename=document.filename,
                            content_disposition_type="inline")
    if not cos_key:
        raise HTTPException(status_code=404, detail="该文档无原始文件预览")

    from utils.cos_service import COSService
    cos = COSService()

    tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp_path = tmp.name
    tmp.close()

    ok = await run_in_threadpool(cos.download_file, cos_key, tmp_path)
    if not ok:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="无法从存储获取文件")

    def _iter_file():
        try:
            with open(tmp_path, "rb") as f:
                while chunk := f.read(256 * 1024):
                    yield chunk
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    return StreamingResponse(
        _iter_file(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": build_content_disposition(document.filename, "inline"),
            "Cache-Control": "private, max-age=3600",
        },
    )
