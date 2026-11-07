"""Durable local tasks; SaaS dispatch continues to delegate to Celery."""
from __future__ import annotations

import asyncio
import importlib
import sqlite3
import threading
import time
import traceback
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from uuid import uuid4

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)
LOCAL_TASK_WORKERS = settings.local_task_workers
# 文档解析（上传 / 网页导入 / 添加来源）单独一组线程，与服务端的专用文档 Worker 对应：
# 否则 PPT、播客这类长任务占满线程后，一个 100 KB 的上传也要排队等它们结束
DOCUMENT_TASK_NAMES = frozenset({
    "tasks.document.process_full", "tasks.document.process_url", "tasks.source.process_file",
})
_executors: dict = {}
_executor_lock = threading.Lock()
_registry_lock = threading.Lock()
_local_task_registry = {}
_current = threading.local()


class LocalTaskCancelled(BaseException):
    """Escape broad exception handlers so cancellation cannot become success."""


class LocalRetry(Exception):
    def __init__(self, exc=None, countdown=1, max_retries=None):
        self.original, self.countdown, self.max_retries = exc, countdown or 0, max_retries
        super().__init__(str(exc or "重试任务"))


def _store():
    from runtime import task_store
    return task_store


def _lane(task_name):
    return "document" if task_name in DOCUMENT_TASK_NAMES else "general"


def get_local_executor(lane="general"):
    with _executor_lock:
        executor = _executors.get(lane)
        if executor is None:
            workers = settings.local_document_task_workers if lane == "document" else LOCAL_TASK_WORKERS
            executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"uninb-{lane}")
            _executors[lane] = executor
    return executor


def check_cancelled():
    event = getattr(_current, "cancel_event", None)
    if event and event.is_set():
        raise LocalTaskCancelled("用户已取消任务")


async def run_local_coroutine(coro):
    """Cancellation/timeout at await points; always await coroutine cleanup."""
    work = asyncio.ensure_future(coro)
    started = time.monotonic()
    timeout = getattr(_current, "timeout", 900)
    try:
        while not work.done():
            check_cancelled()
            if time.monotonic() - started > timeout:
                raise TimeoutError(f"任务超过 {timeout:g} 秒，可调整 LOCAL_TASK_TIMEOUT_SECONDS 后重试")
            await asyncio.wait({work}, timeout=0.1)
        return await work
    finally:
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)


class LocalAsyncResult:
    def __init__(self, task_id, future: Future | None = None, cancel_event=None):
        self.id, self._future = task_id, future
        self.cancel_event = cancel_event or threading.Event()

    @property
    def task_id(self):
        return self.id

    @property
    def state(self):
        record = _store().get(self.id)
        if record:
            return record["status"]
        if self._future is None or not self._future.done():
            return "STARTED" if self._future and self._future.running() else "PENDING"
        if self._future.cancelled():
            return "REVOKED"
        return "FAILURE" if self._future.exception() else "SUCCESS"

    @property
    def status(self):
        return self.state

    @property
    def result(self):
        record = _store().get(self.id)
        if record:
            return record["result"] if record["status"] == "SUCCESS" else record.get("error")
        if self._future is None or not self._future.done() or self._future.cancelled():
            return None
        return self._future.exception() or self._future.result()

    @property
    def info(self):
        return (_store().get(self.id) or {}).get("meta") or {}

    def ready(self):
        return self.state in _store().TERMINAL_STATES

    def successful(self):
        return self.state == "SUCCESS"

    def failed(self):
        return self.state in {"FAILURE", "INTERRUPTED"}

    def get(self, timeout=None):
        if self._future is not None:
            self._future.result(timeout=timeout)
        if self.state != "SUCCESS":
            raise RuntimeError(self.result or self.state)
        return self.result


def get_local_task_result(task_id):
    with _registry_lock:
        result = _local_task_registry.get(task_id)
    if result is not None:
        return result
    return LocalAsyncResult(task_id) if _store().get(task_id) else None


def _domain_status(record, status, error=None):
    targets = {
        "tasks.document.process_full": ("documents", "document_id"),
        "tasks.document.process_url": ("documents", "document_id"),
        "tasks.document.reindex": ("documents", "document_id"),
        "tasks.ppt.generate": ("document_ppts", "task_id"),
        "tasks.poster.generate": ("document_posters", "task_id"),
        "tasks.podcast.generate": ("document_podcasts", "task_id"),
    }
    target = targets.get(record["name"])
    if not target:
        return
    from runtime.database import SQLITE_FILENAME

    table, key = target
    row_id = record["kwargs"].get(key) or (record["args"][0] if record["args"] else None)
    path = settings.data_dir / SQLITE_FILENAME
    if row_id is None or not path.exists():
        return
    with sqlite3.connect(path, timeout=15) as db:
        columns = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        if not columns:
            return
        fields = {("processing_status" if "processing_status" in columns else "status"): status}
        progress_column = "processing_progress" if "processing_progress" in columns else "progress"
        if progress_column in columns and status == "pending":
            fields[progress_column] = 0
        error_column = "processing_error" if "processing_error" in columns else "error_message"
        if error_column in columns:
            fields[error_column] = error
        db.execute(f'UPDATE "{table}" SET ' + ",".join(f'"{k}"=?' for k in fields) + ' WHERE id=?',
                   (*fields.values(), row_id))


def _task_for_name(name):
    group = name.split(".")[1] if name.startswith("tasks.") else ""
    module = {"document": "document_tasks", "source": "source_tasks", "generation": "generation_tasks",
              "ppt": "ppt_tasks", "poster": "ppt_tasks", "podcast": "podcast_tasks"}.get(group)
    if module:
        importlib.import_module(f"tasks.{module}")
    from celery_app import celery_app
    return celery_app.tasks[name]


def _redact(value):
    from runtime.credentials import credential_store, PROVIDER_SETTINGS_MAP
    value = str(value)
    for provider in PROVIDER_SETTINGS_MAP:
        key = credential_store.api_key(provider)
        if key:
            value = value.replace(key, "[redacted]")
    return value


def _submit(task, record):
    task_id, options = record["id"], record["options"]
    result = LocalAsyncResult(task_id)

    def execute():
        _current.task_id, _current.cancel_event = task_id, result.cancel_event
        _current.timeout = float(options.get("time_limit") or settings.local_task_timeout_seconds)
        attempts = 0
        task.push_request(id=task_id, args=record["args"], kwargs=record["kwargs"],
                          called_directly=False, is_eager=True, retries=0)
        try:
            delay = max(0, options.get("run_at", time.time()) - time.time())
            if result.cancel_event.wait(delay):
                raise LocalTaskCancelled()
            if options.get("expires_at") and time.time() >= options["expires_at"]:
                raise TimeoutError("任务在排队期间已过期，请重试")
            while True:
                check_cancelled()
                task.request.retries = attempts
                _store().update(task_id, status="STARTED", attempts=attempts + 1)
                try:
                    value = task.run(*record["args"], **record["kwargs"])
                    check_cancelled()
                    if isinstance(value, dict) and (value.get("success") is False or value.get("status") == "failed"):
                        raise RuntimeError(value.get("error") or value.get("message") or "任务执行失败")
                    _store().update(task_id, status="SUCCESS", result=value, meta={"progress": 100}, error=None)
                    return value
                except LocalRetry as retry:
                    limit = retry.max_retries if retry.max_retries is not None else getattr(task, "max_retries", 3)
                    if attempts >= limit:
                        raise RuntimeError(str(retry.original or retry)) from retry
                    attempts += 1
                    _store().update(task_id, status="RETRY", error=_redact(retry), attempts=attempts)
                    if result.cancel_event.wait(max(0, float(retry.countdown))):
                        raise LocalTaskCancelled()
        except (LocalTaskCancelled, asyncio.CancelledError, CancelledError):
            _store().update(task_id, status="REVOKED", error="用户已取消任务")
            _domain_status(record, "failed", "任务已取消，可重试")
        except Exception as exc:
            error = _redact(exc)
            trace = _redact(traceback.format_exc())
            _store().update(task_id, status="FAILURE", error=error, traceback=trace)
            _domain_status(record, "failed", error)
            # 完整调用栈直接打到终端（已去除 API Key），排查时不必再去设置页翻任务详情
            logger.error("本地任务失败 %s (%s): %s\n%s", task.name, task_id, error, trace)
        finally:
            task.pop_request()
            _current.task_id, _current.cancel_event = None, None

    with _registry_lock:
        _local_task_registry[task_id] = result
        if len(_local_task_registry) > 500:
            for key, old in list(_local_task_registry.items()):
                if old._future and old._future.done():
                    _local_task_registry.pop(key, None)
                if len(_local_task_registry) <= 400:
                    break
        result._future = get_local_executor(_lane(task.name)).submit(execute)
    return result


def cancel_local_task(task_id):
    record = _store().get(task_id)
    if not record:
        raise KeyError(task_id)
    if record["status"] in _store().TERMINAL_STATES:
        return
    _store().update(task_id, cancel_requested=1)
    result = get_local_task_result(task_id)
    result.cancel_event.set()
    if result._future is None or result._future.cancel():
        _store().update(task_id, status="REVOKED", error="用户已取消任务")
        _domain_status(record, "failed", "任务已取消，可重试")


def retry_local_task(task_id):
    record = _store().get(task_id)
    if not record:
        raise KeyError(task_id)
    if record["status"] not in {"FAILURE", "INTERRUPTED", "REVOKED"}:
        raise ValueError("只有失败、中断或已取消的任务可以重试")
    with _registry_lock:
        previous = _local_task_registry.get(task_id)
        if previous and previous._future and not previous._future.done():
            raise ValueError("任务正在结束，请稍后重试")
    task = _task_for_name(record["name"])
    record["options"] = {k: v for k, v in record["options"].items() if k not in {"run_at", "expires_at"}}
    _store().reset_for_retry(task_id, record["options"])
    _domain_status(record, "pending")
    return _submit(task, record)


def initialize_local_tasks():
    interrupted, pending = _store().recover()
    for record in interrupted:
        _domain_status(record, "failed", "上次运行被中断，请在设置页重试任务")
    for record in pending:
        try:
            if record["cancel_requested"]:
                cancel_local_task(record["id"])
            else:
                _submit(_task_for_name(record["name"]), record)
        except Exception as exc:
            _store().update(record["id"], status="FAILURE", error=_redact(exc))


def shutdown_local_executor(wait=False):
    with _registry_lock:
        for result in _local_task_registry.values():
            if result._future and not result._future.done():
                if not result._future.cancel():
                    result.cancel_event.set()
    with _executor_lock:
        executors = list(_executors.values())
        _executors.clear()
    for executor in executors:
        executor.shutdown(wait=wait, cancel_futures=True)


class LocalDispatchMixin:
    def apply_async(self, args=None, kwargs=None, **options):
        if not settings.is_local_mode:
            return super().apply_async(args, kwargs, **options)
        from fastapi import HTTPException
        args, kwargs = tuple(args or ()), dict(kwargs or {})
        task_id = options.get("task_id") or str(uuid4())
        durable_options = {k: options[k] for k in ("time_limit",) if options.get(k)}
        durable_options["run_at"] = time.time() + float(options.get("countdown") or 0)
        if isinstance(options.get("expires"), (int, float)):
            durable_options["expires_at"] = time.time() + options["expires"]
        try:
            _store().create(task_id, self.name, args, kwargs, durable_options,
                            max_pending=settings.local_task_queue_limit)
        except ValueError as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        return _submit(self, _store().get(task_id))

    def update_state(self, task_id=None, state=None, meta=None, **kwargs):
        if not settings.is_local_mode:
            return super().update_state(task_id=task_id, state=state, meta=meta, **kwargs)
        check_cancelled()
        task_id = task_id or getattr(_current, "task_id", None)
        if task_id:
            _store().update(task_id, status=state or "PROGRESS", meta=meta or {})

    def retry(self, *args, **kwargs):
        if not settings.is_local_mode:
            return super().retry(*args, **kwargs)
        raise LocalRetry(kwargs.get("exc"), kwargs.get("countdown", 1), kwargs.get("max_retries"))


def install_task_overrides():
    if not settings.is_local_mode:
        return
    from celery_app import celery_app
    base = celery_app.Task
    if not issubclass(base, LocalDispatchMixin):
        celery_app.Task = type("LocalTask", (LocalDispatchMixin, base), {})
