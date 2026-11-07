"""
文档处理任务

包含：
- 文档解析 + 向量化
- URL 抓取处理
"""
import asyncio
import os
import sys
import logging
from typing import Optional

# 确保 backend 目录在 Python 路径中
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery_app import celery_app
from tasks.base import AsyncTask, async_task
from tasks.utils import (
    COS_BACKUP_TIMEOUT_SECONDS,
    DOCUMENT_TASK_TIMEOUT_SECONDS,
    PARSE_TIMEOUT_SECONDS,
    TaskStatusUpdater,
    get_db_session,
    invalidate_caches,
    log_stage,
)

logger = logging.getLogger(__name__)


async def _parse_with_limits(service, file_content: bytes, filename: str, document_id: int):
    """解析文档：记录阶段耗时并限时，超时时说明最可能的原因"""
    from config import settings
    from document_service.parsers.parser_simple import SimpleParser

    suffix = os.path.splitext(filename)[1].lower()
    fallback = suffix not in SimpleParser.supported_extensions
    parser_desc = settings.document_parser_strategy + ("，该格式回退到 Unstructured" if fallback else "")
    try:
        async with log_stage(f"解析 {filename}（解析器 {parser_desc}）", f"文档 {document_id}"):
            return await asyncio.wait_for(
                service.extract_structured_elements_from_file(file_content, filename),
                timeout=PARSE_TIMEOUT_SECONDS,
            )
    except asyncio.TimeoutError:
        hint = (
            "该格式需由 Unstructured 解析，首次使用会联网下载语言数据，网络不通时会一直卡住；"
            "可先转换为 PDF / DOCX 再上传"
            if fallback or settings.document_parser_strategy != "simple"
            else "请检查文件是否异常（如超大扫描件）后重试"
        )
        raise ValueError(f"文档解析超过 {PARSE_TIMEOUT_SECONDS} 秒仍未完成：{hint}") from None


# 不自动重试：失败会被前端当作终态（并删除该文档），后台重跑只会与之冲突；
# 超时已在任务内部收口（见 DOCUMENT_TASK_TIMEOUT_SECONDS），重跑也只会再等一轮
@celery_app.task(bind=True, base=AsyncTask, name="tasks.document.process_full", autoretry_for=())
def process_document_full(
    self,
    document_id: int,
    filename: str,
    file_type: str,
    user_id: int,
    file_content_b64: Optional[str] = None,  # 兼容旧版：Base64 文件内容
    file_path: Optional[str] = None,         # 推荐：临时文件路径
):
    """
    完整文档处理任务：解析 + 向量化 + 摘要
    
    对应原 background_full_process_document 函数
    """
    import base64
    
    async def _run():
        _original_cos_key = None
        from config import settings

        if settings.is_local_mode:
            from runtime.storage import local_storage

            managed_path = local_storage.resolve(local_storage.document_key(user_id, document_id, filename))
            if managed_path.is_file():
                # A copied data directory can be opened at a new location.
                local_file_path = str(managed_path)
            else:
                local_file_path = file_path
        else:
            local_file_path = file_path

        # 读取文件内容（优先走文件路径，避免大消息队列开销）
        if local_file_path:
            with open(local_file_path, "rb") as f:
                file_content = f.read()

            # 在删除临时文件前，将原始 PDF 备份到 COS（用于前端预览）
            if file_type == "pdf" and not settings.is_local_mode:
                try:
                    from utils.cos_service import COSService
                    cos = COSService()
                    suffix = os.path.splitext(filename)[1] or ".pdf"
                    cos_key = f"documents/{user_id}/{document_id}/original{suffix}"
                    # 放到线程里并限时：同步上传会占住事件循环，网络卡住时整个任务都无法超时退出
                    async with log_stage("原始 PDF 备份到 COS", f"文档 {document_id}"):
                        _url, _err = await asyncio.wait_for(
                            asyncio.to_thread(cos.upload_file_verbose, file_path, cos_key),
                            timeout=COS_BACKUP_TIMEOUT_SECONDS,
                        )
                    if _err:
                        logger.warning(f"⚠️ [Celery] COS 原文备份失败: {_err}")
                    else:
                        _original_cos_key = cos_key
                        logger.info(f"✅ [Celery] 原始 PDF 已备份到 COS: {cos_key}")
                except asyncio.TimeoutError:
                    logger.warning(
                        f"⚠️ [Celery] COS 原文备份超过 {COS_BACKUP_TIMEOUT_SECONDS}s，已跳过（不影响解析，仅在线预览不可用）"
                    )
                except Exception as e:
                    logger.warning(f"⚠️ [Celery] COS 备份异常: {e}")

            if not settings.is_local_mode:
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        elif file_content_b64:
            file_content = base64.b64decode(file_content_b64)
        else:
            raise ValueError("Missing file payload: file_path or file_content_b64 is required")

        if settings.is_local_mode:
            from runtime.storage import local_storage

            original = local_storage.resolve(local_storage.document_key(user_id, document_id, filename))
            if not original.is_file():
                local_storage.save_document(user_id, document_id, filename, file_content)
        
        async with get_db_session() as db:
            from document_service import DocumentService
            from db_models import Document
            from starlette.concurrency import run_in_threadpool
            
            async with TaskStatusUpdater(db, Document, document_id, user_id) as updater:
                service = DocumentService(db=db)
                
                # 1. 解析文档
                await updater.update(progress=10, stage="parsing")
                logger.info(f"📄 [Celery] 开始解析文档: {filename}")
                structured_elements = await _parse_with_limits(service, file_content, filename, document_id)
                text_content = "\n\n".join([elem['text'] for elem in structured_elements])
                
                # 清洗 NULL 字节
                if text_content:
                    text_content = text_content.replace('\x00', '')
                
                # 2. 计算哈希
                await updater.update(progress=20, stage="hashing")
                from document_service.utils import calculate_file_hash
                file_hash = await run_in_threadpool(calculate_file_hash, text_content) if file_type != 'txt' else None
                
                # 3. 获取文档记录
                document = await db.get(Document, document_id)
                if not document:
                    raise ValueError(f"Document {document_id} not found")

                # 5. 更新文档内容
                await updater.update(progress=30, stage="saving_content")
                document.original_text = text_content
                document.file_hash = file_hash
                if _original_cos_key and hasattr(document, "original_file_cos_key"):
                    document.original_file_cos_key = _original_cos_key
                await db.commit()
                
                # 6. 生成摘要
                await updater.update(progress=40, stage="generating_summary")
                
                # 7. 向量化（与摘要并行执行）
                await updater.update(progress=50, stage="vectorizing")
                async with log_stage("向量化与摘要", f"文档 {document_id}"):
                    await service.processor.process_document_async(
                        document_id=document.id,
                        structured_elements=structured_elements
                    )
                # process_document_async 内部已将状态设为 completed/100，
                # 不再调用 updater.update(progress=85) 以避免状态回退
                
                # 8. 失效缓存
                await invalidate_caches(user_id, document_id, names=["status"])
                
                # 🆕 10. 文档上传积分扣费
                try:
                    from credit_service import CreditService, CreditCost
                    credit_service = CreditService(db)
                    await credit_service.deduct_for_document(user_id, document_id)
                    await db.commit()
                    logger.info(f"💸 [Document Credit] 扣费 {CreditCost.DOCUMENT_UPLOAD} 积分")
                except Exception as credit_err:
                    logger.warning(f"⚠️ [Document Credit] 扣费失败: {credit_err}")
                
                logger.info(f"✅ [Celery] 文档处理完成: {filename}, ID={document_id}")
                return {"document_id": document_id, "status": "completed"}
    
    return self.run_async(asyncio.wait_for(_run(), timeout=DOCUMENT_TASK_TIMEOUT_SECONDS))


# 不自动重试：失败会被前端当作终态（并删除该文档），后台重跑只会与之冲突；
# 超时已在任务内部收口（见 DOCUMENT_TASK_TIMEOUT_SECONDS），重跑也只会再等一轮
@celery_app.task(bind=True, base=AsyncTask, name="tasks.document.process_url", autoretry_for=())
def process_url(
    self,
    document_id: int,
    url: str,
    user_id: int
):
    """
    URL 抓取处理任务
    
    对应原 background_process_url 函数
    """
    async def _run():
        async with get_db_session() as db:
            from db_models import Document
            from document_service import DocumentService
            from starlette.concurrency import run_in_threadpool
            import trafilatura
            
            async with TaskStatusUpdater(db, Document, document_id, user_id) as updater:
                logger.info(f"🌐 [Celery] 开始抓取 URL: {url}")

                # 1. 下载网页（SSRF 防护：校验地址、逐跳校验重定向、限制大小与超时）
                await updater.update(progress=10, stage="fetching")
                from utils.safe_fetch import fetch_html
                async with log_stage(f"抓取网页 {url}", f"文档 {document_id}"):
                    downloaded = await run_in_threadpool(fetch_html, url)
                
                if not downloaded:
                    raise ValueError(
                        "🌱 抱歉，UniNotebook 暂时无法触达该网页所在的星球。\n\n"
                        "💡 建议：您可以直接复制网页正文内容，粘贴到输入框中进行分析。"
                    )
                
                # 2. 提取正文
                await updater.update(progress=30, stage="extracting")
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
                
                # 3. 提取元数据
                metadata = await run_in_threadpool(trafilatura.extract_metadata, downloaded)
                title = metadata.title if metadata and metadata.title else "未命名网页"
                author = metadata.author if metadata and metadata.author else None
                
                logger.info(f"✅ [Celery] URL 抓取成功: {title} ({len(result)} chars)")
                
                # 4. 更新文档记录
                await updater.update(progress=50, stage="updating")
                document = await db.get(Document, document_id)
                if not document:
                    raise ValueError(f"Document {document_id} not found")
                
                # 更新文件名
                if document.filename.startswith("url_") and title != "未命名网页":
                    safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
                    if len(safe_title) > 50:
                        safe_title = safe_title[:50] + "..."
                    if safe_title:
                        document.filename = f"{safe_title}.md"
                        document.display_name = title
                
                document.original_text = result
                
                # 计算哈希
                from document_service.utils import calculate_file_hash
                file_hash = await run_in_threadpool(calculate_file_hash, result)
                document.file_hash = file_hash
                await db.commit()
                
                # 5. 构造结构化元素
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
                
                # 6. 向量化
                await updater.update(progress=70, stage="vectorizing")
                service = DocumentService(db=db)
                async with log_stage("向量化与摘要", f"文档 {document_id}"):
                    await service.processor.process_document_async(
                        document_id=document.id,
                        structured_elements=structured_elements
                    )
                # process_document_async 内部已将状态设为 completed/100
                
                # 7. 失效缓存
                await invalidate_caches(user_id, document_id, names=["status"])
                
                # 🆕 8. 文档上传积分扣费
                try:
                    from credit_service import CreditService, CreditCost
                    credit_service = CreditService(db)
                    await credit_service.deduct_for_document(user_id, document_id)
                    await db.commit()
                    logger.info(f"💸 [URL Credit] 扣费 {CreditCost.DOCUMENT_UPLOAD} 积分")
                except Exception as credit_err:
                    logger.warning(f"⚠️ [URL Credit] 扣费失败: {credit_err}")
                
                logger.info(f"✅ [Celery] URL 文档处理完成: {url}")
                return {"document_id": document_id, "status": "completed", "title": title}
    
    return self.run_async(asyncio.wait_for(_run(), timeout=DOCUMENT_TASK_TIMEOUT_SECONDS))


@celery_app.task(bind=True, base=AsyncTask, name="tasks.document.reindex")
def reindex_document(self, document_id: int, user_id: int):
    """Build replacement collections before swapping references; retain old data on failure."""
    async def _run():
        from config import settings
        from sqlalchemy import select
        from db_models import Document, DocumentSource
        from document_service.vector_store import vector_store_manager as vectors
        from runtime.storage import local_storage
        from runtime.tasks import check_cancelled

        async with get_db_session() as db:
            document = await db.get(Document, document_id)
            if not document or document.user_id != user_id:
                raise ValueError("文档不存在")
            sources = (await db.execute(select(DocumentSource).where(
                DocumentSource.document_id == document_id, DocumentSource.user_id == user_id,
            ))).scalars().all()
            created, obsolete = [], []
            committed = False
            try:
                for index, record in enumerate([document, *sources]):
                    check_cancelled()
                    is_main = isinstance(record, Document)
                    content = document.original_text if is_main else local_storage.source_text(user_id, record.id)
                    if not content and record.vector_store_id:
                        def read_old():
                            collection = vectors.client.get_collection(record.vector_store_id)
                            data = collection.get(include=["documents", "metadatas"])
                            parts = sorted(zip(data["documents"], data["metadatas"]),
                                           key=lambda item: (item[1] or {}).get("chunk_index", 0))
                            return "\n\n".join(text for text, _ in parts)
                        content = await vectors._run_in_executor(read_old)
                    if not content:
                        raise ValueError("缺少完整原文，请重新上传该文档或来源")
                    if not is_main and settings.is_local_mode:
                        local_storage.save_source(user_id, record.id, content)
                    collection_id = vectors.create_collection(document_id)
                    created.append(collection_id)
                    count = await vectors.add_documents(collection_id, content, metadata={
                        "document_id": str(document_id),
                        "source_id": f"document:{document_id}" if is_main else f"source:{record.id}",
                        "source_name": document.filename if is_main else record.source_name,
                    })
                    obsolete.append(record.vector_store_id)
                    record.vector_store_id, record.chunk_count = collection_id, count
                    self.update_state(state="PROGRESS", meta={
                        "progress": round((index + 1) / (len(sources) + 1) * 95), "stage": "reindexing",
                    })
                document.processing_status, document.processing_progress = "completed", 100
                document.processing_error = None
                await db.commit()
                committed = True
            finally:
                if not committed:
                    await db.rollback()
                    for collection_id in created:
                        vectors.delete_collection(collection_id)
            # Collections may be shared by duplicate documents. Delete only unreferenced ones.
            for collection_id in obsolete:
                if not collection_id:
                    continue
                refs = await db.execute(select(Document.id).where(Document.vector_store_id == collection_id))
                source_refs = await db.execute(select(DocumentSource.id).where(DocumentSource.vector_store_id == collection_id))
                if refs.first() is None and source_refs.first() is None:
                    vectors.delete_collection(collection_id)
            await invalidate_caches(user_id, document_id, names=["status"])
            return {"success": True, "document_id": document_id}
    return self.run_async(_run())
