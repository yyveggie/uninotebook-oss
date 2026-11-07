"""
路由共享的工具函数
"""
import json
import hashlib
from datetime import datetime, timezone
from email.utils import format_datetime, parsedate_to_datetime
from collections.abc import Mapping, MutableMapping
from typing import Optional as Opt, List, Dict, Any, Callable

from fastapi import HTTPException
from sqlalchemy import update, select
from sqlalchemy.ext.asyncio import AsyncSession

from cache_service import invalidate_document_history
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode


def _normalize_datetime(value: Opt[datetime]) -> Opt[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_weak_etag(payload: Any) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def to_http_date(value: Opt[datetime]) -> Opt[str]:
    normalized = _normalize_datetime(value)
    if normalized is None:
        return None
    return format_datetime(normalized, usegmt=True)


def latest_datetime(candidates: List[Opt[datetime]]) -> Opt[datetime]:
    normalized_values = [_normalize_datetime(v) for v in candidates if v is not None]
    if not normalized_values:
        return None
    return max(normalized_values)


def is_not_modified(request_headers: Mapping[str, str], etag: str, last_modified: Opt[datetime]) -> bool:
    inm = request_headers.get("if-none-match")
    if inm:
        tokens = [token.strip() for token in inm.split(",") if token.strip()]
        if "*" in tokens or etag in tokens:
            return True

    ims = request_headers.get("if-modified-since")
    normalized_last_modified = _normalize_datetime(last_modified)
    if ims and normalized_last_modified is not None:
        try:
            ims_dt = parsedate_to_datetime(ims)
            if ims_dt.tzinfo is None:
                ims_dt = ims_dt.replace(tzinfo=timezone.utc)
            ims_ts = int(ims_dt.timestamp())
            lm_ts = int(normalized_last_modified.timestamp())
            if ims_ts >= lm_ts:
                return True
        except Exception:
            pass

    return False


def apply_cache_headers(
    response_headers: Any,
    etag: str,
    last_modified: Opt[datetime],
    cache_control: str,
    vary: str = "Authorization, Cookie, Accept-Encoding",
) -> None:
    response_headers["ETag"] = etag
    response_headers["Cache-Control"] = cache_control
    response_headers["Vary"] = vary
    http_date = to_http_date(last_modified)
    if http_date:
        response_headers["Last-Modified"] = http_date


def build_structured_elements_from_text(text: str) -> List[Dict[str, str]]:
    """为纯文本构造简易的结构化元素，便于向量化流程复用"""
    if not text:
        return []

    # 先按空行切分段落，不足时退回逐行
    raw_segments = [segment.strip() for segment in text.split('\n\n') if segment.strip()]
    if not raw_segments:
        raw_segments = [line.strip() for line in text.split('\n') if line.strip()]

    total = max(len(raw_segments), 1)
    elements: List[Dict[str, str]] = []
    for idx, segment in enumerate(raw_segments):
        elements.append({
            "text": segment,
            "category": "Text",
            "type": "text",
            "id": f"text_{idx}",
            "position": idx / total
        })

    return elements


async def generic_delete_resource(
    db: AsyncSession,
    model_cls: Any,
    resource_id: int,
    document_id: int,
    user_id: int,
    cache_names: List[str],
    resource_label: str = "记录",
):
    """
    通用资源删除函数（单事务原子操作：删除 + is_current 自动提升）
    """
    record = await db.get(model_cls, resource_id)
    if not record or record.document_id != document_id or record.user_id != user_id:
        raise_http_error(status_code=404, code=ErrorCode.HELPERS_404_MESSAGE, message=f"{resource_label}不存在")

    was_current = getattr(record, "is_current", False)

    await db.delete(record)
    await db.flush()

    if was_current:
        latest_result = await db.execute(
            select(model_cls)
            .filter(
                model_cls.document_id == document_id,
                model_cls.user_id == user_id,
            )
            .order_by(model_cls.created_at.desc())
            .limit(1)
        )
        latest = latest_result.scalars().first()
        if latest and not latest.is_current:
            latest.is_current = True

    await db.commit()
    await invalidate_document_history(user_id, document_id, names=cache_names)

    return {"success": True, "message": "删除成功"}


async def generic_rename_resource(
    db: AsyncSession,
    model_cls: Any,
    resource_id: int,
    document_id: int,
    user_id: int,
    new_title: str,
    cache_names: List[str],
    json_field_name: Opt[str] = None,
    json_transformer: Opt[Callable[[Dict[str, Any], str], Dict[str, Any]]] = None
):
    """
    通用资源重命名函数 (Unified Resource Renaming)

    Args:
        db: 数据库会话
        model_cls: SQLAlchemy 模型类 (e.g. db_models.DocumentMindmap)
        resource_id: 资源ID
        document_id: 文档ID
        user_id: 用户ID
        new_title: 新标题
        cache_names: 需要失效的缓存名称列表 (e.g. ["mindmap"])
        json_field_name: 如果需要同步更新 JSON 字段，提供字段名 (e.g. "mindmap_data")
        json_transformer: JSON 转换函数，接收 (current_json, new_title)，返回 new_json
    """
    # 1. 验证存在性和权限
    result = await db.execute(
        select(model_cls).filter(
            model_cls.id == resource_id,
            model_cls.document_id == document_id,
            model_cls.user_id == user_id
        )
    )
    record = result.scalars().first()
    if not record:
        raise_http_error(status_code=404, code=ErrorCode.HELPERS_404_MESSAGE, message="记录不存在")

    update_values = {"title": new_title}

    # 2. 处理 JSON 数据更新 (如果需要)
    if json_field_name and json_transformer:
        current_data = getattr(record, json_field_name)

        # 健壮的解析逻辑：处理字符串格式的 JSON
        if isinstance(current_data, str):
            try:
                current_data = json.loads(current_data)
            except:
                current_data = {}

        if not isinstance(current_data, dict):
            current_data = {}

        # 执行转换
        new_data = json_transformer(current_data, new_title)
        update_values[json_field_name] = new_data

    # 3. 执行直接更新 (Robust Update)
    await db.execute(
        update(model_cls)
        .where(model_cls.id == resource_id)
        .values(**update_values)
    )
    await db.commit()

    # 4. 失效缓存
    await invalidate_document_history(user_id, document_id, names=cache_names)

    return {"success": True, "message": "重命名成功"}
