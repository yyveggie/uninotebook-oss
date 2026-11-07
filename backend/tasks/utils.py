"""
统一工具函数

为所有 Celery 任务提供公共工具：
- 数据库会话管理
- 任务状态更新
- 缓存失效
- 错误处理
"""
import asyncio
import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Callable, Awaitable
from datetime import datetime

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)


# ============== 任务专用数据库引擎 ==============
# 按**线程**缓存，避免每次任务都创建新引擎。
# - Celery prefork：每个子进程一个任务线程，等价于原先的进程级缓存
# - 本地进程内模式：线程池中每个线程各持一个引擎，
#   与 Web 主线程的引擎彻底隔离，避免 "Future attached to a different loop"

_engine_state = threading.local()


def _get_celery_db_components():
    """
    获取任务专用的数据库引擎和会话工厂

    这些组件在每个任务线程中延迟初始化，
    确保在正确的事件循环中创建，避免 "Future attached to a different loop" 错误
    """
    engine = getattr(_engine_state, "engine", None)
    session_factory = getattr(_engine_state, "session_factory", None)

    if engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from runtime.database import (
            build_database_url,
            build_engine_kwargs,
            install_sqlite_pragmas,
            is_sqlite,
        )

        if is_sqlite():
            # SQLite 用 NullPool，不接受 pool_size / max_overflow
            engine_kwargs = build_engine_kwargs()
        else:
            # Celery 任务用较小的连接池，避免 N 个 worker 打满数据库连接数
            engine_kwargs = build_engine_kwargs(
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
                pool_recycle=1800,   # 30分钟回收连接
            )

        engine = create_async_engine(build_database_url(), **engine_kwargs)
        install_sqlite_pragmas(engine)

        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

        _engine_state.engine = engine
        _engine_state.session_factory = session_factory

        logger.info("🔧 [Task] 初始化线程级数据库引擎 (%s)", threading.current_thread().name)

    return engine, session_factory


@asynccontextmanager
async def get_db_session():
    """
    获取异步数据库会话（上下文管理器）
    
    为 Celery 任务创建独立的数据库引擎，避免与主进程事件循环冲突。
    
    在 Celery 任务中使用：
    ```python
    async with get_db_session() as db:
        result = await db.execute(...)
    ```
    """
    _, session_factory = _get_celery_db_components()
    
    session = session_factory()
    try:
        yield session
    except Exception as e:
        await session.rollback()
        # 会话包住整个任务，这里的异常多半不是数据库本身的错误
        logger.error(f"任务出错，已回滚数据库会话: {type(e).__name__}: {e}")
        raise
    finally:
        await session.close()


async def update_task_status(
    db,
    model_class,
    record_id: int,
    status: str,
    progress: Optional[int] = None,
    current_stage: Optional[str] = None,
    error_message: Optional[str] = None,
    extra_fields: Optional[Dict[str, Any]] = None
) -> bool:
    """
    通用任务状态更新函数
    
    适用于所有有 status/progress 字段的模型（Document, PPTTask, DocumentPoster 等）
    
    Args:
        db: 数据库会话
        model_class: SQLAlchemy 模型类
        record_id: 记录 ID
        status: 新状态
        progress: 进度百分比 (0-100)
        current_stage: 当前阶段描述
        error_message: 错误信息（失败时）
        extra_fields: 额外需要更新的字段
        
    Returns:
        是否更新成功
    """
    try:
        record = await db.get(model_class, record_id)
        if not record:
            logger.warning(f"{model_class.__name__} with id={record_id} not found")
            return False
        
        # 更新状态
        if hasattr(record, 'status'):
            record.status = status
        if hasattr(record, 'processing_status'):
            record.processing_status = status
            
        # 更新进度
        if progress is not None:
            if hasattr(record, 'progress'):
                record.progress = progress
            if hasattr(record, 'processing_progress'):
                record.processing_progress = progress
                
        # 更新阶段
        if current_stage is not None and hasattr(record, 'current_stage'):
            record.current_stage = current_stage
            
        # 更新错误信息
        if error_message is not None:
            if hasattr(record, 'error_message'):
                record.error_message = error_message
            if hasattr(record, 'processing_error'):
                record.processing_error = error_message
                
        # 更新额外字段
        if extra_fields:
            for key, value in extra_fields.items():
                if hasattr(record, key):
                    setattr(record, key, value)
        
        await db.commit()
        logger.info(f"Updated {model_class.__name__}[{record_id}]: status={status}, progress={progress}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update {model_class.__name__}[{record_id}]: {e}")
        await db.rollback()
        return False


# 文档解析类任务（上传 / 网页导入 / 添加来源）的总时限，略小于 Celery 软超时（540 秒）。
# 在协程内部用 asyncio 超时收口：失败状态能正常写回；Celery 软超时则是从事件循环外部打断，
# 协程里的状态更新来不及执行，文档会一直停在「处理中」
DOCUMENT_TASK_TIMEOUT_SECONDS = 480
# 解析这一步的时限：正常文档几秒到几十秒。超出多半是卡在网络上（例如 Unstructured
# 首次使用时下载语言数据），与其等满整个任务时限，不如尽快报出原因
PARSE_TIMEOUT_SECONDS = 180
# 原始 PDF 备份到 COS（仅用于在线预览）的时限；超时跳过备份，不影响文档处理
COS_BACKUP_TIMEOUT_SECONDS = 30
# 阶段持续超过该秒数后，定期在日志里报告「仍在进行」
STAGE_HEARTBEAT_SECONDS = 15


@asynccontextmanager
async def log_stage(label: str, record_id: Any, heartbeat_seconds: float = STAGE_HEARTBEAT_SECONDS):
    """
    记录文档处理某一阶段的开始、耗时与失败；阶段持续较久时定期打印「仍在进行」。

    文档卡住时，日志里最后一条「仍在进行」就是卡住的那一步。阶段内的阻塞操作需放到线程里，
    否则事件循环被占住，心跳也打印不出来。
    """
    started = time.monotonic()
    logger.info(f"▶️ [{record_id}] {label}：开始")

    async def heartbeat():
        while True:
            await asyncio.sleep(heartbeat_seconds)
            logger.warning(f"⏳ [{record_id}] {label}：仍在进行，已 {time.monotonic() - started:.0f}s")

    beat = asyncio.ensure_future(heartbeat())
    try:
        yield
    except BaseException as exc:
        logger.error(f"❌ [{record_id}] {label}：失败（{time.monotonic() - started:.1f}s）{type(exc).__name__}: {exc}")
        raise
    else:
        logger.info(f"✅ [{record_id}] {label}：完成（{time.monotonic() - started:.1f}s）")
    finally:
        beat.cancel()


class TaskStatusUpdater:
    """
    任务状态更新器（上下文管理器）
    
    自动处理任务开始、完成、失败状态更新
    
    使用方式：
    ```python
    async with TaskStatusUpdater(db, Document, doc_id) as updater:
        await updater.update(progress=10, stage="parsing")
        # ... 执行任务 ...
        await updater.update(progress=50, stage="vectorizing")
        # ... 继续执行 ...
    # 退出时自动更新为 completed 或 failed
    ```
    """
    
    def __init__(
        self,
        db,
        model_class,
        record_id: int,
        user_id: Optional[int] = None,
        on_complete: Optional[Callable[[], Awaitable]] = None,
        on_failure: Optional[Callable[[Exception], Awaitable]] = None
    ):
        self.db = db
        self.model_class = model_class
        self.record_id = record_id
        self.user_id = user_id
        self.on_complete = on_complete
        self.on_failure = on_failure
        self._failed = False
        self._exception = None
    
    async def __aenter__(self):
        # 开始任务
        await update_task_status(
            self.db, self.model_class, self.record_id,
            status="processing",
            progress=0,
            current_stage="starting"
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 任务失败
            self._failed = True
            self._exception = exc_val
            if isinstance(exc_val, (asyncio.CancelledError, asyncio.TimeoutError)):
                # 总时限触发的取消，异常本身不带说明文字
                error_message = "处理超时，请稍后重试"
            else:
                error_message = str(exc_val)[:500] if exc_val else "Unknown error"
            await update_task_status(
                self.db, self.model_class, self.record_id,
                status="failed",
                progress=0,
                error_message=error_message,
            )
            if self.on_failure:
                try:
                    await self.on_failure(exc_val)
                except Exception as e:
                    logger.error(f"on_failure callback error: {e}")
            # 不抑制异常，让 Celery 处理重试
            return False
        else:
            # 任务成功
            await update_task_status(
                self.db, self.model_class, self.record_id,
                status="completed",
                progress=100
            )
            if self.on_complete:
                try:
                    await self.on_complete()
                except Exception as e:
                    logger.error(f"on_complete callback error: {e}")
            return True
    
    async def update(
        self,
        progress: Optional[int] = None,
        stage: Optional[str] = None,
        **extra_fields
    ):
        """更新任务进度"""
        await update_task_status(
            self.db, self.model_class, self.record_id,
            status="processing",
            progress=progress,
            current_stage=stage,
            extra_fields=extra_fields if extra_fields else None
        )


async def refund_task_credits_once(
    *,
    user_id: int,
    amount: int,
    credit_type: str,
    reference_id: str,
    reason: str,
    dedupe: bool = True,
) -> bool:
    """
    任务失败退款（幂等）：同一 reference_id 的 `{credit_type}_refund` 流水已存在则跳过。
    （dedupe=False 时不做幂等检查，用于没有任务级唯一 reference_id 的调用方）

    调用方应只在最后一次尝试失败时调用（见 AsyncTask.is_final_attempt）；
    幂等检查是兜底，防止重投递 / 竞态导致同一笔扣费被退多次。

    Returns:
        是否实际执行了退款
    """
    from config import settings

    if settings.is_local_mode:
        # 本地版不计费（NullCreditService），无需退款
        return False

    from sqlalchemy import select
    import db_models
    from credit_service import CreditService

    refund_type = f"{credit_type}_refund"
    try:
        async with get_db_session() as db:
            if dedupe:
                existing = await db.execute(
                    select(db_models.CreditTransaction.id).where(
                        db_models.CreditTransaction.user_id == user_id,
                        db_models.CreditTransaction.type == refund_type,
                        db_models.CreditTransaction.reference_id == reference_id,
                    ).limit(1)
                )
                if existing.first() is not None:
                    logger.info(f"💰 [Task] {refund_type} 已退款过，跳过重复退款: reference_id={reference_id}")
                    return False

            credit_service = CreditService(db)
            await credit_service.refund_credits(
                user_id=user_id,
                amount=amount,
                original_transaction_type=credit_type,
                reason=reason,
                reference_id=reference_id,
            )
            await db.commit()
            logger.info(f"💰 [Task] 已退还 {credit_type} 积分 {amount} 给用户 {user_id} (reference_id={reference_id})")
            return True
    except IntegrityError:
        # 并发重投递在查重之后同时退款：数据库唯一约束（uq_credit_transactions_refund_ref）兜底，
        # 整个事务（含余额变动）已回滚，即只退了一次
        logger.info(f"💰 [Task] {refund_type} 已由并发请求退款，数据库约束拦截重复: reference_id={reference_id}")
        return False
    except Exception as refund_err:
        logger.error(f"❌ [Task] 退还 {credit_type} 积分失败: {refund_err}")
        return False


async def invalidate_caches(user_id: int, document_id: Optional[int] = None, names: Optional[list] = None):
    """
    统一的缓存失效函数
    
    Args:
        user_id: 用户 ID
        document_id: 文档 ID（可选）
        names: 缓存名称列表（可选）
    """
    try:
        from cache_service import (
            invalidate_document_data,
            invalidate_document_list_cache,
            invalidate_document_history,
            invalidate_user_document_history
        )
        
        # 失效文档列表缓存
        await invalidate_document_list_cache(user_id)
        
        # 失效用户文档历史
        await invalidate_user_document_history(user_id)
        
        # 如果指定了文档 ID，失效文档相关缓存
        if document_id:
            if names:
                await invalidate_document_data(user_id, document_id, names=names)
                await invalidate_document_history(user_id, document_id, names=names)
            else:
                await invalidate_document_data(user_id, document_id, names=["status"])
                
    except Exception as e:
        logger.warning(f"Cache invalidation failed: {e}")
