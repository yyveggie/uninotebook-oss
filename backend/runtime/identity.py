"""
身份提供者

本地部署没有注册/登录，但 `user_id` 是整个数据模型的骨架——文档归属、缓存键、
向量库 metadata 全都依赖它（全仓 790 处引用）。因此本地版**不删除用户体系**，
而是固定为一个「本地单用户」。

实现方式：FastAPI 的 `dependency_overrides`。162 处
`Depends(auth.get_current_user)` / `Depends(get_current_user)` 一行都不用改——
两种写法指向同一个函数对象，覆盖一次即可全量生效。

非本地模式下不安装任何覆盖，鉴权链路保持原样。
"""
from __future__ import annotations

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from config import settings
from database import AsyncSessionLocal, get_db
from utils.logger import get_logger

logger = get_logger(__name__)


# 本地单用户的固定身份
LOCAL_USER_ID = 1
LOCAL_USER_EMAIL = "local@uninotebook.local"
LOCAL_USER_NAME = "本地用户"


async def ensure_local_user() -> db_models.User:
    """
    确保本地单用户存在（幂等）。应在应用启动时调用一次。
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(db_models.User).where(db_models.User.id == LOCAL_USER_ID)
        )
        user = result.scalar_one_or_none()
        if user is not None:
            return user

        user = db_models.User(
            id=LOCAL_USER_ID,
            email=LOCAL_USER_EMAIL,
            username=LOCAL_USER_NAME,
            # 本地版不做密码校验，此字段仅为满足非空约束
            hashed_password="!local-no-login!",
            full_name=LOCAL_USER_NAME,
            is_active=True,
            # 本地用户即管理员，否则管理类接口在本地不可用
            is_superuser=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        logger.info("👤 已创建本地单用户 (id=%s)", LOCAL_USER_ID)
        return user


async def local_user_dependency(db: AsyncSession = Depends(get_db)) -> db_models.User:
    """替代 get_current_user 的本地依赖：恒返回本地单用户"""
    result = await db.execute(
        select(db_models.User).where(db_models.User.id == LOCAL_USER_ID)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # 数据库被清空等异常情况下兜底重建
        user = await ensure_local_user()
    return user


def install_identity_overrides(app: FastAPI) -> None:
    """
    在本地模式下用固定单用户替换全部鉴权依赖。

    非本地模式下本函数不做任何事。
    """
    if not settings.is_local_mode:
        return

    # 两个鉴权依赖都指向本地单用户；本地用户已是 superuser
    app.dependency_overrides[auth.get_current_user] = local_user_dependency
    app.dependency_overrides[auth.get_current_active_superuser] = local_user_dependency

    logger.info("🔓 本地模式：鉴权已短路为固定单用户 (id=%s)", LOCAL_USER_ID)
