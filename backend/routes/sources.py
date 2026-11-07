"""
文档来源管理路由

支持：
- 列出来源
- 添加来源（文本或网页）
- 删除来源（并删除对应向量块）
"""
# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
import time
import uuid
import base64
import json
import asyncio
from typing import Optional, Literal, Any, cast

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
import trafilatura
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

import auth
import db_models
from database import get_db
from config import settings
from document_service import get_document_service
from document_service.vector_store import vector_store_manager
from cache_service import get_redis_client, get_json, set_json, is_cache_available
from credit_service import CreditService, InsufficientCreditsError
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from routes.document import read_upload_with_limit, ensure_text_length
from utils.safe_fetch import fetch_html, UnsafeURLError
from utils.logger import get_logger


logger = get_logger(__name__)
router = APIRouter()
MAX_FILES_PER_CONVERSATION_UPLOAD = 5
SOURCE_UPLOAD_PENDING_DOC_PREFIX = "source_upload:pending_doc:"


def _is_primary_document_counted_as_file(document: db_models.Document) -> bool:
    file_type = str(getattr(document, "file_type", "") or "").strip().lower()
    # URL 文档不计入“文件来源数量”上限，其余主文档视为占用 1 个文件名额
    return file_type != "url"


class AddSourceRequest(BaseModel):
    source_type: Literal["text", "web", "web_research", "file"] = "text"
    source_name: str = Field(..., min_length=1, max_length=255)
    content: str = Field(..., min_length=1)
    source_url: Optional[str] = Field(None, max_length=1000)


class AddSourceUrlRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=2000)
    source_name: Optional[str] = Field(None, max_length=255)


class DeepResearchImportRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    source_name: Optional[str] = Field(None, max_length=255)


class DeepResearchPreviewRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class DeepResearchBatchImportRequest(BaseModel):
    draft_id: str = Field(..., min_length=1, max_length=100)
    selected_reference_ids: list[str] = Field(default_factory=list)


class UpdateSourceRequest(BaseModel):
    source_name: Optional[str] = Field(None, max_length=255)
    content: Optional[str] = Field(None)


class SourceTaskBatchRequest(BaseModel):
    task_ids: list[str] = Field(default_factory=list)


SOURCE_TASK_BATCH_MAX_SIZE = 50
SOURCE_TASK_STREAM_INTERVAL_SECONDS = 1.5
SOURCE_TASK_STREAM_TIMEOUT_SECONDS = 180


DEEP_RESEARCH_DRAFT_TTL_SECONDS = 20 * 60
# 候选来源草稿优先存 Redis（多 worker 共享）；缓存不可用（如本地版无 Redis）时退回进程内字典
_DEEP_RESEARCH_DRAFT_CACHE: dict[str, dict[str, Any]] = {}
DEEP_RESEARCH_DRAFT_KEY_PREFIX = "deep_research:draft:"


def _build_source_task_payload(task_id: str) -> dict[str, Any]:
    from config import settings

    status_mapping = {
        "PENDING": "pending",
        "STARTED": "processing",
        "PROGRESS": "processing",
        "SUCCESS": "completed",
        "FAILURE": "failed",
        "REVOKED": "failed",
        "INTERRUPTED": "failed",
        "RETRY": "processing",
    }

    if settings.is_local_mode:
        from runtime.tasks import get_local_task_result
        local_res = get_local_task_result(task_id)
        if local_res is not None:
            status = status_mapping.get(local_res.status, "pending")
            payload: dict[str, Any] = {
                "task_id": task_id,
                "status": status,
            }
            if local_res.ready():
                if local_res.successful():
                    payload["result"] = local_res.result
                else:
                    payload["error"] = str(local_res.result)
            return payload

        return {"task_id": task_id, "status": "failed", "error": "本地任务不存在"}

    from celery.result import AsyncResult
    from celery_app import celery_app

    result = AsyncResult(task_id, app=celery_app)
    status = status_mapping.get(result.status, "pending")

    payload: dict[str, Any] = {
        "task_id": task_id,
        "status": status,
    }
    if result.ready():
        if result.successful():
            payload["result"] = result.result
        else:
            payload["error"] = str(result.result)
    return payload


def _build_source_task_batch_payload(task_ids: list[str]) -> tuple[list[dict[str, Any]], bool]:
    tasks = [_build_source_task_payload(task_id) for task_id in task_ids]
    all_done = all(task.get("status") in {"completed", "failed"} for task in tasks)
    return tasks, all_done


def _cleanup_deep_research_drafts() -> None:
    now = time.time()
    expired_keys = [
        key
        for key, value in _DEEP_RESEARCH_DRAFT_CACHE.items()
        if now - float(value.get("created_at", 0)) > DEEP_RESEARCH_DRAFT_TTL_SECONDS
    ]
    for key in expired_keys:
        _DEEP_RESEARCH_DRAFT_CACHE.pop(key, None)


async def _save_deep_research_draft(draft_id: str, draft: dict[str, Any]) -> None:
    _cleanup_deep_research_drafts()
    if await is_cache_available():
        await set_json(f"{DEEP_RESEARCH_DRAFT_KEY_PREFIX}{draft_id}", draft, ttl=DEEP_RESEARCH_DRAFT_TTL_SECONDS)
    else:
        _DEEP_RESEARCH_DRAFT_CACHE[draft_id] = draft


async def _load_deep_research_draft(draft_id: str) -> Optional[dict[str, Any]]:
    _cleanup_deep_research_drafts()
    draft = await get_json(f"{DEEP_RESEARCH_DRAFT_KEY_PREFIX}{draft_id}")
    if isinstance(draft, dict):
        return draft
    return _DEEP_RESEARCH_DRAFT_CACHE.get(draft_id)


def _extract_reference_candidates(references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for idx, ref in enumerate(references, start=1):
        item = ref or {}
        title = str(item.get("title") or f"网页来源 {idx}").strip()
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        snippet = str(item.get("snippet") or item.get("summary") or "").strip()
        candidates.append(
            {
                "id": f"ref_{idx}",
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )
    return candidates


def _extract_website_candidates(web_sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for idx, site in enumerate(web_sites, start=1):
        item = site or {}
        title = str(item.get("title") or f"网页来源 {idx}").strip()
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        snippet = str(item.get("description") or item.get("snippet") or "").strip()
        candidates.append(
            {
                "id": f"site_{idx}",
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )
    return candidates


def _merge_candidates(*candidate_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for group in candidate_groups:
        for item in group:
            if not isinstance(item, dict):
                continue
            raw_url = str(item.get("url") or "").strip()
            if not raw_url:
                continue
            key = raw_url.lower().rstrip("/")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            merged.append(item)
    return merged


def _fetch_webpage_text(url: str) -> str:
    try:
        downloaded = fetch_html(url)
    except UnsafeURLError as exc:
        logger.warning(f"⚠️ 已拒绝抓取不安全的网址: {url} ({exc})")
        return ""
    if not downloaded:
        return ""
    extracted = trafilatura.extract(
        downloaded,
        include_comments=False,
        include_tables=False,
        output_format="txt",
        favor_precision=True,
    )
    return (extracted or "").strip()


def _is_url_only_content(content: str, source_url: Optional[str]) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    normalized = text.lower()
    source_url_text = (source_url or "").strip().lower()
    if source_url_text and normalized == source_url_text:
        return True
    if normalized.startswith("来源链接："):
        tail = normalized.replace("来源链接：", "", 1).strip()
        if not tail:
            return True
        if source_url_text and tail == source_url_text:
            return True
        if tail.startswith("http://") or tail.startswith("https://"):
            return True
    return len(text) < 80 and ("http://" in normalized or "https://" in normalized)


async def _save_source_with_vector(
    db: AsyncSession,
    document: db_models.Document,
    user_id: int,
    *,
    source_type: str,
    source_name: str,
    content: str,
    source_url: Optional[str] = None,
) -> db_models.DocumentSource:
    from runtime.features import require_capability
    require_capability("document_ingest")
    document_id = int(cast(Any, document.id))
    source = db_models.DocumentSource(
        document_id=document_id,
        user_id=user_id,
        source_type=source_type,
        source_name=source_name.strip(),
        source_url=source_url,
        source_preview=content[:500],
    )
    db.add(source)
    await db.flush()

    if settings.is_local_mode:
        from runtime.storage import local_storage

        await asyncio.to_thread(local_storage.save_source, user_id, source.id, content)
    # Chroma 同步调用放线程池，避免阻塞事件循环
    collection_id = await vector_store_manager._run_in_executor(vector_store_manager.create_collection, document_id)
    chunk_count = await vector_store_manager.add_documents(
        collection_id=collection_id,
        text=content,
        metadata={
            "document_id": str(document.id),
            "source_id": f"source:{source.id}",
            "source_name": source.source_name,
            "source_type": source.source_type,
            "source_url": source.source_url or "",
        },
        structured_elements=None,
    )

    source.vector_store_id = collection_id
    source.chunk_count = chunk_count
    await db.commit()
    await db.refresh(source)
    return source


async def _run_deep_research(question: str) -> dict[str, Any]:
    from runtime.features import require_capability
    from runtime.credentials import credential_store

    require_capability("deep_research")
    import dashscope  # type: ignore[import-untyped]
    from config import settings

    research_key = credential_store.api_key("dashscope_native")
    if not research_key:
        raise_http_error(status_code=500, code=ErrorCode.SOURCES_500_MESSAGE, message="未配置 DASHSCOPE_API_KEY")

    def _call_once(messages: list[dict[str, str]], step_name: str) -> dict[str, Any]:
        responses = dashscope.Generation.call(
            api_key=research_key,
            model="qwen-deep-research",
            messages=cast(Any, messages),
            stream=True,
        )

        content_parts: list[str] = []
        references: list[dict[str, Any]] = []
        web_sites: list[dict[str, Any]] = []
        phases: list[str] = []
        statuses: list[str] = []
        errors: list[str] = []

        for response in responses:
            if hasattr(response, "status_code") and getattr(response, "status_code", 200) != 200:
                code = str(getattr(response, "code", ""))
                message = str(getattr(response, "message", "Deep Research 调用失败"))
                errors.append(f"{code}:{message}" if code else message)
                continue

            output = getattr(response, "output", None)
            if not output:
                continue

            message = output.get("message", {}) if isinstance(output, dict) else {}
            phase = str(message.get("phase") or "")
            status = str(message.get("status") or "")
            if phase:
                phases.append(phase)
            if status:
                statuses.append(status)

            text = str(message.get("content") or "")
            if text:
                content_parts.append(text)

            extra = message.get("extra", {}) if isinstance(message, dict) else {}
            deep_info = extra.get("deep_research", {}) if isinstance(extra, dict) else {}

            refs = deep_info.get("references") if isinstance(deep_info, dict) else None
            if isinstance(refs, list) and refs:
                references = refs

            research = deep_info.get("research") if isinstance(deep_info, dict) else None
            if isinstance(research, dict):
                sites = research.get("webSites")
                if isinstance(sites, list) and sites:
                    web_sites = sites

        final_content = "".join(content_parts).strip()
        return {
            "content": final_content,
            "references": references,
            "web_sites": web_sites,
            "debug": {
                "step": step_name,
                "phases": sorted(set([p for p in phases if p])),
                "statuses": sorted(set([s for s in statuses if s])),
                "references_count": len(references),
                "web_sites_count": len(web_sites),
                "content_length": len(final_content),
                "errors": errors,
            },
        }

    step1_messages = [{"role": "user", "content": question}]
    step1 = _call_once(step1_messages, "step1_confirm")

    step2_messages = [
        {"role": "user", "content": question},
        {"role": "assistant", "content": step1.get("content") or ""},
        {
            "role": "user",
            "content": "请直接执行深入研究，并返回可验证的网页来源（包含标题与URL）。",
        },
    ]
    step2 = _call_once(step2_messages, "step2_research")

    final_content = str(step2.get("content") or step1.get("content") or "").strip()
    if not final_content:
        raise_http_error(status_code=502, code=ErrorCode.SOURCES_502_MESSAGE, message="Deep Research 返回内容为空")

    references = step2.get("references") or step1.get("references") or []
    web_sites = step2.get("web_sites") or step1.get("web_sites") or []

    reference_text = ""
    if references:
        lines = ["\n\n参考来源："]
        for idx, ref in enumerate(references, start=1):
            title = (ref or {}).get("title") or "无标题"
            url = (ref or {}).get("url") or ""
            lines.append(f"{idx}. {title} {url}".strip())
        reference_text = "\n" + "\n".join(lines)

    return {
        "content": final_content + reference_text,
        "references": references,
        "web_sites": web_sites,
        "debug": {
            "step1": step1.get("debug", {}),
            "step2": step2.get("debug", {}),
        },
    }


@router.get("/api/document/{document_id}/sources", tags=["文档来源"])
async def list_document_sources(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    result = await db.execute(
        select(db_models.DocumentSource)
        .where(
            db_models.DocumentSource.document_id == document_id,
            db_models.DocumentSource.user_id == current_user.id,
        )
        .order_by(db_models.DocumentSource.created_at.desc())
    )
    items = result.scalars().all()

    return {
        "success": True,
        "data": [
            {
                "id": item.id,
                "source_id": f"source:{item.id}",
                "source_type": item.source_type,
                "source_name": item.source_name,
                "source_url": item.source_url,
                "source_preview": item.source_preview,
                "chunk_count": item.chunk_count or 0,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in items
        ],
    }


@router.post("/api/document/{document_id}/sources", tags=["文档来源"])
async def add_document_source(
    document_id: int,
    body: AddSourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    if not document.vector_store_id or document.vector_store_id.startswith("text_direct_"):
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="当前文档未完成向量化，暂不可添加来源")

    ensure_text_length(body.content, "来源内容")
    source = await _save_source_with_vector(
        db,
        document,
        int(cast(Any, current_user.id)),
        source_type=body.source_type,
        source_name=body.source_name,
        content=body.content,
        source_url=body.source_url,
    )

    source_id = f"source:{source.id}"

    return {
        "success": True,
        "message": "来源添加成功",
        "data": {
            "id": source.id,
            "source_id": source_id,
            "source_type": source.source_type,
            "source_name": source.source_name,
            "source_url": source.source_url,
            "vector_store_id": source.vector_store_id,
            "chunk_count": source.chunk_count or 0,
        },
    }


@router.post("/api/document/{document_id}/sources/upload-file", tags=["文档来源"])
async def add_document_source_file(
    document_id: int,
    file: UploadFile = File(...),
    source_name: Optional[str] = Form(None),
    total_files: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    from runtime.features import require_capability, require_document_parser
    require_capability("document_ingest")
    require_document_parser(file.filename or "")
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    if not document.vector_store_id or str(document.vector_store_id).startswith("text_direct_"):
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="当前文档未完成向量化，暂不可添加来源")

    if not file or not file.filename:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="请上传文件")

    if total_files is not None and total_files > MAX_FILES_PER_CONVERSATION_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {MAX_FILES_PER_CONVERSATION_UPLOAD} 个文件",
        )

    # 累计限制：主文档(可计入) + 已存在来源文件 + 排队文件 + 当前请求文件(1个) <= 5
    # 注意：该接口是单文件上传，前端会循环调用；这里按“每次 +1”校验，避免批次重复计算。
    if total_files is not None and total_files < 1:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="上传文件数量参数非法")

    existing_file_count_result = await db.execute(
        select(func.count(db_models.DocumentSource.id)).where(
            db_models.DocumentSource.document_id == document_id,
            db_models.DocumentSource.user_id == current_user.id,
            db_models.DocumentSource.source_type == "file",
        )
    )
    existing_file_count = int(existing_file_count_result.scalar() or 0)
    primary_file_count = 1 if _is_primary_document_counted_as_file(document) else 0

    pending_count = 0
    redis_client = await get_redis_client()
    pending_key = f"{SOURCE_UPLOAD_PENDING_DOC_PREFIX}{document_id}"
    if redis_client:
        try:
            pending_count = int(await redis_client.get(pending_key) or 0)
        except Exception:
            pending_count = 0

    if primary_file_count + existing_file_count + pending_count + 1 > MAX_FILES_PER_CONVERSATION_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=(
                f"文件来源总数最多 {MAX_FILES_PER_CONVERSATION_UPLOAD} 个，"
                f"当前已占用 {primary_file_count + existing_file_count} 个，排队中 {pending_count} 个"
            ),
        )

    # 分块读取，超过 settings.max_upload_mb 直接 413
    file_content = await read_upload_with_limit(file)
    if not file_content:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="文件内容为空")

    user_id = int(cast(Any, current_user.id))
    from tasks.source_tasks import process_source_file
    final_source_name = (source_name or file.filename or "上传文件").strip()[:255] or "上传文件"
    # 入队前预占一个排队名额；任务结束时会归还
    if redis_client:
        try:
            await redis_client.incr(pending_key)
            await redis_client.expire(pending_key, 3600)
        except Exception:
            pass

    try:
        task = process_source_file.delay(
            document_id=int(cast(Any, document.id)),
            user_id=user_id,
            filename=file.filename,
            file_content_b64=base64.b64encode(file_content).decode("utf-8"),
            source_name=final_source_name,
        )
    except Exception:
        if redis_client:
            try:
                current = await redis_client.decr(pending_key)
                if current <= 0:
                    await redis_client.set(pending_key, 0, ex=3600)
            except Exception:
                pass
        raise

    return {
        "success": True,
        "message": "文件已加入后台处理队列",
        "data": {
            "task_id": task.id,
            "source_name": final_source_name,
            "status": "pending",
        },
    }


@router.post("/api/document/{document_id}/sources/upload-url", tags=["文档来源"])
async def add_document_source_url(
    document_id: int,
    body: AddSourceUrlRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    if not document.vector_store_id or str(document.vector_store_id).startswith("text_direct_"):
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="当前文档未完成向量化，暂不可添加来源")

    url = body.url.strip()
    if not url:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="URL 不能为空")

    try:
        downloaded = await run_in_threadpool(fetch_html, url)
    except UnsafeURLError as exc:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message=str(exc))
    if not downloaded:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="无法抓取该网页内容")

    extracted = await run_in_threadpool(
        trafilatura.extract,
        downloaded,
        include_comments=False,
        include_tables=True,
        include_images=False,
        include_formatting=False,
        include_links=False,
        output_format="txt",
    )
    text_content = (extracted or "").replace("\x00", "").strip()
    if not text_content:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="网页正文提取为空")

    final_source_name = (body.source_name or url).strip()[:255] or "网页来源"
    source = await _save_source_with_vector(
        db,
        document,
        int(cast(Any, current_user.id)),
        source_type="web",
        source_name=final_source_name,
        content=text_content,
        source_url=url,
    )

    return {
        "success": True,
        "message": "网页来源添加成功",
        "data": {
            "id": source.id,
            "source_id": f"source:{source.id}",
            "source_type": source.source_type,
            "source_name": source.source_name,
            "source_url": source.source_url,
            "vector_store_id": source.vector_store_id,
            "chunk_count": source.chunk_count or 0,
        },
    }


@router.get("/api/document/source-task/{task_id}/status", tags=["文档来源"])
async def get_source_task_status(
    task_id: str,
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    payload = _build_source_task_payload(task_id)
    payload["success"] = True
    return payload


@router.post("/api/document/source-tasks/status", tags=["文档来源"])
async def get_source_tasks_status_batch(
    body: SourceTaskBatchRequest,
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    task_ids = [str(task_id).strip() for task_id in (body.task_ids or []) if str(task_id).strip()]
    if not task_ids:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="task_ids 不能为空")
    if len(task_ids) > SOURCE_TASK_BATCH_MAX_SIZE:
        raise_http_error(
            status_code=400,
            code=ErrorCode.SOURCES_400_BATCH_LIMIT,
            message=f"task_ids 一次最多 {SOURCE_TASK_BATCH_MAX_SIZE} 个",
            details={"max_size": SOURCE_TASK_BATCH_MAX_SIZE},
        )

    tasks, all_done = _build_source_task_batch_payload(task_ids)
    return {
        "success": True,
        "data": {
            "tasks": tasks,
            "all_done": all_done,
        },
    }


@router.post("/api/document/source-tasks/stream", tags=["文档来源"])
async def stream_source_tasks_status(
    body: SourceTaskBatchRequest,
    current_user: db_models.User = Depends(auth.get_current_user),
) -> StreamingResponse:
    task_ids = [str(task_id).strip() for task_id in (body.task_ids or []) if str(task_id).strip()]
    if not task_ids:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="task_ids 不能为空")
    if len(task_ids) > SOURCE_TASK_BATCH_MAX_SIZE:
        raise_http_error(
            status_code=400,
            code=ErrorCode.SOURCES_400_BATCH_LIMIT,
            message=f"task_ids 一次最多 {SOURCE_TASK_BATCH_MAX_SIZE} 个",
            details={"max_size": SOURCE_TASK_BATCH_MAX_SIZE},
        )

    async def event_generator():
        started_at = time.time()
        while True:
            tasks, all_done = _build_source_task_batch_payload(task_ids)
            payload = {
                "success": True,
                "tasks": tasks,
                "all_done": all_done,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            if all_done:
                break

            if time.time() - started_at > SOURCE_TASK_STREAM_TIMEOUT_SECONDS:
                timeout_payload = {
                    "success": True,
                    "tasks": tasks,
                    "all_done": False,
                    "timeout": True,
                }
                yield f"data: {json.dumps(timeout_payload, ensure_ascii=False)}\n\n"
                break

            await asyncio.sleep(SOURCE_TASK_STREAM_INTERVAL_SECONDS)

        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/document/{document_id}/sources/deep-research", tags=["文档来源"])
async def import_source_from_deep_research(
    document_id: int,
    body: DeepResearchImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    credit_service = CreditService(db)
    try:
        await credit_service.deduct_for_deep_research(int(cast(Any, current_user.id)), document_id)
        await db.commit()
    except InsufficientCreditsError as e:
        raise_http_error(status_code=402, code=ErrorCode.SOURCES_402_EXCEPTION, message="请求失败", details=e.message)

    try:
        research_data = await _run_deep_research(body.question.strip())
    except HTTPException:
        raise
    except Exception as exc:
        raise_http_error(status_code=502, code=ErrorCode.SOURCES_502_EXCEPTION, message="Deep Research 调用失败", details=str(exc))

    source_name = (body.source_name or "Deep Research").strip() or "Deep Research"
    source = await _save_source_with_vector(
        db,
        document,
        int(cast(Any, current_user.id)),
        source_type="web_research",
        source_name=source_name,
        content=research_data["content"],
        source_url=None,
    )

    return {
        "success": True,
        "message": "Deep Research 来源导入成功",
        "data": {
            "id": source.id,
            "source_id": f"source:{source.id}",
            "source_type": source.source_type,
            "source_name": source.source_name,
            "vector_store_id": source.vector_store_id,
            "chunk_count": source.chunk_count or 0,
            "references": research_data.get("references", []),
        },
    }


@router.post("/api/document/{document_id}/sources/deep-research/preview", tags=["文档来源"])
async def preview_deep_research_sources(
    document_id: int,
    body: DeepResearchPreviewRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    question = body.question.strip()
    if not question:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="问题不能为空")

    credit_service = CreditService(db)
    try:
        await credit_service.deduct_for_deep_research(int(cast(Any, current_user.id)), document_id)
        await db.commit()
    except InsufficientCreditsError as e:
        raise_http_error(status_code=402, code=ErrorCode.SOURCES_402_EXCEPTION, message="请求失败", details=e.message)

    try:
        research_data = await _run_deep_research(question)
    except HTTPException:
        raise
    except Exception as exc:
        raise_http_error(status_code=502, code=ErrorCode.SOURCES_502_EXCEPTION, message="Deep Research 调用失败", details=str(exc))

    references = research_data.get("references", [])
    web_sites = research_data.get("web_sites", [])
    candidates_from_web = _extract_website_candidates(web_sites if isinstance(web_sites, list) else [])
    candidates_from_refs = _extract_reference_candidates(references if isinstance(references, list) else [])
    candidates = _merge_candidates(candidates_from_web, candidates_from_refs)

    if not candidates:
        return {
            "success": True,
            "message": "本次搜索未获取到可选网页来源，请更换关键词重试",
            "data": {
                "draft_id": "",
                "candidates": [],
                "debug": research_data.get("debug", {}),
            },
        }

    draft_id = uuid.uuid4().hex
    await _save_deep_research_draft(draft_id, {
        "created_at": time.time(),
        "user_id": int(cast(Any, current_user.id)),
        "question": question,
        "content": research_data.get("content", ""),
        "candidates": candidates,
    })

    return {
        "success": True,
        "message": "已返回候选网页来源",
        "data": {
            "draft_id": draft_id,
            "candidates": candidates,
            "debug": research_data.get("debug", {}),
        },
    }


@router.post("/api/document/{document_id}/sources/deep-research/import", tags=["文档来源"])
async def import_selected_deep_research_sources(
    document_id: int,
    body: DeepResearchBatchImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    draft = await _load_deep_research_draft(body.draft_id)
    if not draft or int(draft.get("user_id") or 0) != int(cast(Any, current_user.id)):
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="候选来源已过期，请重新搜索")

    candidates = draft.get("candidates", [])
    candidate_map = {str(item.get("id")): item for item in candidates if isinstance(item, dict)}
    selected_ids = [sid for sid in body.selected_reference_ids if sid in candidate_map]

    imported_sources: list[dict[str, Any]] = []
    failed_sources: list[dict[str, Any]] = []

    report_name = f"Deep Research 报告：{draft.get('question', '')}"[:255]
    report_source = await _save_source_with_vector(
        db,
        document,
        int(cast(Any, current_user.id)),
        source_type="web_research",
        source_name=report_name,
        content=str(draft.get("content", "")),
        source_url=None,
    )
    imported_sources.append(
        {
            "id": report_source.id,
            "source_id": f"source:{report_source.id}",
            "source_name": report_source.source_name,
            "source_url": report_source.source_url,
            "source_type": "web_research",
        }
    )

    max_import_count = min(len(selected_ids), 10)
    for sid in selected_ids[:max_import_count]:
        item = candidate_map[sid]
        title = str(item.get("title") or "网页来源").strip()[:255]
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        try:
            page_text = await run_in_threadpool(_fetch_webpage_text, url)
            content = page_text or snippet or f"来源链接：{url}"
            source = await _save_source_with_vector(
                db,
                document,
                int(cast(Any, current_user.id)),
                source_type="web",
                source_name=title,
                content=content,
                source_url=url,
            )
            imported_sources.append(
                {
                    "id": source.id,
                    "source_id": f"source:{source.id}",
                    "source_name": source.source_name,
                    "source_url": source.source_url,
                    "source_type": "web",
                }
            )
        except Exception as exc:
            failed_sources.append(
                {
                    "id": sid,
                    "title": title,
                    "url": url,
                    "error": str(exc),
                }
            )

    return {
        "success": True,
        "message": "已完成勾选来源导入",
        "data": {
            "imported_count": len(imported_sources),
            "failed_count": len(failed_sources),
            "imported_sources": imported_sources,
            "failed_sources": failed_sources,
        },
    }


@router.get("/api/document/{document_id}/sources/{source_id}/content", tags=["文档来源"])
async def get_source_content(
    document_id: int,
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    source = await db.get(db_models.DocumentSource, source_id)
    if source is None or source.document_id != document_id or source.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="来源不存在")

    if not source.vector_store_id:
        return {
            "success": True,
            "data": {
                "content": source.source_preview or "",
            },
        }

    try:
        def _read_source_chunks():
            collection = vector_store_manager.client.get_collection(str(source.vector_store_id))
            return collection.get(include=["documents", "metadatas"])

        # Chroma 同步调用放线程池，避免阻塞事件循环
        raw = await vector_store_manager._run_in_executor(_read_source_chunks)
        documents = raw.get("documents") or []
        metadatas = raw.get("metadatas") or []

        pairs: list[tuple[str, dict[str, Any]]] = []
        for idx, doc in enumerate(documents):
            if not isinstance(doc, str) or not doc.strip():
                continue
            metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
            pairs.append((doc.strip(), metadata))

        if pairs:
            pairs.sort(key=lambda item: int(cast(Any, item[1].get("chunk_index", 0)) or 0))
            content = "\n\n".join([doc for doc, _ in pairs]).strip()
        else:
            content = ""

        if source.source_url and _is_url_only_content(content, source.source_url):
            refreshed_text = await run_in_threadpool(_fetch_webpage_text, str(source.source_url))
            if refreshed_text:
                old_collection_id = str(source.vector_store_id)
                new_collection_id = await vector_store_manager._run_in_executor(
                    vector_store_manager.create_collection, int(cast(Any, document.id))
                )
                refreshed_chunk_count = await vector_store_manager.add_documents(
                    collection_id=new_collection_id,
                    text=refreshed_text,
                    metadata={
                        "document_id": str(document.id),
                        "source_id": f"source:{source.id}",
                        "source_name": source.source_name,
                        "source_type": source.source_type,
                        "source_url": source.source_url or "",
                    },
                    structured_elements=None,
                )
                source.vector_store_id = new_collection_id
                source.chunk_count = refreshed_chunk_count
                source.source_preview = refreshed_text[:500]
                await db.commit()

                try:
                    await vector_store_manager._run_in_executor(vector_store_manager.delete_collection, old_collection_id)
                except Exception:
                    pass

                content = refreshed_text

        return {
            "success": True,
            "data": {
                "content": content or (source.source_preview or ""),
            },
        }
    except Exception as exc:
        raise_http_error(status_code=500, code=ErrorCode.SOURCES_500_EXCEPTION, message="读取来源全文失败", details=str(exc))


@router.patch("/api/document/{document_id}/sources/{source_id}", tags=["文档来源"])
async def rename_document_source(
    document_id: int,
    source_id: int,
    body: UpdateSourceRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    doc = await db.get(db_models.Document, document_id)
    if doc is None or doc.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    source = await db.get(db_models.DocumentSource, source_id)
    if source is None or source.document_id != document_id or source.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="来源不存在")

    has_name = body.source_name is not None
    has_content = body.content is not None
    if not has_name and not has_content:
        raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="请至少提供 source_name 或 content")

    old_collection_id = ""
    updated_name: Optional[str] = None
    updated_content: Optional[str] = None

    if has_name:
        updated_name = str(body.source_name or "").strip()
        if not updated_name:
            raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="来源名称不能为空")
        source.source_name = updated_name

    if has_content:
        updated_content = str(body.content or "").strip()
        if not updated_content:
            raise_http_error(status_code=400, code=ErrorCode.SOURCES_400_MESSAGE, message="来源内容不能为空")
        ensure_text_length(updated_content, "来源内容")

        old_collection_id = str(source.vector_store_id or "").strip()
        new_collection_id = await vector_store_manager._run_in_executor(vector_store_manager.create_collection, document_id)
        chunk_count = await vector_store_manager.add_documents(
            collection_id=new_collection_id,
            text=updated_content,
            metadata={
                "document_id": str(doc.id),
                "source_id": f"source:{source.id}",
                "source_name": source.source_name,
                "source_type": source.source_type,
                "source_url": source.source_url or "",
            },
            structured_elements=None,
        )

        source.vector_store_id = new_collection_id
        source.chunk_count = chunk_count
        source.source_preview = updated_content[:500]

    await db.commit()
    await db.refresh(source)

    if has_content:
        old_collection_id = str(old_collection_id or "").strip()
        if settings.is_local_mode:
            from runtime.storage import local_storage

            await asyncio.to_thread(local_storage.save_source, current_user.id, source.id, updated_content)
        if old_collection_id and old_collection_id != str(source.vector_store_id or ""):
            try:
                await vector_store_manager._run_in_executor(vector_store_manager.delete_collection, old_collection_id)
            except Exception:
                pass

    return {
        "success": True,
        "data": {
            "id": source.id,
            "source_name": source.source_name,
            "source_preview": source.source_preview,
            "chunk_count": source.chunk_count or 0,
        },
    }


@router.delete("/api/document/{document_id}/sources/{source_id}", tags=["文档来源"])
async def delete_document_source(
    document_id: int,
    source_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
) -> dict[str, Any]:
    document = await db.get(db_models.Document, document_id)
    if document is None or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="文档不存在")

    source = await db.get(db_models.DocumentSource, source_id)
    if source is None or source.document_id != document_id or source.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.SOURCES_404_MESSAGE, message="来源不存在")

    deleted_chunks = 0
    if source.vector_store_id:
        await vector_store_manager._run_in_executor(vector_store_manager.delete_collection, str(source.vector_store_id))
        chunk_count_value = cast(Any, source.chunk_count)
        deleted_chunks = int(chunk_count_value) if chunk_count_value is not None else 0

    await db.delete(source)
    await db.commit()
    if settings.is_local_mode:
        from runtime.storage import local_storage

        await asyncio.to_thread(local_storage.delete_source, current_user.id, source_id)

    return {
        "success": True,
        "message": "来源删除成功",
        "data": {"deleted_chunks": deleted_chunks},
    }
