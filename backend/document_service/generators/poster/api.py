from fastapi import APIRouter, Depends, HTTPException, Form, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from typing import Optional
from database import get_db
from db_models import User, DocumentPoster, Document
from .models import PosterResponse
from .service import create_poster_task, run_poster_pipeline, sync_poster_status
from utils.logger import get_logger
from utils.refunds import one_off_refund_reference
logger = get_logger(__name__)

router = APIRouter(prefix="/api/poster", tags=["poster"])

from auth import get_current_user


@router.post("/generate", response_model=PosterResponse)
async def generate_poster(
    document_id: int = Form(...),
    title: str = Form(None),
    style: str = Form("academic"),
    density: str = Form("medium"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Generate a single-page Academic Poster from a document (已移除 VIP 和数量限制)."""
    # VIP Check 已移除
    from runtime.features import require_capability
    require_capability("poster")

    # 先校验文档（存在且属于当前用户），再扣费：校验失败不应扣积分
    doc = await db.execute(select(Document).where(Document.id == document_id))
    doc = doc.scalar_one_or_none()
    if not doc or doc.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    # 🆕 积分检查和扣费
    from credit_service import CreditService, CreditCost, InsufficientCreditsError
    credit_service = CreditService(db)
    try:
        await credit_service.deduct_for_poster(current_user.id, document_id)
        await db.commit()
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=e.message)

    task_id = None
    try:
        # Create Task
        task = await create_poster_task(
            db=db,
            user_id=current_user.id,
            document_id=document_id,
            title=title,
            style=style,
            density=density
        )
        task_id = task.id

        # 🚀 使用 Celery 任务替代 asyncio.create_task
        from tasks.ppt_tasks import generate_poster
        generate_poster.delay(task_id)
    except Exception as e:
        # 任务未能提交：退还已扣积分，并把已建的记录标记失败（避免永远 pending）
        logger.error(f"❌ 海报任务提交失败，退还积分: {e}")
        await db.rollback()
        try:
            if task_id is not None:
                await db.execute(
                    update(DocumentPoster)
                    .where(DocumentPoster.id == task_id)
                    .values(status="failed", error_message="任务提交失败，积分已退还，请稍后重试")
                )
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=CreditCost.POSTER,
                original_transaction_type="poster",
                reason="海报任务提交失败退款",
                # 有任务记录时与任务内退款同 ID（重复会被唯一约束挡住），否则按一次性退款处理
                reference_id=str(task_id) if task_id is not None else one_off_refund_reference(document_id),
            )
            await db.commit()
        except Exception as refund_error:
            await db.rollback()
            logger.error(f"❌ 海报任务提交失败后退款失败: {refund_error}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="海报任务提交失败，积分已退还，请稍后重试")
    logger.info(f"👉 Celery 任务已提交: Poster {task.id}")
    
    # 返回带积分信息
    task.credits_deducted = CreditCost.POSTER
    return task


@router.get("/status/{task_id}", response_model=PosterResponse)
async def get_poster_status(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(DocumentPoster).where(
        DocumentPoster.id == task_id,
        DocumentPoster.user_id == current_user.id
    )
    result = await db.execute(stmt)
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="Poster task not found")
    
    task = await sync_poster_status(db, task)
    
    # 🆕 超时检测：processing/pending 超过 30 分钟自动标记失败
    if task.status in ('processing', 'pending') and task.created_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        created = task.created_at.replace(tzinfo=timezone.utc) if task.created_at.tzinfo is None else task.created_at
        elapsed_minutes = (now - created).total_seconds() / 60
        if elapsed_minutes > 30:
            task.status = 'failed'
            task.error_message = f'任务超时：已运行 {int(elapsed_minutes)} 分钟未完成，请重试'
            await db.commit()
            logger.info(f"⏰ [Timeout] Poster {task_id} auto-failed after {int(elapsed_minutes)} min")
        
    return PosterResponse(
        id=task.id,
        title=task.title,
        status=task.status,
        progress=task.progress,
        poster_url=task.poster_file_path,
        preview_url=task.preview_image,
        error_message=task.error_message,
        created_at=task.created_at
    )


@router.get("/document/{document_id}/history")
async def get_poster_history(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Get all poster generation history for a document."""
    stmt = select(DocumentPoster).where(
        DocumentPoster.document_id == document_id,
        DocumentPoster.user_id == current_user.id
    ).order_by(DocumentPoster.created_at.desc())
    
    result = await db.execute(stmt)
    posters = result.scalars().all()
    
    # 对已完成但缺少 poster_url 的海报，尝试从 COS 恢复
    poster_data = []
    for p in posters:
        if p.status == "completed" and not p.poster_file_path:
            p = await sync_poster_status(db, p)
        
        poster_data.append({
            "id": p.id,
            "title": p.title,
            "status": p.status,
            "progress": p.progress,
            "poster_url": p.poster_file_path,
            "preview_url": p.preview_image,
            "error_message": p.error_message,
            "created_at": p.created_at.isoformat() if p.created_at else None
        })
    
    return {
        "success": True,
        "data": poster_data
    }


@router.put("/{poster_id}/rename")
async def rename_poster(
    poster_id: int,
    title: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Rename a poster."""
    stmt = select(DocumentPoster).where(
        DocumentPoster.id == poster_id,
        DocumentPoster.user_id == current_user.id
    )
    result = await db.execute(stmt)
    poster = result.scalar_one_or_none()
    
    if not poster:
        raise HTTPException(status_code=404, detail="Poster not found")
    
    poster.title = title.strip() if title.strip() else "海报"
    await db.commit()
    
    return {"success": True, "message": "重命名成功", "title": poster.title}
