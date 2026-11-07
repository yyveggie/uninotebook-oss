"""Durable local task journal, separate from event-loop-bound ORM sessions."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager

from config import settings

TERMINAL_STATES = {"SUCCESS", "FAILURE", "REVOKED", "INTERRUPTED"}
_JSON_FIELDS = {"args", "kwargs", "options", "result", "meta"}
_FIELDS = {"status", "result", "error", "traceback", "meta", "attempts", "cancel_requested", "options"}


def reset_for_retry(task_id, options):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        pending = db.execute(
            "SELECT COUNT(*) FROM task_runs WHERE status NOT IN ('SUCCESS','FAILURE','REVOKED','INTERRUPTED')"
        ).fetchone()[0]
        if pending >= settings.local_task_queue_limit:
            raise ValueError("本地后台任务队列已满，请等待任务完成后重试")
        result = db.execute(
            "UPDATE task_runs SET status='PENDING',error=NULL,traceback=NULL,result=NULL,"
            "meta='{}',cancel_requested=0,options=?,updated_at=? WHERE id=? "
            "AND status IN ('FAILURE','INTERRUPTED','REVOKED')",
            (json.dumps(options), time.time(), task_id),
        )
        if result.rowcount != 1:
            raise ValueError("任务已在执行，不能重复重试")


@contextmanager
def connection():
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "tasks.sqlite3"
    if not path.exists():
        path.touch(mode=0o600, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError("任务数据版本比当前程序新，请使用较新版本启动")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("""CREATE TABLE IF NOT EXISTS task_runs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, user_id INTEGER NOT NULL,
            args TEXT NOT NULL, kwargs TEXT NOT NULL, options TEXT NOT NULL,
            status TEXT NOT NULL, result TEXT, error TEXT, traceback TEXT,
            meta TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        )""")
        if version == 0:
            db.execute("PRAGMA user_version=1")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
        path.chmod(0o600)


def _decode(row):
    if row is None:
        return None
    result = dict(row)
    for key in _JSON_FIELDS:
        result[key] = json.loads(result[key]) if result.get(key) is not None else None
    return result


def create(task_id, name, args, kwargs, options, max_pending=100):
    with connection() as db:
        db.execute("BEGIN IMMEDIATE")
        pending = db.execute(
            "SELECT COUNT(*) FROM task_runs WHERE status NOT IN ('SUCCESS','FAILURE','REVOKED','INTERRUPTED')"
        ).fetchone()[0]
        if pending >= max_pending:
            raise ValueError("本地后台任务队列已满，请等待任务完成后重试")
        now = time.time()
        db.execute(
            "INSERT INTO task_runs(id,name,user_id,args,kwargs,options,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,'PENDING',?,?)",
            (task_id, name, int(kwargs.get("user_id", 1)), json.dumps(args),
             json.dumps(kwargs), json.dumps(options), now, now),
        )


def get(task_id):
    with connection() as db:
        return _decode(db.execute("SELECT * FROM task_runs WHERE id=?", (task_id,)).fetchone())


def update(task_id, **fields):
    if not fields or set(fields) - _FIELDS:
        raise ValueError("非法任务字段")
    values = [json.dumps(v, ensure_ascii=False, default=str) if k in _JSON_FIELDS else v
              for k, v in fields.items()]
    with connection() as db:
        db.execute(
            "UPDATE task_runs SET " + ",".join(f"{k}=?" for k in fields) + ",updated_at=? WHERE id=?",
            (*values, time.time(), task_id),
        )


def list_tasks(user_id=1, limit=100):
    with connection() as db:
        return [_decode(row) for row in db.execute(
            "SELECT * FROM task_runs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, min(max(limit, 1), 200)),
        )]


def recover():
    """Resume work that never started; interrupted work needs an explicit retry."""
    with connection() as db:
        interrupted = [_decode(row) for row in db.execute(
            "SELECT * FROM task_runs WHERE status IN ('STARTED','PROGRESS','RETRY')"
        )]
        db.execute(
            "UPDATE task_runs SET status='INTERRUPTED',error=?,updated_at=? "
            "WHERE status IN ('STARTED','PROGRESS','RETRY')",
            ("上次运行被中断，可重试此任务", time.time()),
        )
        pending = [_decode(row) for row in db.execute(
            "SELECT * FROM task_runs WHERE status='PENDING' ORDER BY created_at"
        )]
    return interrupted, pending


def public_task(record):
    return {k: v for k, v in record.items() if k not in {"args", "kwargs", "options", "traceback"}}
