"""
来源文件处理任务

包含：
- 来源文件解析 + 向量化
- 成功后扣费（失败不扣费）
- 用户并发槽位回收
"""
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportGeneralTypeIssues=false, reportAttributeAccessIssue=false, reportUntypedFunctionDecorator=false
import asyncio
import os
import sys
import logging
import base64
from typing import Any, Optional, cast

# 确保 backend 目录在 Python 路径中
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery_app import celery_app
from tasks.base import AsyncTask
from tasks.utils import DOCUMENT_TASK_TIMEOUT_SECONDS, PARSE_TIMEOUT_SECONDS, get_db_session, log_stage

logger = logging.getLogger(__name__)

SOURCE_UPLOAD_ACTIVE_KEY_PREFIX = "source_upload:active:"
SOURCE_UPLOAD_ACTIVE_TTL = 3600
MAX_SOURCE_UPLOAD_CONCURRENCY_PER_USER = 2
SOURCE_SLOT_WAIT_SECONDS = 2
SOURCE_SLOT_MAX_WAIT_ROUNDS = 120
SOURCE_UPLOAD_PENDING_DOC_PREFIX = "source_upload:pending_doc:"


async def _acquire_user_source_slot(user_id: int) -> bool:
    """申请用户来源处理并发槽位（每用户同时处理中≤2）。"""
    try:
        from cache_service import get_redis_client
        client = await get_redis_client()
        if not client:
            return True
        key = f"{SOURCE_UPLOAD_ACTIVE_KEY_PREFIX}{user_id}"
        current = await client.incr(key)
        await client.expire(key, SOURCE_UPLOAD_ACTIVE_TTL)
        if current > MAX_SOURCE_UPLOAD_CONCURRENCY_PER_USER:
            await client.decr(key)
            return False
        return True
    except Exception as e:
        logger.warning(f"⚠️ [Source Slot] 申请并发槽位失败，降级放行: {e}")
        return True


async def _release_user_source_slot(user_id: int) -> None:
    """释放用户来源上传并发槽位。"""
    try:
        from cache_service import get_redis_client
        client = await get_redis_client()
        if not client:
            return
        key = f"{SOURCE_UPLOAD_ACTIVE_KEY_PREFIX}{user_id}"
        current = await client.decr(key)
        if current <= 0:
            await client.set(key, 0, ex=SOURCE_UPLOAD_ACTIVE_TTL)
        else:
            await client.expire(key, SOURCE_UPLOAD_ACTIVE_TTL)
    except Exception as e:
        logger.warning(f"⚠️ [Source Slot] 释放并发槽位失败: {e}")


async def _release_document_pending_slot(document_id: int) -> None:
    """释放文档来源上传排队名额。"""
    try:
        from cache_service import get_redis_client
        client = await get_redis_client()
        if not client:
            return
        key = f"{SOURCE_UPLOAD_PENDING_DOC_PREFIX}{document_id}"
        current = await client.decr(key)
        if current <= 0:
            await client.set(key, 0, ex=SOURCE_UPLOAD_ACTIVE_TTL)
        else:
            await client.expire(key, SOURCE_UPLOAD_ACTIVE_TTL)
    except Exception as e:
        logger.warning(f"⚠️ [Source Pending] 释放排队名额失败: {e}")


# type: ignore[misc]
# 与主文档一致：不自动重试，超时在任务内部收口
@celery_app.task(bind=True, base=AsyncTask, name="tasks.source.process_file", autoretry_for=())
def process_source_file(
    self: Any,
    document_id: int,
    user_id: int,
    filename: str,
    file_content_b64: str,
    source_name: Optional[str] = None,
) -> Any:
    """异步处理来源文件。"""

    async def _run():
        import asyncio
        from document_service import DocumentService
        from document_service.vector_store import vector_store_manager
        from db_models import Document, DocumentSource

        acquired_slot = False
        try:
            file_content = base64.b64decode(file_content_b64)
        except Exception as decode_err:
            raise ValueError(f"来源文件解码失败: {decode_err}")

        async with get_db_session() as db:
            try:
                for _ in range(SOURCE_SLOT_MAX_WAIT_ROUNDS):
                    acquired_slot = await _acquire_user_source_slot(user_id)
                    if acquired_slot:
                        break
                    await asyncio.sleep(SOURCE_SLOT_WAIT_SECONDS)

                if not acquired_slot:
                    raise RuntimeError("来源文件处理排队超时，请稍后重试")

                document = await db.get(Document, document_id)
                if not document or int(getattr(document, "user_id", 0)) != int(user_id):
                    raise ValueError("文档不存在或无权限")

                service = DocumentService(db=db)
                async with log_stage(f"解析来源 {filename}", f"文档 {document_id}"):
                    try:
                        structured_elements = await asyncio.wait_for(
                            service.extract_structured_elements_from_file(file_content, filename),
                            timeout=PARSE_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        raise ValueError(
                            f"来源文件解析超过 {PARSE_TIMEOUT_SECONDS} 秒仍未完成，可先转换为 PDF / DOCX 再上传"
                        ) from None
                text_content = "\n\n".join([elem.get("text", "") for elem in structured_elements if elem.get("text")]).replace("\x00", "").strip()
                if not text_content:
                    raise ValueError("文件解析结果为空，暂不支持该文件内容")

                source = DocumentSource(
                    document_id=document_id,
                    user_id=user_id,
                    source_type="file",
                    source_name=(source_name or filename or "上传文件").strip()[:255] or "上传文件",
                    source_url=None,
                    source_preview=text_content[:500],
                )
                db.add(source)
                await db.flush()

                from config import settings
                if settings.is_local_mode:
                    from runtime.storage import local_storage

                    await asyncio.to_thread(local_storage.save_source, user_id, source.id, text_content)
                # 同步的 Chroma 调用放到线程里，避免占住事件循环（阶段心跳与任务时限都依赖它）
                collection_id = await vector_store_manager._run_in_executor(
                    vector_store_manager.create_collection, document_id
                )
                async with log_stage(f"向量化来源 {filename}", f"文档 {document_id}"):
                    chunk_count = await vector_store_manager.add_documents(
                        collection_id=collection_id,
                        text=text_content,
                        metadata={
                            "document_id": str(document_id),
                            "source_id": f"source:{source.id}",
                            "source_name": source.source_name,
                            "source_type": source.source_type,
                            "source_url": "",
                        },
                        structured_elements=None,
                    )

                setattr(source, "vector_store_id", collection_id)
                setattr(source, "chunk_count", int(chunk_count or 0))
                await db.commit()
                await db.refresh(source)

                # 与主文档一致：成功后扣费；失败不扣费
                try:
                    from credit_service import CreditService, CreditCost
                    credit_service = CreditService(db)
                    await credit_service.deduct_credits(
                        user_id=user_id,
                        amount=CreditCost.DOCUMENT_UPLOAD,
                        transaction_type="source_upload",
                        description=f"来源文件上传解析: {getattr(source, 'source_name', '')}",
                        reference_id=f"source:{getattr(source, 'id', '')}",
                    )
                    await db.commit()
                    logger.info(f"💸 [Source Credit] 扣费 {CreditCost.DOCUMENT_UPLOAD} 积分, source_id={source.id}")
                except Exception as credit_err:
                    logger.warning(f"⚠️ [Source Credit] 扣费失败: {credit_err}")

                return cast(Any, {
                    "success": True,
                    "source_id": getattr(source, "id", None),
                    "source_name": getattr(source, "source_name", ""),
                    "chunk_count": getattr(source, "chunk_count", 0) or 0,
                })
            finally:
                if acquired_slot:
                    await _release_user_source_slot(user_id)
                await _release_document_pending_slot(document_id)

    return self.run_async(asyncio.wait_for(_run(), timeout=DOCUMENT_TASK_TIMEOUT_SECONDS))
