"""
文档对话路由

包含: 文档问答(流式/非流式)、会话管理、聊天历史
"""
import json
from typing import Optional, List, Dict

from fastapi import APIRouter, HTTPException, Depends, Form, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from database import get_db
from utils.logger import log_info, get_logger
from document_service import get_document_service
from routes.error_codes import ErrorCode
from cache_service import (
    get_chat_history_cache,
    set_chat_history_cache,
    invalidate_chat_history_cache,
)
from ._errors import raise_http_error
from ._idempotency import begin_idempotency, commit_idempotency, rollback_idempotency

logger = get_logger(__name__)

router = APIRouter()


def _build_first_message_title(question: str, max_chars: int = 20) -> str:
    text = (question or "").strip()
    if not text:
        return "新对话"
    normalized = " ".join(text.split())
    return normalized[:max_chars] or "新对话"


async def _maybe_promote_session_title(
    db: AsyncSession,
    user_id: int,
    document_id: int,
    session_id: Optional[str],
    question: str,
) -> None:
    """若会话仍是占位标题（新对话），在首条消息时自动改为问题前20字。"""
    if not session_id:
        return

    try:
        result = await db.execute(
            select(db_models.DocumentChatHistory)
            .filter(
                db_models.DocumentChatHistory.document_id == document_id,
                db_models.DocumentChatHistory.user_id == user_id,
                db_models.DocumentChatHistory.session_id == session_id,
            )
            .order_by(db_models.DocumentChatHistory.created_at.asc())
            .limit(1)
        )
        first_record = result.scalar_one_or_none()
        if not first_record:
            return

        first_question = (first_record.question or "").strip()
        first_answer = (first_record.answer or "").strip()

        # 仅处理占位会话，避免覆盖用户手动命名或已有真实标题
        if first_answer == "会话已创建。" and first_question.startswith("新对话"):
            first_record.question = _build_first_message_title(question)
            await db.commit()
    except Exception:
        await db.rollback()


@router.post("/api/document/{document_id}/chat", tags=["文档学习"])
async def chat_with_document(
    document_id: int,
    question: str = Form(...),
    conversation_history: str = Form("[]"),  # JSON 字符串格式的历史对话
    selected_source_ids: str = Form("[]"),  # JSON 字符串格式的来源ID列表
    session_id: str = Form(None),  # 会话ID（可选）
    use_iterative: bool = Form(False),  # 是否使用多轮迭代模式（可选，默认False）
    max_iterations: Optional[int] = Form(None),  # 最大迭代次数（可选，默认由 Agent 根据模式决定）
    is_agent_mode: bool = Form(True),  # 是否启用智能体模式（默认开启）
    stream: bool = Form(False), # 是否流式输出
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    与文档对话（智能RAG + 对话历史）
    
    Args:
        document_id: 文档ID
        question: 当前问题
        conversation_history: JSON格式的对话历史 [{"question": "...", "answer": "..."}]
        session_id: 会话ID（可选）
        use_iterative: 是否使用多轮迭代模式（默认False）
        max_iterations: 最大迭代次数（默认10）
        stream: 是否流式输出 (默认False, 通过 text/event-stream 返回)
    """
    try:
        service = get_document_service(db)
        
        # 🆕 检查积分余额
        from credit_service import CreditService
        from credit_service.constants import CreditCost
        
        credit_service = CreditService(db)
        user_credits = await credit_service.get_user_credits(current_user.id)
        
        # 只要余额大于0即可开始，具体扣费由 QA 动态计算
        # 或者设定最低门槛 QA_BASE / AGENT_QA_BASE
        required_base = CreditCost.AGENT_QA_BASE if is_agent_mode else CreditCost.QA_BASE
        if user_credits.balance < required_base:
             # 如果余额不足起步价，明确拒绝
             mode_name = "智能体模式" if is_agent_mode else "快速模式"
             raise HTTPException(
                 status_code=402, 
                 detail=f"积分不足：{mode_name}需至少 {required_base} 积分，当前余额 {user_credits.balance}"
             )

        # 获取文档
        document = await service.get_document_by_id(document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.CHAT_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.CHAT_404_MESSAGE, message="无权访问此文档")

        # 解析对话历史
        try:
            history = json.loads(conversation_history) if conversation_history else []
        except json.JSONDecodeError:
            history = []

        try:
            source_ids_payload = json.loads(selected_source_ids) if selected_source_ids else []
            if not isinstance(source_ids_payload, list):
                source_ids_payload = []
            selected_source_ids_list = [str(item).strip() for item in source_ids_payload if str(item).strip()]
        except json.JSONDecodeError:
            selected_source_ids_list = []

        # 首条消息自动命名：新对话 -> 问题前20字
        await _maybe_promote_session_title(
            db=db,
            user_id=current_user.id,
            document_id=document_id,
            session_id=session_id,
            question=question,
        )
        
        log_info(f"📜 收到对话历史: {len(history)} 轮")
        if stream:
            log_info(f"🌊 使用流式响应模式: is_agent_mode={is_agent_mode}")
            return StreamingResponse(
                service.ask_document_stream(
                    document=document,
                    question=question,
                    conversation_history=history,
                    user_id=current_user.id,
                    session_id=session_id,
                    max_iterations=max_iterations,
                    db_question=None,
                    is_agent_mode=is_agent_mode,
                    selected_source_ids=selected_source_ids_list,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        if use_iterative:
            log_info(f"🔄 使用多轮迭代模式（最多{max_iterations}轮）")
            result = await service.ask_document(
                document=document,
                question=question,
                conversation_history=history,
                user_id=current_user.id,
                session_id=session_id,
                max_iterations=max_iterations,
                db_question=None,
                is_agent_mode=is_agent_mode,
                selected_source_ids=selected_source_ids_list,
            )
        else:
            log_info(f"⚡ 使用单次检索模式")
            result = await service.ask_document(
                document=document,
                question=question,
                conversation_history=history,
                user_id=current_user.id,
                session_id=session_id,
                db_question=None,
                is_agent_mode=is_agent_mode,
                selected_source_ids=selected_source_ids_list,
            )
        
        
        # 更新文档最后访问时间

        
        # 失效聊天历史缓存（新消息，不清除 memory/summary 缓存）
        await invalidate_chat_history_cache(current_user.id, document_id, session_id, clear_memory=False)
        
        return {
            "success": True,
            "data": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 问答失败: {e}")
        raise_http_error(
            status_code=500,
            code=ErrorCode.CHAT_INTERNAL_ERROR,
            message="问答失败",
            details=str(e),
        )


# 建会话时写入的占位回答（后一条是旧版本的文案）
SESSION_PLACEHOLDER_ANSWERS = ("会话已创建。", "会话已创建，等待您的第一个问题...")


async def _purge_empty_sessions(db: AsyncSession, user_id: int, document_id: int, keep_session_id: str) -> None:
    """
    删除这篇文档下除 keep_session_id 外、一条真实问答都没有的会话。

    每次打开文档或点「新建」都会登记一个占位会话，不清理的话
    历史对话里会堆满空的「新对话」。清理失败不影响当前操作。
    """
    history = db_models.DocumentChatHistory
    scope = (history.document_id == document_id, history.user_id == user_id)
    with_content = select(history.session_id).where(
        *scope, history.session_id.isnot(None), history.answer.notin_(SESSION_PLACEHOLDER_ANSWERS)
    )
    try:
        await db.execute(
            delete(history)
            .where(
                *scope,
                history.session_id != keep_session_id,
                history.answer.in_(SESSION_PLACEHOLDER_ANSWERS),
                history.session_id.notin_(with_content),
            )
            .execution_options(synchronize_session=False)
        )
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.warning(f"⚠️ 清理空会话失败: {exc}")


@router.post("/api/document/{document_id}/sessions", tags=["文档学习"])
async def create_document_chat_session(
    document_id: int,
    payload: dict,
    http_request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    创建新的对话会话
    """
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key"),
            user_id=current_user.id,
            scope=f"chat_session:{document_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        session_id = payload.get('session_id')
        title = payload.get('title', '新对话')
        
        if not session_id:
            raise_http_error(status_code=400, code=ErrorCode.CHAT_400_MESSAGE, message="session_id 是必需的")
        
        # 检查会话是否已存在
        result = await db.execute(
            select(db_models.DocumentChatHistory.id)
            .filter(
                db_models.DocumentChatHistory.document_id == document_id,
                db_models.DocumentChatHistory.user_id == current_user.id,
                db_models.DocumentChatHistory.session_id == session_id
            )
            .limit(1)
        )
        existing_session = result.scalars().first()
        
        if existing_session:
            await _purge_empty_sessions(db, current_user.id, document_id, session_id)
            response_payload = {
                "success": True,
                "message": "会话已存在",
                "data": {"session_id": session_id}
            }
            return await commit_idempotency(idempotency_ctx, response_payload)
        
        # 创建一个占位消息来建立会话记录
        placeholder_message = db_models.DocumentChatHistory(
            user_id=current_user.id,
            document_id=document_id,
            session_id=session_id,
            question=title,
            answer="会话已创建。",
            relevant_chunks=[]
        )
        
        db.add(placeholder_message)
        await db.commit()
        await db.refresh(placeholder_message)
        await _purge_empty_sessions(db, current_user.id, document_id, session_id)
        
        logger.info(f"✅ 新会话已创建: {session_id}")
        
        response_payload = {
            "success": True,
            "message": "会话创建成功",
            "data": {
                "session_id": session_id,
                "message_id": placeholder_message.id
            }
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
        
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception as e:
        await rollback_idempotency(idempotency_ctx)
        logger.error(f"❌ 创建会话失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.CHAT_500_EXCEPTION, message="创建会话失败", details=str(e))


@router.get("/api/document/{document_id}/sessions", tags=["文档学习"])
async def get_document_chat_sessions(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    获取文档的所有对话会话列表
    
    Returns:
        会话列表，每个会话包含：session_id, 第一条消息, 最后活动时间, 消息数量
    """
    try:
        base_filters = (
            db_models.DocumentChatHistory.document_id == document_id,
            db_models.DocumentChatHistory.user_id == current_user.id,
            db_models.DocumentChatHistory.session_id.isnot(None),
        )

        # 子查询1：每个 session 的聚合统计
        session_stats_subq = (
            select(
                db_models.DocumentChatHistory.session_id.label("session_id"),
                func.min(db_models.DocumentChatHistory.created_at).label("first_message_time"),
                func.max(db_models.DocumentChatHistory.created_at).label("last_activity_time"),
                func.count(db_models.DocumentChatHistory.id).label("message_count"),
            )
            .filter(*base_filters)
            .group_by(db_models.DocumentChatHistory.session_id)
            .subquery()
        )

        # 子查询2：每个 session 第一条消息（按 created_at, id 升序确保稳定）
        first_msg_ranked_subq = (
            select(
                db_models.DocumentChatHistory.session_id.label("session_id"),
                db_models.DocumentChatHistory.question.label("question"),
                func.row_number()
                .over(
                    partition_by=db_models.DocumentChatHistory.session_id,
                    order_by=(
                        db_models.DocumentChatHistory.created_at.asc(),
                        db_models.DocumentChatHistory.id.asc(),
                    ),
                )
                .label("row_num"),
            )
            .filter(*base_filters)
            .subquery()
        )

        # 一次查询返回会话列表 + 第一条消息
        result = await db.execute(
            select(
                session_stats_subq.c.session_id,
                session_stats_subq.c.first_message_time,
                session_stats_subq.c.last_activity_time,
                session_stats_subq.c.message_count,
                first_msg_ranked_subq.c.question.label("first_question"),
            )
            .outerjoin(
                first_msg_ranked_subq,
                and_(
                    first_msg_ranked_subq.c.session_id == session_stats_subq.c.session_id,
                    first_msg_ranked_subq.c.row_num == 1,
                ),
            )
            .order_by(session_stats_subq.c.last_activity_time.desc())
        )
        sessions = result.all()
        
        logger.info(f"✅ 找到 {len(sessions)} 个会话")
        
        session_list = []
        for session in sessions:
            session_list.append({
                "session_id": session.session_id,
                "first_question": session.first_question or "未知对话",
                "first_message_time": session.first_message_time.isoformat(),
                "last_activity_time": session.last_activity_time.isoformat(),
                "message_count": session.message_count
            })
        
        logger.info(f"✅ 构建会话列表成功: {len(session_list)} 个会话")
        return {"success": True, "data": session_list}
    except Exception as e:
        logger.error(f"❌ 获取会话列表失败: {e}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.CHAT_500_EXCEPTION, message="获取会话列表失败", details=str(e))


@router.get("/api/document/{document_id}/history", tags=["文档学习"])
async def get_document_chat_history(
    document_id: int,
    session_id: str = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    获取文档的对话历史
    
    Args:
        document_id: 文档ID
        session_id: 可选，指定会话ID
        limit: 返回的历史记录数量（默认50）
    """
    try:
        # 尝试从缓存获取
        cached = await get_chat_history_cache(current_user.id, document_id, session_id)
        if cached:
            return cached
        
        # 查询对话历史
        filters = [
            db_models.DocumentChatHistory.document_id == document_id,
            db_models.DocumentChatHistory.user_id == current_user.id
        ]
        if session_id:
            filters.append(db_models.DocumentChatHistory.session_id == session_id)
        
        result = await db.execute(
            select(db_models.DocumentChatHistory).filter(
                *filters
            ).order_by(db_models.DocumentChatHistory.created_at.asc()).limit(limit)
        )
        history = result.scalars().all()
        
        # 解析历史数据（兼容旧版 List 结构和新版 Dict 结构）
        data_list = []
        for h in history:
            raw_chunks = h.relevant_chunks or []
            sources = []
            flowchart = None
            chart_data = None  # 🆕 数据图表
            status_history = [] # Initialize status_history
            
            if isinstance(raw_chunks, list):
                # 旧版格式：直接是 List[Chunk]
                sources = raw_chunks
            elif isinstance(raw_chunks, dict):
                # 新版格式：{"sources": [], "flowchart": {}, "chart": {}, "status_history": []}
                sources = raw_chunks.get("sources", [])
                flowchart = raw_chunks.get("flowchart")
                chart_data = raw_chunks.get("chart")  # 🆕 读取数据图表
                status_history = raw_chunks.get("status_history", []) # 🆕 读取历史状态
            
            data_list.append({
                "id": h.id,
                "question": h.question,
                "answer": h.answer,
                "sources": sources,
                "flowchart": h.flowchart if hasattr(h, 'flowchart') and h.flowchart else flowchart,  # 优先使用新字段
                "chart_data": h.chart_data if hasattr(h, 'chart_data') and h.chart_data else chart_data,  # 优先使用新字段
                "status_history": h.status_history if hasattr(h, 'status_history') and h.status_history else status_history,  # 优先使用新字段
                "search_count": h.search_count,
                "elapsed_time": h.elapsed_time,
                "created_at": h.created_at.isoformat() if h.created_at else None,
                # 🆕 追问相关字段
                "is_clarification": h.is_clarification if hasattr(h, 'is_clarification') else False,
                "clarification_options": h.clarification_options if hasattr(h, 'clarification_options') else None,
                "selected_option": h.selected_option if hasattr(h, 'selected_option') else None,
                "plan": h.plan if hasattr(h, 'plan') else None,  # 🆕 暴露执行计划 plan
            })

        response = {
            "success": True,
            "data": data_list
        }
        
        # 写入缓存
        await set_chat_history_cache(current_user.id, document_id, session_id, response)
        
        return response
    except Exception as e:
        logger.error(f"❌ 获取历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.CHAT_500_EXCEPTION, message="获取历史失败", details=str(e))


@router.delete("/api/document/{document_id}/history", tags=["文档学习"])
async def clear_document_chat_history(
    document_id: int,
    session_id: str = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    清除文档的对话历史
    
    Args:
        document_id: 文档ID
        session_id: 可选，指定会话ID。如果提供，只删除该会话；否则删除所有历史
    """
    try:
        from sqlalchemy import delete as sa_delete

        # 构建过滤条件
        filters = [
            db_models.DocumentChatHistory.document_id == document_id,
            db_models.DocumentChatHistory.user_id == current_user.id
        ]
        if session_id:
            filters.append(db_models.DocumentChatHistory.session_id == session_id)
        
        # 批量删除（单条 SQL）
        result = await db.execute(
            sa_delete(db_models.DocumentChatHistory).filter(*filters)
        )
        deleted_count = result.rowcount
        await db.commit()
        
        log_msg = f"会话 {session_id}" if session_id else "所有历史"
        log_info(f"🗑️ 已清除文档 {document_id} 的{log_msg}：{deleted_count} 条记录")
        
        # 失效聊天历史缓存
        await invalidate_chat_history_cache(current_user.id, document_id, session_id)
        
        return {
            "success": True,
            "message": f"已清除 {deleted_count} 条对话记录"
        }
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 清除历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.CHAT_500_EXCEPTION, message="清除失败", details=str(e))


@router.delete("/api/document/{document_id}/history/after/{message_id}", tags=["文档学习"])
async def delete_chat_history_after(
    document_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    删除指定消息及其之后的所有对话（用于编辑功能）
    
    Args:
        document_id: 文档ID
        message_id: 消息ID（删除这条消息及其之后的所有消息）
    """
    try:
        # 获取指定消息
        result = await db.execute(
            select(db_models.DocumentChatHistory).filter(
            db_models.DocumentChatHistory.id == message_id,
            db_models.DocumentChatHistory.document_id == document_id,
            db_models.DocumentChatHistory.user_id == current_user.id
            )
        )
        target_message = result.scalar_one_or_none()
        
        if not target_message:
            raise_http_error(status_code=404, code=ErrorCode.CHAT_404_MESSAGE, message="消息不存在")
        
        # 删除该消息及其之后创建的所有消息（在同一个 session 内）
        result = await db.execute(
            select(db_models.DocumentChatHistory).filter(
            db_models.DocumentChatHistory.document_id == document_id,
            db_models.DocumentChatHistory.user_id == current_user.id,
                db_models.DocumentChatHistory.session_id == target_message.session_id,  # 🔥 关键：必须是同一个会话
                db_models.DocumentChatHistory.created_at >= target_message.created_at
            )
        )
        records_to_delete = result.scalars().all()
        
        # 执行删除
        for record in records_to_delete:
            await db.delete(record)
        
        deleted_count = len(records_to_delete)
        
        await db.commit()
        
        log_info(f"✂️ 已删除消息 {message_id} 及其之后的 {deleted_count} 条对话")

        # 🧠 关键修复：编辑截断后，必须同步清除该会话记忆（Redis + PostgreSQL摘要）
        # 否则被删除的后续内容可能仍通过 summary/memory 回灌到下一轮上下文。
        try:
            from document_service.memory import ConversationMemoryManager
            memory_manager = ConversationMemoryManager(db)
            await memory_manager.clear_memory(
                user_id=current_user.id,
                document_id=document_id,
                session_id=target_message.session_id,
            )
        except Exception as mem_err:
            logger.warning(f"⚠️ 清理会话记忆失败（继续返回删除结果）: {mem_err}")
        
        # 🆕 失效缓存，确保前端拉取到最新状态
        await invalidate_chat_history_cache(current_user.id, document_id, target_message.session_id)
        
        return {
            "success": True,
            "message": f"已删除 {deleted_count} 条对话记录"
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.CHAT_500_EXCEPTION, message="删除失败", details=str(e))
