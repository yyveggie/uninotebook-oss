"""
统一任务基类

提供所有 Celery 任务的公共基础设施：
- AsyncTask: 支持 async/await 的任务基类
- run_async: 在 Celery 同步环境中运行 async 函数的工具
"""
import os
import sys
import asyncio
import functools
import logging
import atexit
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, TypeVar
from celery import Task

# 确保 backend 目录在 Python 路径中（Celery Worker 进程需要）
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from runtime.tasks import LocalDispatchMixin

logger = logging.getLogger(__name__)

T = TypeVar("T")

# 距 expires 不足该秒数时，重试大概率来不及执行（会被当作过期丢弃）
_RETRY_EXPIRY_MARGIN_SECONDS = 60

# Worker 事件循环，按**线程**隔离。
# - Celery prefork：每个子进程只有一个任务线程，等价于原先的进程级单例
# - 本地进程内模式：线程池中每个线程各持一个循环，互不干扰
_loop_state = threading.local()
# 记录所有已创建的循环，供进程退出时统一关闭
_all_loops: list[asyncio.AbstractEventLoop] = []
_all_loops_lock = threading.Lock()


def _get_or_create_worker_loop() -> asyncio.AbstractEventLoop:
    loop = getattr(_loop_state, "loop", None)
    if loop is None or loop.is_closed():
        loop = asyncio.new_event_loop()
        _loop_state.loop = loop
        with _all_loops_lock:
            _all_loops.append(loop)
    return loop


def _close_worker_loop() -> None:
    with _all_loops_lock:
        loops = list(_all_loops)
        _all_loops.clear()
    for loop in loops:
        if not loop.is_closed():
            loop.close()
    _loop_state.loop = None


atexit.register(_close_worker_loop)


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """
    在 Celery Worker 中运行异步协程
    
    由于 Celery 默认使用 prefork (多进程) 模式，每个任务在同步环境中执行。
    此函数创建新的事件循环来运行 async 代码。
    
    🆕 工业级优化：
    - 重置 vector_store 的 embeddings 客户端，避免事件循环冲突
    
    Args:
        coro: 异步协程对象
        
    Returns:
        协程的返回值
    """
    # 确保路径设置正确（fork 进程可能丢失）
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    
    try:
        # 尝试获取当前事件循环
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的事件循环，创建新的
        loop = None
    
    if loop is not None:
        # 如果已经在事件循环中（不太可能在 Celery Worker 中），使用 nest_asyncio
        # 但为了简单起见，我们直接报错
        raise RuntimeError("Cannot run_async inside an existing event loop")
    
    # 🆕 重置 vector_store 的 embeddings 客户端，确保在新事件循环中创建
    try:
        from document_service.vector_store import vector_store_manager
        vector_store_manager.reset_embeddings_client()
    except Exception as e:
        logger.warning(f"无法重置 embeddings 客户端: {e}")
    
    # 创建新的 wrapper 协程来处理初始化和清理
    async def _async_runner():
        # 1. 销毁旧的数据库连接池（关键修复）
        # AsyncEngine 全局单例会持有旧事件循环的锁/资源，导致 "attached to a different loop" 错误
        # 在新循环中调用 dispose() 会强制重建池，绑定到当前循环
        #
        # ⚠️ 仅在 Celery 独立进程中这样做。本地进程内模式下任务与 Web 服务同进程，
        # dispose() 会连带摧毁**正在服务 HTTP 请求**的连接池。
        # 该模式下任务用的是 tasks/utils.py 中按线程隔离的独立引擎，本就无需 dispose。
        from config import settings
        if not settings.is_local_mode:
            try:
                from database import async_engine
                await async_engine.dispose()
            except Exception as e:
                logger.warning(f"无法重置数据库连接池: {e}")

        # 2. 运行实际任务
        if settings.is_local_mode:
            from runtime.tasks import run_local_coroutine

            return await run_local_coroutine(coro)
        return await coro

    # 复用 worker 进程级单例事件循环，避免每个任务更换 loop 导致 Future 绑定冲突
    loop = _get_or_create_worker_loop()

    if loop.is_running():
        # 正常情况下在 Celery 同步任务中不会发生，仅作为防御分支
        future = asyncio.run_coroutine_threadsafe(_async_runner(), loop)
        return future.result()

    return loop.run_until_complete(_async_runner())


class AsyncTask(LocalDispatchMixin, Task):
    """
    支持异步代码的 Celery 任务基类

    特性：
    - 自动包装 async 函数
    - 统一的错误处理和日志
    - 任务状态回调
    - 数据库会话管理
    - 本地模式下 `.delay()` / `.apply_async()` 改走进程内线程池（见 runtime.tasks）

    使用方式：
    ```python
    from tasks.base import AsyncTask
    from celery_app import celery_app
    
    @celery_app.task(base=AsyncTask, bind=True)
    def my_task(self, arg1, arg2):
        async def _run():
            # async 代码
            pass
        return self.run_async(_run())
    ```
    """
    
    # 抽象基类标记，防止 Celery 直接注册
    abstract = True
    
    # 重试配置
    autoretry_for = (Exception,)
    retry_kwargs = {'max_retries': 3}
    retry_backoff = True
    retry_backoff_max = 600  # 最大退避时间（秒）
    retry_jitter = True
    
    def run_async(self, coro: Coroutine[Any, Any, T]) -> T:
        """运行异步协程的便捷方法"""
        return run_async(coro)

    def is_final_attempt(self) -> bool:
        """
        当前执行失败后是否**不会**再自动重试（即这是最后一次尝试）。

        失败退款等「只能做一次」的补偿动作必须以此为准：autoretry_for=(Exception,)
        会让同一个任务最多执行 1 + max_retries 次，每次都退款就会重复退款。
        """
        request = self.request
        if getattr(request, "called_directly", True):
            # 非 worker 内调用（直接调用 / eager），Task.retry 会直接抛出原异常，不会重试
            return True

        max_retries = (self.retry_kwargs or {}).get("max_retries", self.max_retries)
        if max_retries is None:
            return False
        if (getattr(request, "retries", 0) or 0) >= max_retries:
            return True

        # 重试消息沿用原任务的 expires：若已临近过期，重试会被 worker 直接丢弃（不会再走到退款逻辑），
        # 因此视为最后一次尝试。
        expires = getattr(request, "expires", None)
        if expires:
            try:
                deadline = expires if isinstance(expires, datetime) else datetime.fromisoformat(str(expires))
                if deadline.tzinfo is None:
                    deadline = deadline.replace(tzinfo=timezone.utc)
                if deadline <= datetime.now(timezone.utc) + timedelta(seconds=_RETRY_EXPIRY_MARGIN_SECONDS):
                    return True
            except (TypeError, ValueError):
                pass
        return False

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """任务失败回调"""
        logger.error(
            f"Task {self.name}[{task_id}] failed: {exc}",
            exc_info=einfo.exc_info if einfo else None
        )
        super().on_failure(exc, task_id, args, kwargs, einfo)
    
    def on_success(self, retval, task_id, args, kwargs):
        """任务成功回调"""
        logger.info(f"Task {self.name}[{task_id}] completed successfully")
        super().on_success(retval, task_id, args, kwargs)
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """任务重试回调"""
        logger.warning(
            f"Task {self.name}[{task_id}] retrying due to: {exc}",
            exc_info=einfo.exc_info if einfo else None
        )
        super().on_retry(exc, task_id, args, kwargs, einfo)


def async_task(func: Callable[..., Coroutine]) -> Callable:
    """
    装饰器：将 async 函数转换为可在 Celery 中运行的同步函数
    
    用法：
    ```python
    @celery_app.task(bind=True, base=AsyncTask)
    @async_task
    async def my_async_task(self, arg1, arg2):
        # 直接写 async 代码，无需手动调用 run_async
        result = await some_async_operation()
        return result
    ```
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return run_async(func(*args, **kwargs))
    return wrapper
