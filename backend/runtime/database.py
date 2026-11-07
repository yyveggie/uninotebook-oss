"""
数据库后端选择

- **local** ：SQLite + aiosqlite，零外部服务，数据落在 `~/.uninotebook/`
- **其他模式**：PostgreSQL + asyncpg，连接池按预算治理

SQLite 在本项目里的难点是**并发写**：Web 主线程与最多 4 个后台任务线程会同时
写库，默认配置下极易撞上 `database is locked`。因此每条连接建立时都会设置：

- `journal_mode=WAL`   读写不互斥，读多写少的场景下并发大幅改善
- `busy_timeout`       撞锁时自动等待重试，而不是立刻抛错
- `foreign_keys=ON`    SQLite 默认**不**强制外键，而模型里大量使用
                       `ondelete="CASCADE"`，不开这个删除行为会与 PostgreSQL 不一致
- `synchronous=NORMAL` WAL 下的推荐值，兼顾安全与写入速度
"""
from __future__ import annotations

import os
from typing import Any, Dict, Tuple

from datetime import timezone

from sqlalchemy import Column, DateTime, event
from sqlalchemy.pool import NullPool
from sqlalchemy.types import TypeDecorator

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


SQLITE_FILENAME = "uninotebook.db"
# 撞锁后的等待上限（毫秒）。本地单用户场景下写事务都很短，
# 15s 足以覆盖后台任务的批量写入。
SQLITE_BUSY_TIMEOUT_MS = 15_000


def is_sqlite() -> bool:
    return settings.is_local_mode


class UTCDateTime(TypeDecorator):
    """
    让 SQLite 的时间列和 PostgreSQL 的 timestamptz 行为一致：读出来带 UTC 时区。

    SQLite 不保存时区，CURRENT_TIMESTAMP 写入的是 UTC。读出来是不带时区的时间，
    序列化后浏览器会当成本地时间显示，所有时间都差一个时区。
    写入时带时区的值换算成 UTC；不带时区的值按 UTC 处理。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


def _use_utc_datetime(column, _table) -> None:
    if type(column.type) is DateTime and column.type.timezone:
        column.type = UTCDateTime()


def install_utc_datetimes() -> None:
    """把此后定义的全部 DateTime(timezone=True) 列换成 UTCDateTime。须在导入模型之前调用。"""
    if not event.contains(Column, "after_parent_attach", _use_utc_datetime):
        event.listen(Column, "after_parent_attach", _use_utc_datetime)


def build_database_url() -> str:
    """按部署模式返回数据库连接串"""
    if is_sqlite():
        data_dir = settings.data_dir
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / SQLITE_FILENAME).touch(mode=0o600, exist_ok=True)
        return f"sqlite+aiosqlite:///{data_dir / SQLITE_FILENAME}"

    return (
        f"postgresql+asyncpg://{settings.db_user}:{settings.db_password}"
        f"@{settings.db_host}:{settings.db_port}/{settings.db_name}"
    )


def resolve_pool_config() -> Tuple[int, int]:
    """
    连接池治理：优先读取显式配置，否则按总连接预算反推每 worker 池参数。

    规则：
    - per_worker_budget = db_connection_budget // db_workers_hint
    - pool_size 默认取 per_worker_budget 的约 60%
    - max_overflow = per_worker_budget - pool_size
    """
    if settings.db_pool_size is not None and settings.db_max_overflow is not None:
        return settings.db_pool_size, settings.db_max_overflow

    workers = max(1, int(os.getenv("GUNICORN_WORKERS", str(settings.db_workers_hint))))
    budget = max(4, int(settings.db_connection_budget))
    per_worker_budget = max(2, budget // workers)

    pool_size = max(2, min(12, int(per_worker_budget * 0.6)))
    max_overflow = max(0, per_worker_budget - pool_size)
    return pool_size, max_overflow


def build_engine_kwargs(**overrides: Any) -> Dict[str, Any]:
    """
    返回 `create_async_engine` 的关键字参数。

    SQLite 与 PostgreSQL 的参数**不能混用**：NullPool 不接受 pool_size /
    max_overflow，传了会直接 TypeError。
    """
    if is_sqlite():
        return {
            # 每次取用建立新连接。SQLite 建连极廉价，且能彻底避免
            # 连接在不同线程/事件循环间被复用。
            "poolclass": NullPool,
            "connect_args": {"check_same_thread": False},
            "echo": False,
        }

    connect_args: Dict[str, Any] = {}
    if settings.db_use_pgbouncer:
        connect_args["statement_cache_size"] = 0

    pool_size, max_overflow = resolve_pool_config()
    kwargs: Dict[str, Any] = {
        "pool_pre_ping": settings.db_pool_pre_ping,
        "pool_size": pool_size,
        "max_overflow": max_overflow,
        "pool_recycle": settings.db_pool_recycle,
        "pool_timeout": settings.db_pool_timeout,
        "pool_use_lifo": settings.db_pool_use_lifo,
        "connect_args": connect_args,
        "echo": False,
    }
    kwargs.update(overrides)
    return kwargs


def install_sqlite_pragmas(async_engine) -> None:
    """
    为 SQLite 连接安装 PRAGMA。非 SQLite 时为空操作。

    必须挂在 sync_engine 上——异步引擎的连接事件由底层同步引擎发出。
    """
    if not is_sqlite():
        return

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()

    logger.info("🗄️ SQLite PRAGMA 已启用 (WAL / busy_timeout / foreign_keys)")


def describe_backend() -> str:
    """供启动日志展示的后端描述（不含密码）"""
    if is_sqlite():
        return f"SQLite ({settings.data_dir / SQLITE_FILENAME})"
    return (
        f"PostgreSQL ({settings.db_host}:{settings.db_port}/{settings.db_name})"
    )
