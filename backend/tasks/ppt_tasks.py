"""
PPT/海报生成任务

包含：
- PPT 生成（paper2slides）
- 海报生成
"""
import os
import sys
import logging
from typing import Optional

# 确保 backend 目录在 Python 路径中
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery_app import celery_app
from tasks.base import AsyncTask
from tasks.utils import get_db_session, update_task_status, invalidate_caches
from celery.exceptions import SoftTimeLimitExceeded

logger = logging.getLogger(__name__)


async def _refund_record_credits(model_class, task_id: int, credit_type: str, amount: int, label: str, error_msg: str):
    """PPT / 海报任务失败时退还积分（按记录 ID 幂等：同一个任务只退一次）"""
    from config import settings

    if settings.is_local_mode:
        # 本地版不计费，无需退款
        return

    from sqlalchemy import select
    from tasks.utils import refund_task_credits_once

    try:
        async with get_db_session() as db:
            result = await db.execute(
                select(model_class.user_id).where(model_class.id == task_id)
            )
            user_id = result.scalar_one_or_none()
    except Exception as lookup_err:
        logger.error(f"❌ [Celery] 退还{label}积分失败（查询任务失败）: {lookup_err}")
        return

    if not user_id:
        return

    await refund_task_credits_once(
        user_id=user_id,
        amount=amount,
        credit_type=credit_type,
        reference_id=str(task_id),
        reason=f"{label}生成失败: {(error_msg or '')[:100]}",
    )


async def _refund_ppt_credits(task_id: int, error_msg: str):
    """PPT 任务失败时退还积分（同一任务只退一次）"""
    from credit_service.constants import CreditCost
    import db_models

    await _refund_record_credits(db_models.DocumentPPT, task_id, "ppt", CreditCost.PPT, "PPT", error_msg)


async def _refund_poster_credits(task_id: int, error_msg: str):
    """海报任务失败时退还积分（同一任务只退一次）"""
    from credit_service.constants import CreditCost
    import db_models

    await _refund_record_credits(db_models.DocumentPoster, task_id, "poster", CreditCost.POSTER, "海报", error_msg)


@celery_app.task(bind=True, base=AsyncTask, name="tasks.ppt.generate")
def generate_ppt(self, task_id: int):
    """
    PPT 生成任务
    
    对应原 run_ppt_generation_task 函数
    """
    async def _run():
        from document_service.generators.ppt.service import run_ppt_generation_task
        from database import AsyncSessionLocal
        
        logger.info(f"🎨 [Celery] 开始 PPT 生成: task_id={task_id}")
        
        try:
            pipeline_error = await run_ppt_generation_task(task_id, AsyncSessionLocal)

        except SoftTimeLimitExceeded:
            error_msg = "任务执行超时（SoftTimeLimitExceeded），请稍后重试或减少生成内容复杂度"
            logger.error(f"❌ [Celery] PPT 生成超时: {task_id}")

            # 🆕 退还积分：只在最后一次尝试失败时退（自动重试期间不退），且按任务幂等
            if self.is_final_attempt():
                await _refund_ppt_credits(task_id, error_msg)
            raise RuntimeError(error_msg)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ [Celery] PPT 生成失败: {error_msg}")
            
            # 🆕 退还积分：只在最后一次尝试失败时退（自动重试期间不退），且按任务幂等
            if self.is_final_attempt():
                await _refund_ppt_credits(task_id, error_msg)
            
            raise  # 重新抛出异常

        if pipeline_error:
            # 流水线失败已在 service 内标记为 failed（不触发自动重试），这里退款一次
            logger.error(f"❌ [Celery] PPT 生成失败: task_id={task_id}, {pipeline_error}")
            await _refund_ppt_credits(task_id, pipeline_error)
            return {"task_id": task_id, "status": "failed", "error": pipeline_error}

        logger.info(f"✅ [Celery] PPT 生成完成: task_id={task_id}")
        return {"task_id": task_id, "status": "completed"}
    
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.poster.generate")
def generate_poster(self, task_id: int):
    """
    海报生成任务
    
    对应原 run_poster_pipeline 函数
    """
    async def _run():
        from document_service.generators.poster.service import run_poster_pipeline
        
        logger.info(f"🖼️ [Celery] 开始海报生成: task_id={task_id}")
        
        try:
            pipeline_error = await run_poster_pipeline(task_id)

        except SoftTimeLimitExceeded:
            error_msg = "任务执行超时（SoftTimeLimitExceeded），请稍后重试"
            logger.error(f"❌ [Celery] 海报生成超时: {task_id}")

            # 🆕 退还积分：只在最后一次尝试失败时退（自动重试期间不退），且按任务幂等
            if self.is_final_attempt():
                await _refund_poster_credits(task_id, error_msg)
            raise RuntimeError(error_msg)
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ [Celery] 海报生成失败: {error_msg}")
            
            # 🆕 退还积分：只在最后一次尝试失败时退（自动重试期间不退），且按任务幂等
            if self.is_final_attempt():
                await _refund_poster_credits(task_id, error_msg)
            
            raise  # 重新抛出异常

        if pipeline_error:
            # 流水线跑完但未拿到可访问的海报（已标记 failed，不触发自动重试），这里退款一次
            logger.error(f"❌ [Celery] 海报生成失败: task_id={task_id}, {pipeline_error}")
            await _refund_poster_credits(task_id, pipeline_error)
            return {"task_id": task_id, "status": "failed", "error": pipeline_error}

        logger.info(f"✅ [Celery] 海报生成完成: task_id={task_id}")
        return {"task_id": task_id, "status": "completed"}
    
    return self.run_async(_run())

