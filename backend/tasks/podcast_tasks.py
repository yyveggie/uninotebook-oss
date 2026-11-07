"""
播客生成任务 - Celery 异步实现
"""
import logging
from typing import Dict, Any, Optional

from celery_app import celery_app
from tasks.base import AsyncTask
from tasks.utils import get_db_session

logger = logging.getLogger(__name__)

async def _run_podcast_generation(task_id: int, text: str, style: str, language: str) -> Optional[str]:
    """
    实际执行播客生成的异步函数

    Returns:
        None 表示成功；生成失败（记录已标记 failed）时返回错误信息
    """
    from database import AsyncSessionLocal
    from config import settings
    from langchain_openai import ChatOpenAI
    from document_service.generators.podcast.generator import PodcastGenerator
    from sqlalchemy import select, update
    from db_models import DocumentPodcast

    async with AsyncSessionLocal() as session:
        try:
            # 1. 获取任务记录
            result = await session.execute(select(DocumentPodcast).where(DocumentPodcast.id == task_id))
            podcast = result.scalar_one_or_none()
            
            if not podcast:
                logger.error(f"❌ [Celery] Podcast task {task_id} not found")
                return f"Podcast task {task_id} not found"
                
            # 更新状态为 processing
            podcast.status = "processing"
            podcast.current_stage = "initializing"
            await session.commit()
            
            # 2. 初始化生成器
            # 注意：这里我们创建新的 LLM 客户端，不复用全局单例，避免事件循环问题
            from llm_service import get_llm_for_feature
            llm = get_llm_for_feature("doc_general", temperature=0.7)
            
            generator = PodcastGenerator(lc_client=llm, db=session)
            
            # 3. 执行生成
            logger.info(f"🎙️ [Celery] 开始生成播客: task_id={task_id}, style={style}")
            error = await generator.generate_podcast(task_id, text, style, language)
            if error:
                logger.error(f"❌ [Celery] 播客生成失败: task_id={task_id}, {error}")
                return error
            
            logger.info(f"✅ [Celery] 播客生成完成: task_id={task_id}")
            return None
            
        except Exception as e:
            logger.error(f"❌ [Celery] 播客生成失败: {e}")
            # 尝试更新数据库状态为 failed
            try:
                await session.execute(
                    update(DocumentPodcast)
                    .where(DocumentPodcast.id == task_id)
                    .values(
                        status="failed",
                        error_message=str(e),
                        current_stage="failed"
                    )
                )
                await session.commit()
            except Exception as db_err:
                logger.error(f"❌ [Celery] 无法更新任务失败状态: {db_err}")
            raise e

@celery_app.task(bind=True, base=AsyncTask, name="tasks.podcast.generate")
def generate_podcast_task(self, task_id: int, text: str, style: str, language: str):
    """
    Celery 任务入口
    """
    async def _run():
        try:
            error = await _run_podcast_generation(task_id, text, style, language)
        except Exception as e:
            # 自动重试期间不退款，只在最后一次尝试失败时退（按任务幂等）
            if self.is_final_attempt():
                await _refund_podcast_credits(task_id, str(e))
            raise
        if error:
            # 生成失败已标记 failed（不触发自动重试），这里退款一次
            await _refund_podcast_credits(task_id, error)
            return {"task_id": task_id, "status": "failed", "error": error}
        return {"task_id": task_id, "status": "completed"}

    return self.run_async(_run())


async def _refund_podcast_credits(task_id: int, error_msg: str):
    """播客任务失败时退还积分（同一任务只退一次）"""
    from credit_service.constants import CreditCost
    from tasks.ppt_tasks import _refund_record_credits
    from db_models import DocumentPodcast

    await _refund_record_credits(DocumentPodcast, task_id, "podcast", CreditCost.PODCAST, "播客", error_msg)
