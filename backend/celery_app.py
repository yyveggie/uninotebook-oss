"""
Celery 应用配置

UniNotebook 分布式任务队列 - 使用 Redis 作为 Broker 和 Backend
"""
import os
import sys
from pathlib import Path

# 确保 backend 目录在 Python 路径中（Celery Worker 需要）
_backend_dir = os.path.dirname(os.path.abspath(__file__))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery import Celery
from celery.signals import worker_process_init
from kombu import Queue

# 延迟导入配置，避免循环依赖
def get_redis_url(db: int = 1) -> str:
    """获取 Redis URL（动态加载配置）"""
    try:
        from config import settings
        host = getattr(settings, 'redis_host', 'localhost')
        port = getattr(settings, 'redis_port', 6379)
        password = getattr(settings, 'redis_password', None)
        
        if password:
            return f"redis://:{password}@{host}:{port}/{db}"
        return f"redis://{host}:{port}/{db}"
    except ImportError:
        return f"redis://localhost:6379/{db}"


# 创建 Celery 应用
celery_app = Celery(
    "uninotebook",
    broker=get_redis_url(db=1),      # Broker: Redis DB 1
    backend=get_redis_url(db=2),     # Result Backend: Redis DB 2
    # 开源版不含邮件任务模块（无注册流程），只登记实际存在的任务模块，
    # 否则 worker 启动时会因 ImportError 直接退出
    include=[
        module
        for module in (
            "tasks.document_tasks",
            "tasks.source_tasks",
            "tasks.generation_tasks",
            "tasks.ppt_tasks",
            "tasks.podcast_tasks",
            "tasks.email_tasks",
        )
        if (Path(__file__).resolve().parent / (module.replace(".", "/") + ".py")).exists()
    ]
)

# Celery 配置
celery_app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    
    # 任务追踪
    task_track_started=True,
    result_extended=True,
    
    # 超时设置
    task_time_limit=600,         # 硬超时: 10 分钟
    task_soft_time_limit=540,    # 软超时: 9 分钟（允许清理）
    
    # Worker 配置
    worker_prefetch_multiplier=1,  # 公平调度，避免任务堆积在单个 Worker
    worker_concurrency=2,          # 默认并发数（可通过启动参数覆盖）
    
    # 队列定义
    task_queues=(
        Queue('default', routing_key='default'),
        Queue('document', routing_key='document.#'),
        Queue('generation_chat', routing_key='generation.chat.#'),
        Queue('generation_content', routing_key='generation.content.#'),
        Queue('generation_heavy', routing_key='generation.heavy.#'),
        Queue('generation_creative', routing_key='generation.creative.#'),
        Queue('ppt', routing_key='ppt.#'),
        Queue('email_otp', routing_key='email.otp.#'),
        Queue('email', routing_key='email.#'),
    ),
    task_default_queue='default',
    task_default_routing_key='default',
    
    # 任务路由
    task_routes={
        'tasks.document.process_full': {'queue': 'document', 'routing_key': 'document.process_full'},
        'tasks.document.process_url': {'queue': 'document', 'routing_key': 'document.process_url'},
        'tasks.source.process_file': {'queue': 'document', 'routing_key': 'document.source_process_file'},
        'tasks.generation.flashcards': {'queue': 'generation_heavy', 'routing_key': 'generation.heavy.flashcards'},
        'tasks.generation.quiz': {'queue': 'generation_heavy', 'routing_key': 'generation.heavy.quiz'},
        'tasks.generation.mindmap': {'queue': 'generation_heavy', 'routing_key': 'generation.heavy.mindmap'},
        'tasks.generation.highlights': {'queue': 'generation_heavy', 'routing_key': 'generation.heavy.highlights'},
        'tasks.generation.report': {'queue': 'generation_heavy', 'routing_key': 'generation.heavy.report'},
        'tasks.generation.learning_path': {'queue': 'generation_heavy', 'routing_key': 'generation.heavy.learning_path'},
        'tasks.generation.interview_start': {'queue': 'generation_creative', 'routing_key': 'generation.creative.interview_start'},
        'tasks.generation.eureka': {'queue': 'generation_creative', 'routing_key': 'generation.creative.eureka'},
        'tasks.generation.merge_suggestions': {'queue': 'generation_content', 'routing_key': 'generation.content.merge_suggestions'},
        'tasks.generation.chat_flashcards': {'queue': 'generation_chat', 'routing_key': 'generation.chat.flashcards'},
        'tasks.generation.chat_quiz': {'queue': 'generation_chat', 'routing_key': 'generation.chat.quiz'},
        'tasks.generation.chat_highlights': {'queue': 'generation_chat', 'routing_key': 'generation.chat.highlights'},
        'tasks.ppt.generate': {'queue': 'ppt', 'routing_key': 'ppt.generate'},
        'tasks.poster.generate': {'queue': 'ppt', 'routing_key': 'ppt.poster'},
        'tasks.podcast.generate': {'queue': 'generation_creative', 'routing_key': 'generation.creative.podcast'},
        'tasks.email.send_verification_email': {'queue': 'email_otp', 'routing_key': 'email.otp.verification'},
        'tasks.email.send_password_reset_email': {'queue': 'email', 'routing_key': 'email.reset_password'},
        'tasks.email.send_contact_email': {'queue': 'email', 'routing_key': 'email.contact'},
    },

    # 任务级速率限制（并发舱壁）
    task_annotations={
        'tasks.generation.flashcards': {'rate_limit': '20/m'},
        'tasks.generation.quiz': {'rate_limit': '20/m'},
        'tasks.generation.mindmap': {'rate_limit': '12/m'},
        'tasks.generation.highlights': {'rate_limit': '20/m'},
        'tasks.generation.report': {'rate_limit': '10/m'},
        'tasks.generation.learning_path': {'rate_limit': '10/m'},
        'tasks.generation.interview_start': {'rate_limit': '8/m'},
        'tasks.generation.eureka': {'rate_limit': '8/m'},
        'tasks.generation.chat_flashcards': {'rate_limit': '40/m'},
        'tasks.generation.chat_quiz': {'rate_limit': '40/m'},
        'tasks.generation.chat_highlights': {'rate_limit': '40/m'},
        'tasks.generation.merge_suggestions': {'rate_limit': '30/m'},
        'tasks.ppt.generate': {
            'rate_limit': '6/m',
            'soft_time_limit': 2700,   # 45 分钟
            'time_limit': 3000,        # 50 分钟
        },
        'tasks.poster.generate': {
            'rate_limit': '6/m',
            'soft_time_limit': 1800,   # 30 分钟
            'time_limit': 2100,        # 35 分钟
        },
        'tasks.podcast.generate': {'rate_limit': '6/m'},
    },
    
    # 结果过期时间（1 小时）
    result_expires=3600,
    
    # 任务重试
    task_acks_late=True,  # 任务完成后才确认，防止 Worker 崩溃丢失任务
    task_reject_on_worker_lost=True,

    # Redis Broker 消费策略（尽量公平，避免长队列饿死）
    broker_transport_options={
        'queue_order_strategy': 'round_robin',
        'visibility_timeout': 3600,
    },

    # 日志策略：不劫持根日志，保留 utils.logger 的统一格式与输出
    worker_hijack_root_logger=False,
)

# 可选：定义定时任务（Beat）
# celery_app.conf.beat_schedule = {
#     'cleanup-expired-tasks': {
#         'task': 'tasks.cleanup.cleanup_expired',
#         'schedule': 3600.0,  # 每小时
#     },
# }


# ==================== 孤立向量集合清理 ====================
# 入库任务中途被直接杀掉（OOM、重启）时，已创建的向量集合不会被异常分支清理。
# worker 子进程启动时（包括崩溃后被主进程重新拉起的子进程）在后台线程里清理一次，
# 同一台机器 10 分钟内最多执行一次。设置 VECTOR_ORPHAN_SWEEP=0 可关闭。
_ORPHAN_SWEEP_INTERVAL_SECONDS = 600


def _run_orphan_vector_sweep() -> None:
    import asyncio
    import logging
    import tempfile
    import time

    stamp = Path(tempfile.gettempdir()) / "uninotebook_vector_sweep.stamp"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < _ORPHAN_SWEEP_INTERVAL_SECONDS:
            return
        stamp.touch()
    except OSError:
        pass

    async def _sweep() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from document_service.vector_maintenance import sweep_orphan_collections
        from runtime.database import build_database_url, build_engine_kwargs

        # 独立的小连接池：不碰任务线程正在使用的数据库引擎
        engine = create_async_engine(
            build_database_url(), **build_engine_kwargs(pool_size=1, max_overflow=0)
        )
        try:
            await sweep_orphan_collections(async_sessionmaker(engine, expire_on_commit=False))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_sweep())
    except Exception as exc:
        logging.getLogger(__name__).warning("⚠️ 孤立向量集合清理失败: %s", exc)


@worker_process_init.connect
def _schedule_orphan_vector_sweep(**_kwargs) -> None:
    # 信号处理必须尽快返回（阻塞会拖慢甚至卡死子进程启动），实际清理放到后台线程
    if os.getenv("VECTOR_ORPHAN_SWEEP", "1") == "0":
        return
    import threading

    threading.Thread(target=_run_orphan_vector_sweep, name="orphan-vector-sweep", daemon=True).start()
