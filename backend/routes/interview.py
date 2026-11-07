"""
面试功能路由

包含: 面试开始/答题/中止/恢复、面试历史、面试详情、重命名/删除
"""
import re

from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from database import get_db
from utils.logger import log_info, get_logger
from utils.refunds import one_off_refund_reference
from document_service import get_document_service
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from cache_service import (
    get_document_history_cache,
    set_document_history_cache,
    invalidate_document_history,
)
from cache_service import get_redis_client
from config import settings

logger = get_logger(__name__)

router = APIRouter()


async def _ensure_creative_queue_capacity() -> None:
    client = await get_redis_client()
    if not client:
        return
    try:
        pending = await client.llen("generation_creative")
        if pending >= settings.celery_generation_creative_queue_max_pending:
            raise_http_error(status_code=429, code=ErrorCode.INTERVIEW_429_MESSAGE, message="系统繁忙（创意任务排队较多），请稍后再试")
    except HTTPException:
        raise
    except Exception:
        return


async def _ensure_interview_session_access(
    db: AsyncSession,
    user_id: int,
    document_id: int,
    session_id: str,
) -> None:
    document = await db.get(db_models.Document, document_id)
    if not document:
        raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="文档不存在")
    if document.user_id != user_id:
        raise_http_error(status_code=403, code=ErrorCode.INTERVIEW_403_MESSAGE, message="无权访问此文档")

    result = await db.execute(
        select(db_models.InterviewRecord).filter(
            db_models.InterviewRecord.session_id == session_id,
            db_models.InterviewRecord.user_id == user_id,
        )
    )
    record = result.scalar_one_or_none()
    if record:
        if record.document_id != document_id:
            raise_http_error(status_code=403, code=ErrorCode.INTERVIEW_403_MESSAGE, message="无权访问此面试会话")
        return

    from document_service.generators import interview
    manager = interview.interview_manager
    if manager is not None:
        active_session = manager.get_session(session_id)
        if active_session and active_session.document_id == document_id:
            return

    raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="面试会话不存在或已过期")


@router.post("/api/document/{document_id}/interview/start", tags=["面试功能"])
async def start_interview(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    开始面试（异步版本）
    
    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    # 验证文档存在
    document = await db.get(db_models.Document, document_id)
    if not document:
        raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="文档不存在")
    
    if document.user_id != current_user.id:
        raise_http_error(status_code=403, code=ErrorCode.INTERVIEW_403_MESSAGE, message="无权访问此文档")
    
    # 先做队列容量检查，避免队列已满时仍扣费
    await _ensure_creative_queue_capacity()

    # 🆕 积分检查和扣费
    from credit_service import CreditService, CreditCost, InsufficientCreditsError
    credit_service = CreditService(db)
    try:
        await credit_service.deduct_for_interview(current_user.id, document_id)
        await db.commit()
    except InsufficientCreditsError as e:
        raise_http_error(status_code=402, code=ErrorCode.INTERVIEW_402_EXCEPTION, message="请求失败", details=e.message)
    except Exception as e:
        await db.rollback()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="扣费失败", details=str(e))
    
    # 提交 Celery 任务（提交失败时自动退款）
    try:
        from tasks.generation_tasks import start_interview_task
        task = start_interview_task.apply_async(
            kwargs={
                "document_id": document_id,
                "user_id": current_user.id,
            },
            queue="generation_creative",
            expires=settings.celery_task_expires_seconds,
        )
    except Exception as e:
        try:
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=CreditCost.INTERVIEW,
                original_transaction_type="interview",
                reason="面试初始化失败退款",
                reference_id=one_off_refund_reference(document_id),
            )
            await db.commit()
        except Exception as refund_error:
            await db.rollback()
            logger.error(f"❌ 面试初始化失败后退款失败: {refund_error}")

        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="面试初始化失败", details=str(e))
    
    log_info(f"✅ 面试初始化任务已提交: task_id={task.id}")
    
    return {
        "success": True,
        "message": "面试初始化任务已提交",
        "task_id": task.id,
        "task_type": "interview",
        "credits_deducted": CreditCost.INTERVIEW
    }


@router.post("/api/document/{document_id}/interview/answer", tags=["面试功能"])
async def submit_interview_answer(
    document_id: int,
    session_id: str = Body(...),
    answer: str = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """提交面试答案"""
    try:
        from document_service.generators import interview

        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="文档不存在")
        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.INTERVIEW_403_MESSAGE, message="无权访问此文档")

        trimmed_answer = (answer or "").strip()
        compact_answer = re.sub(r"\s+", "", trimmed_answer)

        normalized_session_id = (session_id or "").strip()
        if not normalized_session_id:
            raise_http_error(status_code=400, code=ErrorCode.INTERVIEW_400_MESSAGE, message="会话ID不能为空")

        if len(set(compact_answer)) < 10:
            raise_http_error(status_code=400, code=ErrorCode.INTERVIEW_400_MESSAGE, message="回答内容重复度较高，请补充更多具体观点")
        
        if interview.interview_manager is None:
            raise Exception("面试管理器未初始化")
        
        # 提交答案并获取结果
        result = await interview.interview_manager.submit_answer(
            normalized_session_id,
            answer,
            db,
            expected_user_id=current_user.id,
            expected_document_id=document_id,
        )

        await invalidate_document_history(current_user.id, document_id, names=["interview"])
        
        return {
            "success": True,
            **result
        }
    except ValueError as e:
        raise_http_error(status_code=400, code=ErrorCode.INTERVIEW_400_EXCEPTION, message="请求失败", details=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 提交答案失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="提交答案失败", details=str(e))


@router.post("/api/document/{document_id}/interview/abort", tags=["面试功能"])
async def abort_interview(
    document_id: int,
    session_id: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """中止面试"""
    try:
        from document_service.generators import interview

        logger.info(f"🔍 收到中止面试请求: document_id={document_id}, session_id={session_id}")

        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="文档不存在")
        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.INTERVIEW_403_MESSAGE, message="无权访问此文档")
        
        if interview.interview_manager is None:
            raise Exception("面试管理器未初始化")
        
        await interview.interview_manager.abort_interview(
            session_id,
            db,
            expected_user_id=current_user.id,
            expected_document_id=document_id,
        )

        await invalidate_document_history(current_user.id, document_id, names=["interview"])
        
        return {
            "success": True,
            "message": "面试已中止"
        }
    except ValueError as e:
        raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 中止面试失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="中止面试失败", details=str(e))


@router.get("/api/document/{document_id}/interview/history", tags=["面试功能"])
async def get_interview_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的面试历史记录"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "interview")
        if cached:
            return cached

        # 查询该文档的所有面试记录
        result = await db.execute(
            select(db_models.InterviewRecord).filter(
                db_models.InterviewRecord.document_id == document_id,
                db_models.InterviewRecord.user_id == current_user.id
            ).order_by(db_models.InterviewRecord.created_at.desc())
        )
        records = result.scalars().all()
        
        # 转换为返回格式
        history = []
        for record in records:
            history.append({
                "id": record.id,
                "session_id": record.session_id,
                "title": record.title or '面试记录',  # 添加标题字段
                "total_rounds": record.total_rounds,
                "average_score": record.average_score,
                "status": record.status,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None
            })
        
        response = {
            "success": True,
            "history": history
        }
        await set_document_history_cache(current_user.id, document_id, "interview", response, ttl=120)
        return response
    except Exception as e:
        logger.error(f"❌ 获取面试历史失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="获取面试历史失败", details=str(e))


@router.get("/api/interview/{record_id}/detail", tags=["面试功能"])
async def get_interview_detail(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取面试详情"""
    try:
        # 查询面试记录
        result = await db.execute(
            select(db_models.InterviewRecord).filter(
                db_models.InterviewRecord.id == record_id,
                db_models.InterviewRecord.user_id == current_user.id
            )
        )
        record = result.scalar_one_or_none()
        
        if not record:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="面试记录不存在")
        
        return {
            "success": True,
            "record": {
                "id": record.id,
                "session_id": record.session_id,
                "document_id": record.document_id,
                "total_rounds": record.total_rounds,
                "average_score": record.average_score,
                "status": record.status,
                "qa_history": record.qa_history,
                "summary": record.summary,
                "knowledge_points": record.knowledge_points,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "completed_at": record.completed_at.isoformat() if record.completed_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取面试详情失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="获取面试详情失败", details=str(e))


@router.post("/api/interview/{record_id}/resume", tags=["面试功能"])
async def resume_interview(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """恢复中止的面试"""
    try:
        from document_service.generators import interview
        from document_service.vector_store import vector_store_manager
        
        if interview.interview_manager is None:
            raise Exception("面试管理器未初始化")
        
        # 查询面试记录
        result = await db.execute(
            select(db_models.InterviewRecord).filter(
                db_models.InterviewRecord.id == record_id,
                db_models.InterviewRecord.user_id == current_user.id
            )
        )
        record = result.scalar_one_or_none()
        
        if not record:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="面试记录不存在")
        
        if record.status != "aborted":
            raise_http_error(status_code=400, code=ErrorCode.INTERVIEW_400_MESSAGE, message="只能恢复已中止的面试")
        
        # 获取文档
        service = get_document_service(db)
        document = await service.get_document_by_id(record.document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="文档不存在")
        
        # 恢复面试
        session_id, next_question_data = await interview.interview_manager.resume_interview(
            record_id,
            document,
            vector_store_manager,
            db
        )
        
        return {
            "success": True,
            "session_id": session_id,
            "next_question": next_question_data,
            "previous_qa": record.qa_history  # 返回之前的问答历史
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 恢复面试失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="恢复面试失败", details=str(e))


@router.put("/api/interview/{record_id}/rename", tags=["面试功能"])
async def rename_interview_record(
    record_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名面试记录"""
    try:
        # 查询面试记录
        result = await db.execute(
            select(db_models.InterviewRecord).filter(
                db_models.InterviewRecord.id == record_id,
                db_models.InterviewRecord.user_id == current_user.id
            )
        )
        record = result.scalar_one_or_none()
        
        if not record:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="面试记录不存在")
        
        # 更新标题
        record.title = title
        await db.commit()
        await invalidate_document_history(current_user.id, record.document_id, names=["interview"])
        
        return {"success": True, "message": "重命名成功"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 重命名面试记录失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="重命名失败", details=str(e))


@router.delete("/api/interview/{record_id}", tags=["面试功能"])
async def delete_interview_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除面试记录"""
    try:
        # 查询面试记录
        result = await db.execute(
            select(db_models.InterviewRecord).filter(
                db_models.InterviewRecord.id == record_id,
                db_models.InterviewRecord.user_id == current_user.id
            )
        )
        record = result.scalar_one_or_none()
        
        if not record:
            raise_http_error(status_code=404, code=ErrorCode.INTERVIEW_404_MESSAGE, message="面试记录不存在")
        
        # 删除记录
        doc_id = record.document_id
        await db.delete(record)
        await db.commit()

        await invalidate_document_history(current_user.id, doc_id, names=["interview"])
        
        logger.info(f"✅ 面试记录已删除: id={record_id}")
        
        return {
            "success": True,
            "message": "删除成功"
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除面试记录失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.INTERVIEW_500_EXCEPTION, message="删除失败", details=str(e))
