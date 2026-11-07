"""
幂等处理工具：支持 Idempotency-Key 请求头

- 首次请求：抢占锁并继续执行
- 重复请求：若已有结果则直接返回缓存结果
- 并发重复：返回 409，提示请求处理中
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

from fastapi import HTTPException

from config import settings
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from cache_service.redis_client import (
    cache_delete,
    cache_get_bytes,
    cache_set_bytes,
    cache_set_nx_bytes,
    is_cache_available,
)


@dataclass
class IdempotencyContext:
    enabled: bool
    key: Optional[str] = None
    lock_key: Optional[str] = None
    resp_key: Optional[str] = None
    replay_response: Optional[Any] = None


def _normalize_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.strip().encode("utf-8")).hexdigest()


async def begin_idempotency(
    *,
    idempotency_key: Optional[str],
    user_id: int,
    scope: str,
) -> IdempotencyContext:
    if not idempotency_key:
        return IdempotencyContext(enabled=False)

    # 缓存不可用时（本地版无 Redis、或线上 Redis 抖动）直接放弃幂等保护。
    # 否则下方的 SETNX 会因拿不到锁而恒返回 False，把**每个**带幂等键的请求
    # 都误判为「重复请求处理中」并回 409。丢失去重能力可以接受，拒绝服务不行。
    if not await is_cache_available():
        return IdempotencyContext(enabled=False)

    key_hash = _normalize_key(idempotency_key)
    base = f"idempotency:{user_id}:{scope}:{key_hash}"
    lock_key = f"{base}:lock"
    resp_key = f"{base}:resp"

    cached = await cache_get_bytes(resp_key)
    if cached:
        try:
            payload = json.loads(cached.decode("utf-8"))
            return IdempotencyContext(
                enabled=True,
                key=key_hash,
                lock_key=lock_key,
                resp_key=resp_key,
                replay_response=payload,
            )
        except Exception:
            pass

    lock_acquired = await cache_set_nx_bytes(
        lock_key,
        b"1",
        ttl=max(30, settings.idempotency_ttl_seconds),
    )
    if not lock_acquired:
        raise_http_error(status_code=409, code=ErrorCode.IDEMPOTENCY_409_MESSAGE, message="重复请求处理中，请稍后重试")

    return IdempotencyContext(
        enabled=True,
        key=key_hash,
        lock_key=lock_key,
        resp_key=resp_key,
    )


async def commit_idempotency(ctx: IdempotencyContext, response_payload: Any) -> Any:
    if not ctx.enabled or not ctx.resp_key:
        return response_payload

    try:
        payload = json.dumps(response_payload, ensure_ascii=False).encode("utf-8")
        await cache_set_bytes(ctx.resp_key, payload, ttl=settings.idempotency_ttl_seconds)
    finally:
        if ctx.lock_key:
            await cache_delete(ctx.lock_key)

    return response_payload


async def rollback_idempotency(ctx: Optional[IdempotencyContext]) -> None:
    if not ctx or not ctx.enabled:
        return
    if ctx.lock_key:
        await cache_delete(ctx.lock_key)
