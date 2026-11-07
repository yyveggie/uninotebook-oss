"""
AI创作空间路由

包含: AI智能编辑、快捷操作、获取编辑器内容、草稿管理
"""
import logging
from typing import AsyncIterator, Optional as Opt, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from database import get_db
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from utils.refunds import one_off_refund_reference

logger = logging.getLogger(__name__)
router = APIRouter()

# 单次 AI 编辑的输入上限：按次计费，不设上限的话一次请求就能塞进几十万字
EDITOR_MAX_SELECTION_CHARS = 6000
EDITOR_MAX_INSTRUCTION_CHARS = 500
EDITOR_MAX_CONTEXT_CHARS = 4000
# 流式输出开始后已无法改 HTTP 状态码：中途失败或没有产出时以此标记结尾，前端据此丢弃结果并提示
EDITOR_STREAM_ERROR_MARKER = "\u0000[AI_EDIT_ERROR]"
DRAFT_FILENAME_MAX_CHARS = 255


class EditorRequest(BaseModel):
    selection: str = Field("", max_length=EDITOR_MAX_SELECTION_CHARS)
    instruction: str = Field("", max_length=EDITOR_MAX_INSTRUCTION_CHARS)
    action_key: Opt[str] = None
    context_before: str = Field("", max_length=EDITOR_MAX_CONTEXT_CHARS)
    context_after: str = Field("", max_length=EDITOR_MAX_CONTEXT_CHARS)


async def _refund_editor_charge(user_id: int, reason: str) -> None:
    """AI 编辑失败时退还积分。流式响应开始后请求自带的数据库会话已经关闭，这里用独立会话"""
    from credit_service import CreditService, CreditCost
    from database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await CreditService(session).refund_credits(
                user_id=user_id,
                amount=CreditCost.QA_BASE,
                original_transaction_type="editor",
                reason=f"AI 编辑失败退款：{reason}",
                reference_id=one_off_refund_reference(f"editor-user-{user_id}"),
            )
            await session.commit()
    except Exception:
        logger.exception("❌ [AIEditor] 退款失败 user=%s", user_id)


async def _relay_editor_stream(first_chunk: str, stream: AsyncIterator[str], user_id: int) -> AsyncIterator[str]:
    produced = bool(first_chunk.strip())
    yield first_chunk
    try:
        async for chunk in stream:
            produced = produced or bool(chunk.strip())
            yield chunk
    except Exception:
        logger.exception("❌ [AIEditor] 流式输出中断 user=%s", user_id)
        await _refund_editor_charge(user_id, "输出中断")
        yield EDITOR_STREAM_ERROR_MARKER
        return
    if not produced:
        await _refund_editor_charge(user_id, "模型未返回内容")
        yield EDITOR_STREAM_ERROR_MARKER


@router.get("/api/editor/quick-actions", tags=["AI创作"])
async def get_quick_actions(
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """获取所有可用的快捷操作列表"""
    from document_service.editor import AIEditorService
    return {"success": True, "data": AIEditorService.get_quick_actions()}


@router.post("/api/editor/ai-edit", tags=["AI创作"])
async def ai_edit(
    request: EditorRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """AI 智能编辑（流式输出）"""
    from document_service.editor import AIEditorService, CONTINUE_ACTION
    from credit_service import CreditService, CreditCost, InsufficientCreditsError

    if not request.selection and not request.instruction and not request.action_key:
        raise_http_error(status_code=400, code=ErrorCode.GENERATION_400_MESSAGE, message="请选择文本或输入指令")
    if request.action_key == CONTINUE_ACTION and not (request.selection.strip() or request.context_before.strip()):
        raise_http_error(status_code=400, code=ErrorCode.GENERATION_400_MESSAGE, message="没有可以续写的内容")

    credit_service = CreditService(db)
    try:
        await credit_service.deduct_credits(
            user_id=current_user.id,
            amount=CreditCost.QA_BASE,
            transaction_type="editor",
            description="AI 编辑",
        )
        await db.commit()
    except InsufficientCreditsError as e:
        raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="积分不足", details=e.message)

    stream = AIEditorService().stream_edit(
        selection=request.selection,
        instruction=request.instruction,
        context_before=request.context_before,
        context_after=request.context_after,
        action_key=request.action_key,
    )
    # 拿到第一段输出再开始响应：鉴权失败、模型不可用、超时等大多发生在这之前，
    # 这样能退款并返回真正的错误，而不是把报错文字当成编辑结果
    try:
        first_chunk = await stream.__anext__()
    except StopAsyncIteration:
        await _refund_editor_charge(current_user.id, "模型未返回内容")
        raise_http_error(status_code=502, code=ErrorCode.EDITOR_502_EXCEPTION, message="AI 没有返回内容，请重试")
    except Exception as exc:
        logger.exception("❌ [AIEditor] 调用模型失败 user=%s", current_user.id)
        await _refund_editor_charge(current_user.id, "调用模型失败")
        # 线上版会只记日志、不把服务商原始报错回传给用户（见 raise_http_error）
        raise_http_error(status_code=502, code=ErrorCode.EDITOR_502_EXCEPTION, message="AI 编辑失败，请稍后重试", details=str(exc))

    return StreamingResponse(
        _relay_editor_stream(first_chunk, stream, current_user.id),
        media_type="text/plain",
    )


@router.get("/api/editor/document/{document_id}/content", tags=["AI创作"])
async def get_editor_content(
    document_id: int,
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """获取编辑器内容"""
    from document_service.editor import AIEditorService
    service = AIEditorService()
    return await service.get_document_content(document_id, current_user.id)


# ==================== 草稿管理 ====================


def _clean_draft_filename(value: str) -> str:
    """文件名只用于展示和下载：去掉换行等控制字符和首尾空白，不能为空"""
    name = "".join(ch for ch in value if ch.isprintable()).strip()
    if not name:
        raise ValueError("文件名不能为空")
    return name


class DraftCreateRequest(BaseModel):
    filename: str = "未命名文档.md"
    content: str = ""
    source_type: str = "local"
    source_document_id: Opt[int] = None

    @field_validator("filename")
    @classmethod
    def _check_filename(cls, value: str) -> str:
        # 导入、上传时的名称来自已有文件，过长直接截断（数据库列为 255）
        return _clean_draft_filename(value)[:DRAFT_FILENAME_MAX_CHARS]


class DraftUpdateRequest(BaseModel):
    filename: Opt[str] = None
    content: Opt[str] = None

    @field_validator("filename")
    @classmethod
    def _check_filename(cls, value: Opt[str]) -> Opt[str]:
        if value is None:
            return None
        name = _clean_draft_filename(value)
        if len(name) > DRAFT_FILENAME_MAX_CHARS:
            raise ValueError(f"文件名最多 {DRAFT_FILENAME_MAX_CHARS} 个字符")
        return name


@router.get("/api/editor/drafts", tags=["AI创作"])
async def list_drafts(
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """获取用户的所有草稿"""
    result = await db.execute(
        select(db_models.StudioDraft)
        .where(db_models.StudioDraft.user_id == current_user.id)
        .order_by(db_models.StudioDraft.updated_at.desc())
    )
    drafts = result.scalars().all()
    return {
        "success": True,
        "data": [
            {
                "id": d.id,
                "filename": d.filename,
                "content": d.content,
                "source_type": d.source_type,
                "source_document_id": d.source_document_id,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in drafts
        ],
    }


@router.post("/api/editor/drafts", tags=["AI创作"])
async def create_draft(
    req: DraftCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """创建新草稿"""
    draft = db_models.StudioDraft(
        user_id=current_user.id,
        filename=req.filename,
        content=req.content,
        source_type=req.source_type,
        source_document_id=req.source_document_id,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return {
        "success": True,
        "data": {
            "id": draft.id,
            "filename": draft.filename,
            "content": draft.content,
            "source_type": draft.source_type,
            "source_document_id": draft.source_document_id,
            "created_at": draft.created_at.isoformat() if draft.created_at else None,
            "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
        },
    }


@router.put("/api/editor/drafts/{draft_id}", tags=["AI创作"])
async def update_draft(
    draft_id: int,
    req: DraftUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """更新草稿内容"""
    result = await db.execute(
        select(db_models.StudioDraft).where(
            db_models.StudioDraft.id == draft_id,
            db_models.StudioDraft.user_id == current_user.id,
        )
    )
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")

    if req.filename is not None:
        draft.filename = req.filename
    if req.content is not None:
        draft.content = req.content

    await db.commit()
    await db.refresh(draft)
    return {
        "success": True,
        "data": {
            "id": draft.id,
            "filename": draft.filename,
            "updated_at": draft.updated_at.isoformat() if draft.updated_at else None,
        },
    }


@router.delete("/api/editor/drafts/{draft_id}", tags=["AI创作"])
async def delete_draft(
    draft_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    """删除草稿"""
    result = await db.execute(
        select(db_models.StudioDraft).where(
            db_models.StudioDraft.id == draft_id,
            db_models.StudioDraft.user_id == current_user.id,
        )
    )
    draft = result.scalar_one_or_none()
    if not draft:
        raise HTTPException(status_code=404, detail="草稿不存在")

    await db.delete(draft)
    await db.commit()
    return {"success": True}
