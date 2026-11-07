"""
首屏聚合路由

提供 bootstrap 接口，聚合用户态、积分和首页摘要，降低首屏 RTT。
支持通过 refresh_token Cookie 自动恢复登录态（跨子域跳转场景）。
"""
from datetime import datetime
from typing import Optional, Any

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from jose import JWTError, jwt

import auth as auth_module
import db_models
from config import settings
from database import get_db
from routes._helpers import (
    apply_cache_headers,
    build_weak_etag,
    is_not_modified,
    latest_datetime,
)

router = APIRouter()


# Cookie domain 计算逻辑与 routes/auth.py 共用一份实现——
# 此前两处各存一份副本，改一处就会漏另一处（域名也曾在两边被硬编码）。
from routes.auth import _normalize_auth_cookie_domain, set_refresh_token_cookie  # noqa: E402


def _to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


async def _resolve_optional_user(request: Request, db: AsyncSession) -> Optional[db_models.User]:
    """从 Authorization Header 解析 access_token，获取用户"""
    # 本地版没有登录流程，也就没有 token。本接口是前端首屏判断登录态的依据，
    # 不在此处短路的话，本地版会一直停在落地页的「请先登录」。
    # 注意：本接口未使用 get_current_user 依赖，因此不受 dependency_overrides 影响，
    # 必须单独处理。
    if settings.is_local_mode:
        from runtime.identity import LOCAL_USER_ID

        result = await db.execute(
            select(db_models.User).where(db_models.User.id == LOCAL_USER_ID)
        )
        return result.scalar_one_or_none()

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return None

    token = auth_header.replace("Bearer ", "", 1).strip()
    if not token:
        return None

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        if payload.get("type") != "access":
            return None

        user_id = payload.get("user_id")
        if not user_id:
            return None

        # 重置密码后签发的旧 access_token 一律作废（与 get_current_user 的判定一致）
        if await auth_module.is_user_token_revoked(user_id, payload.get("iat")):
            return None

        result = await db.execute(
            select(db_models.User).where(db_models.User.id == int(user_id))
        )
        user = result.scalar_one_or_none()
        if not user or not bool(getattr(user, "is_active", False)):
            return None
        return user
    except (JWTError, ValueError, TypeError):
        return None


async def _try_refresh_from_cookie(
    request: Request, response: Response, db: AsyncSession
) -> tuple[Optional[db_models.User], Optional[str]]:
    """
    当没有 access_token 时，尝试用 Cookie 中的 refresh_token 恢复登录态。
    
    Returns:
        (user, new_access_token) 或 (None, None)
    """
    refresh_token = request.cookies.get("refresh_token")
    if not refresh_token:
        return None, None

    try:
        # 宽限期内：别的标签页刚把这个 refresh_token 轮换掉。复用轮换结果并下发新 Cookie，
        # 否则首屏会被误判为未登录，旧 Cookie 过了宽限期还会触发「重复使用」吊销。
        rotated = await auth_module.get_rotated_refresh_pair(refresh_token)
        if rotated:
            rotated_access, rotated_refresh = rotated
            token_data = auth_module.decode_token(rotated_refresh, token_type="refresh")
            user = await auth_module.get_user_by_id(db, token_data.user_id)
            if not user or not user.is_active:
                return None, None
            set_refresh_token_cookie(request, response, rotated_refresh)
            return user, rotated_access

        # 检查黑名单
        if await auth_module.is_token_blacklisted(refresh_token):
            return None, None

        # 解码 refresh_token
        token_data = auth_module.decode_token(refresh_token, token_type="refresh")

        # 获取用户
        user = await auth_module.get_user_by_id(db, token_data.user_id)
        if not user or not user.is_active:
            return None, None

        # 幂等窗口 + 单飞锁：首屏并发恢复只计算一次 access_token
        cached_access_token = await auth_module.get_cached_refresh_access_token(refresh_token)
        if cached_access_token:
            new_access_token = cached_access_token
        else:
            lock_acquired = await auth_module.acquire_refresh_singleflight(refresh_token)
            if not lock_acquired:
                waited_token = await auth_module.wait_for_cached_refresh_access_token(refresh_token)
                if waited_token:
                    new_access_token = waited_token
                else:
                    new_access_token = auth_module.create_access_token(
                        data={"sub": user.username, "user_id": user.id}
                    )
            else:
                try:
                    new_access_token = auth_module.create_access_token(
                        data={"sub": user.username, "user_id": user.id}
                    )
                    await auth_module.cache_refresh_access_token(
                        refresh_token,
                        new_access_token,
                        ttl_seconds=min(30, settings.access_token_expire_minutes * 60),
                    )
                finally:
                    await auth_module.release_refresh_singleflight(refresh_token)

        return user, new_access_token

    except Exception:
        return None, None


@router.get("/api/bootstrap", tags=["首屏聚合"], response_model=None)
async def get_bootstrap(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """首屏聚合数据：用户态 + 积分 + 首页摘要。"""
    current_user = await _resolve_optional_user(request, db)
    new_access_token = None

    # 如果 Authorization Header 中没有有效的 access_token，
    # 尝试用 Cookie 中的 refresh_token 恢复登录态。
    # 这是跨子域跳转（www → space）后的关键认证路径。
    if not current_user:
        current_user, new_access_token = await _try_refresh_from_cookie(
            request, response, db
        )

    if not current_user:
        payload: dict[str, Any] = {
            "success": True,
            "authenticated": False,
            # 首屏据此决定走「免登录工作台」还是「登录/落地页」，无需依赖构建期变量
            "deploy_mode": "local" if settings.is_local_mode else "saas",
            "data": None,
        }
        etag = build_weak_etag(payload)
        cache_control = "private, max-age=5, s-maxage=5, stale-while-revalidate=15"
        apply_cache_headers(response.headers, etag, None, cache_control)

        if is_not_modified(request.headers, etag, None):
            return Response(status_code=304, headers=dict(response.headers))

        return payload

    from credit_service import CreditService

    service = CreditService(db)
    current_user_obj: Any = current_user
    user_id = int(getattr(current_user_obj, "id"))
    credit = await service.get_user_credits(user_id)

    total_count = await db.scalar(
        select(func.count(db_models.Document.id)).where(
            db_models.Document.user_id == user_id
        )
    )
    completed_count = await db.scalar(
        select(func.count(db_models.Document.id)).where(
            db_models.Document.user_id == user_id,
            db_models.Document.processing_status == "completed",
        )
    )
    processing_count = await db.scalar(
        select(func.count(db_models.Document.id)).where(
            db_models.Document.user_id == user_id,
            db_models.Document.processing_status.in_(["pending", "processing"]),
        )
    )

    recent_docs_result = await db.execute(
        select(db_models.Document)
        .where(db_models.Document.user_id == user_id)
        .order_by(db_models.Document.last_accessed_at.desc())
        .limit(5)
    )
    recent_docs = recent_docs_result.scalars().all()

    await db.commit()

    last_modified = latest_datetime([
        getattr(current_user_obj, "created_at", None),
        getattr(credit, "updated_at", None),
        *[getattr(d, "last_accessed_at", None) for d in recent_docs],
        *[getattr(d, "created_at", None) for d in recent_docs],
    ])

    payload: dict[str, Any] = {
        "success": True,
        "authenticated": True,
        "deploy_mode": "local" if settings.is_local_mode else "saas",
        "data": {
            "user": {
                "id": user_id,
                "email": current_user_obj.email,
                "phone": current_user_obj.phone,
                "username": current_user_obj.username,
                "full_name": current_user_obj.full_name,
                "is_active": bool(current_user_obj.is_active),
                "my_invite_code": current_user_obj.my_invite_code,
                "created_at": _to_iso(getattr(current_user_obj, "created_at", None)),
            },
            "credits": {
                "balance": credit.balance,
                "total_earned": credit.total_earned,
                "total_spent": credit.total_spent,
                "updated_at": _to_iso(getattr(credit, "updated_at", None)),
            },
            "home_summary": {
                "stats": {
                    "documents_total": int(total_count or 0),
                    "documents_completed": int(completed_count or 0),
                    "documents_processing": int(processing_count or 0),
                },
                "recent_documents": [
                    {
                        "document_id": d.id,
                        "filename": d.filename,
                        "display_name": d.display_name,
                        "summary": d.summary,
                        "processing_status": d.processing_status,
                        "created_at": _to_iso(getattr(d, "created_at", None)),
                        "last_accessed_at": _to_iso(getattr(d, "last_accessed_at", None)),
                    }
                    for d in recent_docs
                ],
            },
        },
    }

    # 如果是通过 refresh_token Cookie 恢复的登录态，附带新的 access_token
    # 前端收到后存入内存，后续请求即可通过 Authorization Header 认证
    if new_access_token:
        payload["access_token"] = new_access_token

    etag = build_weak_etag(payload)
    cache_control = "private, max-age=15, s-maxage=15, stale-while-revalidate=45"
    apply_cache_headers(response.headers, etag, last_modified, cache_control)

    if is_not_modified(request.headers, etag, last_modified):
        return Response(status_code=304, headers=dict(response.headers))

    return payload
