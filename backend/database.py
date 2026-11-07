from __future__ import annotations

"""
数据库连接和配置 - 异步版本
"""
import os

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from config import settings
from runtime.database import (
    build_database_url,
    build_engine_kwargs,
    describe_backend,
    install_sqlite_pragmas,
    install_utc_datetimes,
    is_sqlite,
    resolve_pool_config,
)
from utils.logger import get_logger
logger = get_logger(__name__)

# 数据库URL：本地部署用 SQLite + aiosqlite；服务端部署用 PostgreSQL + asyncpg
DATABASE_URL = build_database_url()

# 创建异步数据库引擎
async_engine = create_async_engine(DATABASE_URL, **build_engine_kwargs())

# SQLite 需要逐连接设置 WAL / busy_timeout / 外键（非 SQLite 时为空操作）
install_sqlite_pragmas(async_engine)

if is_sqlite():
    POOL_SIZE = MAX_OVERFLOW = 0  # NullPool：不维护常驻连接
    logger.info("🗄️ 数据库后端: %s", describe_backend())
else:
    POOL_SIZE, MAX_OVERFLOW = resolve_pool_config()
    logger.info(
        "🗄️ DB连接池配置: "
        f"pool_size={POOL_SIZE}, max_overflow={MAX_OVERFLOW}, "
        f"workers_hint={os.getenv('GUNICORN_WORKERS', settings.db_workers_hint)}, "
        f"budget={settings.db_connection_budget}, pgbouncer={settings.db_use_pgbouncer}"
    )

# 提供一个同步引擎给迁移脚本等同步场景使用
engine = async_engine.sync_engine

# 创建异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # 防止访问已提交对象时报错
    autocommit=False,
    autoflush=False,
)

# SQLite 的时间列读出时补上 UTC 时区（必须早于任何模型定义）
if is_sqlite():
    install_utc_datetimes()

# 创建基类
Base = declarative_base()


async def get_db():
    """
    获取异步数据库会话
    用于FastAPI依赖注入
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


def get_async_session_maker():
    """
    获取异步会话工厂（用于后台任务）
    
    Returns:
        async_sessionmaker: 异步会话工厂
    """
    return AsyncSessionLocal


async def init_db():
    """异步初始化数据库（创建所有表）"""
    # 导入所有模型，确保 Base.metadata 包含完整的表定义
    import db_models  # noqa: F401
    import knowledge_graph.models  # noqa: F401

    if is_sqlite():
        from runtime.migrations import upgrade_local_database

        await upgrade_local_database(async_engine, Base.metadata)
        return
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("✅ 数据库表创建成功（异步模式）")

