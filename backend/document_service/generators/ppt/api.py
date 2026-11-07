from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from database import get_db
from auth import get_current_user
from db_models import User, Document
from .service import create_ppt_task_with_upload, run_ppt_generation_task, get_ppt_status, OUTPUT_DIR
from . import service # Changed from .service to . to access service functions
from fastapi.responses import FileResponse
from pathlib import Path # Added import for Path
from utils.logger import get_logger
from utils.refunds import one_off_refund_reference
logger = get_logger(__name__)

router = APIRouter(prefix="/api/ppt", tags=["PPT Generation"])

@router.get("/eligibility")
async def check_eligibility(
    document_id: Optional[int] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if user can generate PPT"""
    return await service.check_ppt_eligibility(db, current_user.id, document_id)


@router.post("/generate")
async def generate_ppt(
    file: UploadFile = File(None),
    title: str = Form(None),
    style: str = Form("academic"),
    language: str = Form("zh"),
    mode: str = Form("paper"),
    output_type: str = Form("slides"), # NEW: slides | poster
    document_id: int = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    background_tasks: BackgroundTasks = BackgroundTasks()
):
    """
    Generate PPT from uploaded PDF file OR existing document ID.
    Enforces credit limit. (已移除 VIP 和数量限制)
    """
    from runtime.features import require_capability
    from config import settings
    require_capability("ppt")

    # 先校验，再扣费：参数错误 / 文档不存在或无权访问 / 文件过大时不应扣积分
    if not file and not document_id:
        raise HTTPException(status_code=400, detail="Must provide either a file or a document_id")

    if file:
        if file.size is not None and file.size > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(
                status_code=413,
                detail=f"文件过大：单个文件不能超过 {settings.max_upload_mb} MB，请压缩或拆分后重试",
            )
    else:
        document = await db.get(Document, document_id)
        if not document or document.user_id != current_user.id:
            raise HTTPException(status_code=404, detail="Document not found")
        if not document.original_text:
            raise HTTPException(status_code=400, detail="Document has no content")

    # 🆕 积分检查和扣费
    from credit_service import CreditService, CreditCost, InsufficientCreditsError
    credit_service = CreditService(db)
    try:
        await credit_service.deduct_for_ppt(current_user.id, document_id or 0)
        await db.commit()
    except InsufficientCreditsError as e:
        raise HTTPException(status_code=402, detail=e.message)

    try:
        if file:
            # Generate from Upload
            if not title:
                title = file.filename

            task = await service.create_ppt_task_with_upload(
                file=file,
                user_id=current_user.id,
                title=title,
                style=style,
                language=language,
                mode=mode,
                output_type=output_type, # Pass new param
                db=db,
                background_tasks=background_tasks
            )
        else:
            # Generate from Document ID
            task = await service.create_ppt_task_from_document(
                document_id=document_id,
                user_id=current_user.id,
                title=title, # might be None, service handles it default
                style=style,
                language=language,
                mode=mode,
                output_type=output_type, # Pass new param
                db=db,
                background_tasks=background_tasks
            )
    except Exception as e:
        # 任务未能创建 / 提交：退还已扣积分
        logger.error(f"❌ PPT 任务创建失败，退还积分: {e}")
        await db.rollback()
        try:
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=CreditCost.PPT,
                original_transaction_type="ppt",
                reason="PPT任务创建失败退款",
                reference_id=one_off_refund_reference(document_id or 0),
            )
            await db.commit()
        except Exception as refund_error:
            await db.rollback()
            logger.error(f"❌ PPT 任务创建失败后退款失败: {refund_error}")
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail="PPT 任务创建失败，积分已退还，请稍后重试")

    return {"task_id": task.id, "status": "pending", "message": "PPT generation started", "credits_deducted": CreditCost.PPT}

from pydantic import BaseModel

class RenameRequest(BaseModel):
    title: str

@router.put("/rename/{task_id}")
async def rename_ppt(
    task_id: int,
    request: RenameRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    updated = await service.rename_ppt_task(db, task_id, current_user.id, request.title)
    if not updated:
        raise HTTPException(status_code=404, detail="PPT task not found or unauthorized")
    return {"message": "success", "title": updated.title}

@router.get("/status/{task_id}")
async def get_task_status(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get status of PPT generation task.
    Includes stale task detection: auto-fails tasks stuck for >30 min.
    """
    ppt = await get_ppt_status(db, task_id, current_user.id)
    
    # 🆕 超时检测：如果任务处于 processing/pending 超过 30 分钟，自动标记失败
    if ppt.status in ('processing', 'pending') and ppt.created_at:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        created = ppt.created_at.replace(tzinfo=timezone.utc) if ppt.created_at.tzinfo is None else ppt.created_at
        elapsed_minutes = (now - created).total_seconds() / 60
        if elapsed_minutes > 30:
            ppt.status = 'failed'
            ppt.error_message = f'任务超时：已运行 {int(elapsed_minutes)} 分钟未完成，可能是后台服务异常，请重试'
            await db.commit()
            logger.info(f"⏰ [Timeout] Task {task_id} auto-failed after {int(elapsed_minutes)} min")
    
    response = {
        "id": ppt.id,
        "status": ppt.status,
        "progress": ppt.progress,
        "current_stage": ppt.current_stage,
        "error": ppt.error_message,
        "preview_images": ppt.preview_images if ppt.status == 'completed' else [],
        "ppt_url": f"/api/ppt/download/{task_id}" if ppt.status == 'completed' and ppt.ppt_file_path else None
    }
    logger.info(f"📤 [API Response] Task {task_id}: status={ppt.status}, preview_images_count={len(ppt.preview_images) if ppt.preview_images else 0}")
    return response

@router.get("/download/{task_id}")
async def download_ppt(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user) # Security check
):
    """
    Download PPT resources (Images + PDF) as a ZIP file.
    """
    # Generate the ZIP package (this handles downloading from COS and Zipping)
    zip_path = await service.generate_ppt_zip(db, task_id, current_user.id)
    
    if not zip_path or not Path(zip_path).exists():
        raise HTTPException(status_code=404, detail="Failed to prepare download package")
    
    filename = f"ppt_package_{task_id}.zip"
         
    return FileResponse(
        path=zip_path,
        filename=filename,
        media_type="application/zip"
    )

@router.get("/document/{document_id}/history")
async def get_ppt_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get history of PPTs generated for a document."""
    history = await service.get_ppt_history_by_doc(db, document_id, current_user.id)
    return {"success": True, "data": history}

@router.get("/detail/{ppt_id}")
async def get_ppt_detail(
    ppt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get detailed info about a PPT task (including image URLs)."""
    ppt = await service.get_ppt_detail(db, ppt_id, current_user.id)
    if not ppt:
        raise HTTPException(status_code=404, detail="PPT not found")
    return {"success": True, "data": ppt}

@router.delete("/task/{ppt_id}")
async def delete_ppt_task(
    ppt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete a PPT task."""
    success = await service.delete_ppt(db, ppt_id, current_user.id)
    if not success:
        raise HTTPException(status_code=404, detail="PPT task not found or not owned by user")
    return {"success": True}

@router.post("/cancel-all")
async def cancel_all_tasks(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Cancel all active PPT tasks for the current user."""
    count = await service.cancel_all_ppt_tasks(db, current_user.id)
    return {"success": True, "cancelled_count": count}
