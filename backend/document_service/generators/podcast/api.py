
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from pydantic import BaseModel

from database import get_db
from auth import get_current_user
from db_models import User, Document, DocumentPodcast
from .generator import PodcastGenerator
from langchain_openai import ChatOpenAI
from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/podcast", tags=["podcast"])

# <audio> 元素无法携带 Authorization 头，音频地址里附带短期签名令牌来鉴权
PODCAST_AUDIO_TOKEN_TYPE = "podcast_audio"
PODCAST_AUDIO_TOKEN_TTL = timedelta(hours=1)


def create_podcast_audio_token(user_id: int, task_id: int) -> str:
    """签发播客音频访问令牌（JWT，绑定用户与播客任务，1 小时有效）"""
    from jose import jwt

    payload = {
        "type": PODCAST_AUDIO_TOKEN_TYPE,
        "sub": str(user_id),
        "task_id": int(task_id),
        "exp": datetime.now(timezone.utc) + PODCAST_AUDIO_TOKEN_TTL,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def verify_podcast_audio_token(token: Optional[str], task_id: int) -> int:
    """校验音频令牌（签名 / 过期 / 类型 / 任务 ID），返回令牌所属用户 ID"""
    from jose import JWTError, jwt

    if not token:
        raise HTTPException(status_code=401, detail="缺少音频访问令牌，请刷新页面后重试")
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise HTTPException(status_code=401, detail="音频链接已失效，请刷新页面后重试")
    if payload.get("type") != PODCAST_AUDIO_TOKEN_TYPE or payload.get("task_id") != int(task_id):
        raise HTTPException(status_code=403, detail="无权访问该音频")
    try:
        return int(payload.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=403, detail="无权访问该音频")


def build_podcast_audio_url(user_id: int, task_id: int) -> str:
    if settings.is_local_mode:
        # 本地版单用户、免登录，不需要令牌
        return f"/api/podcast/audio/{task_id}"
    return f"/api/podcast/audio/{task_id}?token={create_podcast_audio_token(user_id, task_id)}"


class PodcastRequest(BaseModel):
    document_id: int
    title: Optional[str] = "AI Podcast"
    style: str = "fun" # fun, serious, summary
    language: str = "zh"

class PodcastResponse(BaseModel):
    task_id: int
    status: str
    message: str

@router.post("/generate", response_model=PodcastResponse)
async def generate_podcast(
    req: PodcastRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 1. Check document
    from runtime.features import require_capability
    require_capability("podcast")
    result = await db.execute(select(Document).where(Document.id == req.document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    # Check and Deduct Credits
    from credit_service import CreditService
    credit_service = CreditService(db)
    try:
        await credit_service.deduct_for_podcast(current_user.id, doc.id)
    except Exception as e:
        raise HTTPException(status_code=402, detail=f"Insufficient credits or payment failed: {str(e)}")

    # 2. Create Task Record
    podcast = DocumentPodcast(
        document_id=req.document_id,
        user_id=current_user.id,
        title=req.title,
        style=req.style,
        status="pending",
        progress=0,
        current_stage="pending"
    )
    db.add(podcast)
    await db.commit()
    await db.refresh(podcast)
    
    # 3. Trigger Celery Task (Async)
    from tasks.podcast_tasks import generate_podcast_task
    try:
        generate_podcast_task.delay(podcast.id, doc.original_text, req.style, req.language)
    except Exception as e:
        # 已扣费但任务没能入队（Broker 不可用 / 本地队列已满）：退款并把记录标记失败。
        # reference_id 与任务内退款一致（记录 ID），任务侧的幂等检查可避免重复退款。
        logger.error(f"❌ 播客任务提交失败: {e}")
        from credit_service import CreditCost

        try:
            await credit_service.refund_credits(
                user_id=current_user.id,
                amount=CreditCost.PODCAST,
                original_transaction_type="podcast",
                reason="播客任务提交失败",
                reference_id=str(podcast.id),
            )
            podcast.status = "failed"
            podcast.error_message = "任务提交失败，积分已退还，请稍后重试"
            await db.commit()
        except Exception as refund_error:
            await db.rollback()
            logger.error(f"❌ 播客任务提交失败后退款失败: {refund_error}")
        raise HTTPException(status_code=503, detail="播客任务提交失败，积分已退还，请稍后重试")

    return PodcastResponse(task_id=podcast.id, status="pending", message="Podcast generation started (Async)")


@router.get("/status/{task_id}")
async def get_podcast_status(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(DocumentPodcast).where(DocumentPodcast.id == task_id))
    podcast = result.scalar_one_or_none()
    
    if not podcast:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if podcast.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")

    storage_type = None
    if podcast.audio_file_path:
        storage_type = "cos" if podcast.audio_file_path.startswith(("http://", "https://")) else "local"

    cos_fallback_reason = None
    if podcast.status == "completed" and podcast.current_stage == "completed_local_fallback":
        cos_fallback_reason = podcast.error_message
        
    return {
        "id": podcast.id,
        "status": podcast.status,
        "progress": podcast.progress,
        "current_stage": podcast.current_stage,
        "script": podcast.script,
        # 前端直接使用该地址（SaaS 下带短期签名令牌，供 <audio> 鉴权）
        "audio_url": build_podcast_audio_url(current_user.id, task_id) if podcast.status == "completed" else None,
        "error": podcast.error_message,
        "storage_type": storage_type,
        "cos_fallback_reason": cos_fallback_reason,
    }

@router.get("/audio/{task_id}")
async def get_podcast_audio(
    task_id: int,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # <audio> 元素无法携带 Authorization 头：鉴权改用 /status 返回的 audio_url 中的短期签名令牌。
    # 本地版单用户、免登录，跳过令牌校验。
    token_user_id = None if settings.is_local_mode else verify_podcast_audio_token(token, task_id)

    result = await db.execute(select(DocumentPodcast).where(DocumentPodcast.id == task_id))
    podcast = result.scalar_one_or_none()

    if not podcast or not podcast.audio_file_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    if token_user_id is not None and podcast.user_id != token_user_id:
        raise HTTPException(status_code=404, detail="Audio not found")

    if podcast.audio_file_path.startswith("http://") or podcast.audio_file_path.startswith("https://"):
        return RedirectResponse(url=podcast.audio_file_path, status_code=307)
        
    import os
    if not os.path.exists(podcast.audio_file_path):
        raise HTTPException(status_code=404, detail="File missing on server")
        
    return FileResponse(podcast.audio_file_path, media_type="audio/mpeg", filename=f"podcast_{task_id}.mp3")

@router.get("/list/{document_id}")
async def list_document_podcasts(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(DocumentPodcast)
        .where(DocumentPodcast.document_id == document_id)
        .where(DocumentPodcast.user_id == current_user.id)
        .order_by(DocumentPodcast.created_at.desc())
    )
    podcasts = result.scalars().all()
    return podcasts


class RenameRequest(BaseModel):
    title: str

@router.put("/{task_id}/rename")
async def rename_podcast(
    task_id: int,
    req: RenameRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import update
    # Verify ownership
    result = await db.execute(select(DocumentPodcast).where(DocumentPodcast.id == task_id))
    podcast = result.scalar_one_or_none()
    
    if not podcast:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if podcast.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    # Update title
    podcast.title = req.title.strip()
    await db.commit()
    await db.refresh(podcast)
    
    return {"success": True, "title": podcast.title}


@router.delete("/{task_id}")
async def delete_podcast(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import delete
    # Verify ownership
    result = await db.execute(select(DocumentPodcast).where(DocumentPodcast.id == task_id))
    podcast = result.scalar_one_or_none()
    
    if not podcast:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if podcast.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    # Delete
    await db.delete(podcast)
    await db.commit()
    
    # Optional: Delete audio file asynchronously or here?
    # For simplicity, we just delete the record. Cleanup of files can be done by a periodic job 
    # or we can try to delete it here if it exists.
    if podcast.audio_file_path:
        import os
        try:
            is_remote = podcast.audio_file_path.startswith("http://") or podcast.audio_file_path.startswith("https://")
            if is_remote:
                from urllib.parse import urlparse
                from utils.cos_service import COSService

                parsed = urlparse(podcast.audio_file_path)
                key = parsed.path.lstrip('/')
                if key:
                    cos = COSService()
                    cos.delete_file(key)
            elif os.path.exists(podcast.audio_file_path):
                os.remove(podcast.audio_file_path)
        except Exception:
            pass # Ignore file deletion errors
            
    return {"success": True, "message": "Podcast deleted"}
