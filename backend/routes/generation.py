"""
generation routes – 闪卡 / 测验 / 思维导图 / 重点提炼 / 利弊分析 / 通俗示例 / 节点扩展 / Celery 任务状态
"""
from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Dict, List, Optional as Opt

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from database import get_db
from document_service import get_document_service
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from cache_service import (
    get_document_data_cache,
    set_document_data_cache,
    get_document_history_cache,
    set_document_history_cache,
    invalidate_document_history,
)
from routes._helpers import generic_rename_resource, generic_delete_resource
from routes._idempotency import begin_idempotency, commit_idempotency, rollback_idempotency
from utils.logger import log_info
from utils.refunds import one_off_refund_reference
from config import settings
from cache_service import get_redis_client

logger = logging.getLogger(__name__)

router = APIRouter()


async def _ensure_queue_capacity(queue_name: str, max_pending: int) -> None:
    """队列舱壁：当队列积压过高时拒绝新任务，避免慢任务拖垮系统。"""
    client = await get_redis_client()
    if not client:
        return

    try:
        pending = await client.llen(queue_name)
        if pending >= max_pending:
            raise HTTPException(
                status_code=429,
                detail=f"系统繁忙（{queue_name}队列积压 {pending}），请稍后再试",
            )
    except HTTPException:
        raise
    except Exception:
        # Redis 不可用时降级放行，不影响主流程
        return


async def _refund_generation_charge(
    db: AsyncSession,
    credit_service,
    user_id: int,
    amount: int,
    credit_type: str,
    document_id: int,
    reason: str,
) -> None:
    """扣费后任务提交 / 生成失败时退还积分（与 interview.py 的处理方式一致）"""
    try:
        await credit_service.refund_credits(
            user_id=user_id,
            amount=amount,
            original_transaction_type=credit_type,
            reason=reason,
            reference_id=one_off_refund_reference(document_id),
        )
        await db.commit()
    except Exception as refund_error:
        await db.rollback()
        logger.error(f"❌ {credit_type} 退款失败: {refund_error}")


async def _generate_text_or_refund(
    db: AsyncSession,
    credit_service,
    user_id: int,
    document_id: int,
    prompt: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """通过统一模型工厂调用 LLM；失败时退还已扣的积分（deduct_for_highlights）并返回 500"""
    from langchain_core.messages import HumanMessage
    from llm_service import get_llm_for_feature
    from credit_service import CreditCost

    try:
        llm = get_llm_for_feature("doc_general", temperature=temperature, max_tokens=max_tokens)
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = response.content if isinstance(response.content, str) else str(response.content or "")
        if not content.strip():
            raise ValueError("模型返回内容为空")
        return content
    except Exception as e:
        logger.error(f"❌ 生成失败，退还积分: {e}")
        await _refund_generation_charge(
            db, credit_service, user_id, CreditCost.HIGHLIGHTS, "highlights", document_id, "生成失败退款"
        )
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="生成失败，积分已退还", details=str(e))


class FlashcardsGenerateRequest(BaseModel):
    card_count: Opt[int] = None
    difficulty: Opt[str] = None
    requirements: Opt[str] = None


class QuizGenerateRequest(BaseModel):
    question_count: Opt[int] = None
    difficulty: Opt[str] = None
    requirements: Opt[str] = None


class HighlightsGenerateRequest(BaseModel):
    point_count: Opt[int] = None
    difficulty: Opt[str] = None
    requirements: Opt[str] = None


class ReportGenerateRequest(BaseModel):
    report_type: Opt[str] = None
    custom_style: Opt[str] = None
    focus_points: Opt[str] = None
    requirements: Opt[str] = None



@router.get("/api/generation-task/{task_id}/status", tags=["异步任务"])
async def get_generation_task_status(
    task_id: str,
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    查询 Celery 生成任务状态
    
    返回：
    - status: pending | processing | completed | failed
    - result: 任务结果（completed 时）
    - error: 错误信息（failed 时）
    """
    if settings.is_local_mode:
        from runtime.tasks import get_local_task_result

        result = get_local_task_result(task_id)
        if result is None:
            raise HTTPException(status_code=404, detail="任务不存在")
    else:
        from celery.result import AsyncResult
        from celery_app import celery_app

        result = AsyncResult(task_id, app=celery_app)
    
    status_mapping = {
        'PENDING': 'pending',
        'STARTED': 'processing',
        'PROGRESS': 'processing',
        'SUCCESS': 'completed',
        'FAILURE': 'failed',
        'REVOKED': 'failed',
        'INTERRUPTED': 'failed',
        'RETRY': 'processing',
    }
    
    status = status_mapping.get(result.status, 'pending')
    
    response = {
        "task_id": task_id,
        "status": status,
    }
    
    if result.ready():
        if result.successful():
            task_result = result.result
            response["success"] = True
            
            # 提取实际数据（任务返回格式: {success, record_id, data: {flashcards/quiz/...}}）
            if isinstance(task_result, dict):
                # 面试任务直接返回 session_id 和 first_question
                if "session_id" in task_result:
                    response["session_id"] = task_result["session_id"]
                    response["first_question"] = task_result.get("first_question")
                    response["data"] = task_result
                else:
                    data = task_result.get("data", task_result)
                    
                    # 进一步提取：如果 data 是 {flashcards: [...]} 格式，提取数组
                    if isinstance(data, dict):
                        if "flashcards" in data:
                            response["data"] = data["flashcards"]
                        elif "quiz" in data:
                            response["data"] = data["quiz"]
                        elif "root" in data:
                            # 思维导图保持原格式
                            response["data"] = data
                        else:
                            response["data"] = data
                    else:
                        response["data"] = data
                    
                    if "record_id" in task_result:
                        response["record_id"] = task_result["record_id"]
        else:
            response["success"] = False
            response["error"] = str(result.result) if result.result else "任务执行失败"
    else:
        response["success"] = None

    if isinstance(result.info, dict):
        meta = result.info
        if "progress" in meta:
            response["progress"] = meta.get("progress")
        if "stage" in meta:
            response["stage"] = meta.get("stage")
        if "current_section" in meta:
            response["current_section"] = meta.get("current_section")
        if "total_sections" in meta:
            response["total_sections"] = meta.get("total_sections")
    
    return response

@router.post("/api/document/{document_id}/generate-flashcards", tags=["文档学习"])
async def generate_document_flashcards(
    document_id: int,
    body: Opt[FlashcardsGenerateRequest] = Body(None),
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    基于文档生成学习闪卡（异步版本）
    
    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_flashcards:{document_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_heavy", settings.celery_generation_heavy_queue_max_pending)

        # 验证文档存在
        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        # 🆕 积分检查和扣费
        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_flashcard(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="请求失败", details=e.message)

        # 提交 Celery 任务
        from tasks.generation_tasks import generate_flashcards
        try:
            task = generate_flashcards.apply_async(
                kwargs={
                    "document_id": document_id,
                    "user_id": current_user.id,
                    "card_count": body.card_count if body else None,
                    "difficulty": body.difficulty if body else None,
                    "requirements": body.requirements if body else None,
                },
                queue="generation_heavy",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.FLASHCARD, "flashcard", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        log_info(f"✅ 闪卡生成任务已提交: task_id={task.id}")

        response_payload = {
            "success": True,
            "message": "闪卡生成任务已提交",
            "task_id": task.id,
            "task_type": "flashcards",
            "credits_deducted": CreditCost.FLASHCARD,
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise

@router.post("/api/document/{document_id}/generate-quiz", tags=["文档学习"])
async def generate_document_quiz(
    document_id: int,
    body: Opt[QuizGenerateRequest] = Body(None),
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    基于文档生成测验题目（异步版本）
    
    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_quiz:{document_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_heavy", settings.celery_generation_heavy_queue_max_pending)

        # 验证文档存在
        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        # 🆕 积分检查和扣费
        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_quiz(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="请求失败", details=e.message)

        # 提交 Celery 任务
        from tasks.generation_tasks import generate_quiz
        try:
            task = generate_quiz.apply_async(
                kwargs={
                    "document_id": document_id,
                    "user_id": current_user.id,
                    "question_count": body.question_count if body else None,
                    "difficulty": body.difficulty if body else None,
                    "requirements": body.requirements if body else None,
                },
                queue="generation_heavy",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.QUIZ, "quiz", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        log_info(f"✅ 测验生成任务已提交: task_id={task.id}")

        response_payload = {
            "success": True,
            "message": "测验生成任务已提交",
            "task_id": task.id,
            "task_type": "quiz",
            "credits_deducted": CreditCost.QUIZ,
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise

@router.post("/api/document/{document_id}/generate-mindmap", tags=["文档学习"])
async def generate_document_mindmap(
    document_id: int,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    基于文档生成思维导图（异步版本）
    
    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_mindmap:{document_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_heavy", settings.celery_generation_heavy_queue_max_pending)

        # 验证文档存在
        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        # 🆕 积分检查和扣费
        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_mindmap(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="请求失败", details=e.message)

        # 提交 Celery 任务
        from tasks.generation_tasks import generate_mindmap
        try:
            task = generate_mindmap.apply_async(
                kwargs={
                    "document_id": document_id,
                    "user_id": current_user.id,
                },
                queue="generation_heavy",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.MINDMAP, "mindmap", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        log_info(f"✅ 思维导图生成任务已提交: task_id={task.id}")

        response_payload = {
            "success": True,
            "message": "思维导图生成任务已提交",
            "task_id": task.id,
            "task_type": "mindmap",
            "credits_deducted": CreditCost.MINDMAP,
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise

@router.post("/api/document/{document_id}/expand-mindmap-node", tags=["文档学习"])
async def expand_mindmap_node(
    document_id: int,
    request: dict,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    扩展思维导图的特定节点
    
    请求体：
    {
        "node_id": "要扩展的节点ID",
        "current_mindmap": {...}  // 当前完整的思维导图数据
    }
    """
    try:
        service = get_document_service(db)
        
        # 获取文档
        document = await service.get_document_by_id(document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")
        
        # 检查权限
        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")
        
        # 获取请求参数
        node_id = request.get("node_id")
        current_mindmap = request.get("current_mindmap")
        
        if not node_id:
            raise_http_error(status_code=400, code=ErrorCode.GENERATION_400_MESSAGE, message="缺少 node_id 参数")
        if not current_mindmap:
            raise_http_error(status_code=400, code=ErrorCode.GENERATION_400_MESSAGE, message="缺少 current_mindmap 参数")
        
        # 扩展节点
        new_nodes = await service.expand_mindmap_node(
            document=document,
            node_id=node_id,
            current_mindmap=current_mindmap,
            user_id=current_user.id
        )
        
        return {
            "success": True,
            "new_nodes": new_nodes,
            "message": f"成功为节点 {node_id} 生成了 {len(new_nodes)} 个子节点"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 扩展思维导图节点失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="请求失败", details=str(e))

@router.get("/api/document/{document_id}/mindmap-history", tags=["文档学习"])
async def get_mindmap_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的思维导图生成历史（来自 DocumentMindmap）。"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "mindmap")
        if cached:
            return cached

        result = await db.execute(
            select(db_models.DocumentMindmap)
            .filter(
                db_models.DocumentMindmap.document_id == document_id,
                db_models.DocumentMindmap.user_id == current_user.id
            )
            .order_by(db_models.DocumentMindmap.created_at.desc())
        )
        records = result.scalars().all()

        def count_nodes(node: Dict[str, Any]) -> int:
            total = 1
            for child in node.get("children", []) or []:
                total += count_nodes(child)
            return total
        
        history = []
        for record in records:
            # 增强的 JSON 解析逻辑，处理可能的字符串格式
            mindmap_data = record.mindmap_data
            if isinstance(mindmap_data, str):
                try:
                    mindmap_data = json.loads(mindmap_data)
                except:
                    mindmap_data = {}
            
            if not isinstance(mindmap_data, dict):
                mindmap_data = {}

            node_count = record.node_count
            if not node_count and mindmap_data:
                root = mindmap_data.get("root", {})
                node_count = count_nodes(root) if isinstance(root, dict) else 0

            history.append({
                "id": record.id,
                "title": record.title, # 修复：使用数据库中的实际标题，而非硬编码
                "node_count": node_count,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "mindmap_data": mindmap_data
            })

        response = {"success": True, "data": history}
        await set_document_history_cache(current_user.id, document_id, "mindmap", response, ttl=120)
        return response
        
    except Exception as e:
        logger.error(f"❌ 获取思维导图历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.get("/api/document/{document_id}/quiz-history", tags=["文档学习"])
async def get_quiz_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的测验生成历史（来自 DocumentQuiz）。"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "quiz")
        if cached:
            return cached

        result = await db.execute(
            select(db_models.DocumentQuiz)
            .filter(
                db_models.DocumentQuiz.document_id == document_id,
                db_models.DocumentQuiz.user_id == current_user.id
            )
            .order_by(db_models.DocumentQuiz.created_at.desc())
        )
        records = result.scalars().all()
        
        history = []
        for record in records:
            raw_quiz = record.quiz_data
            if isinstance(raw_quiz, list):
                quiz_list = raw_quiz
            elif isinstance(raw_quiz, dict):
                candidate = raw_quiz.get("quiz")
                quiz_list = candidate if isinstance(candidate, list) else []
            else:
                quiz_list = []

            question_count = record.question_count or len(quiz_list)
            
            history.append({
                "id": record.id,
                "title": record.title or "测验",
                "question_count": question_count,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None
            })
        
        response = {"success": True, "data": history}
        await set_document_history_cache(current_user.id, document_id, "quiz", response, ttl=120)
        return response
        
    except Exception as e:
        logger.error(f"❌ 获取测验历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.post("/api/document/{document_id}/generate-pros-cons", tags=["文档学习"])
async def generate_pros_cons(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    基于文档生成优点与缺点分析（第三方独立视角）
    """
    try:
        service = get_document_service(db)
        
        # 获取文档
        document = await service.get_document_by_id(document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_highlights(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="积分不足", details=e.message)
        
        # 构建批判性分析的prompt
        prompt = f"""请以第三方独立评审专家的视角，批判性地分析以下文档内容。

文档标题：{document.filename}
文档内容：
{document.original_text[:3000]}

请从独立、客观、批判的角度，列出：

## 优点排行（Top 5）
1. [第一个优点] - 详细说明
2. [第二个优点] - 详细说明
3. [第三个优点] - 详细说明
4. [第四个优点] - 详细说明
5. [第五个优点] - 详细说明

## 缺点排行（Top 5）
1. [第一个缺点] - 详细说明
2. [第二个缺点] - 详细说明
3. [第三个缺点] - 详细说明
4. [第四个缺点] - 详细说明
5. [第五个缺点] - 详细说明

注意：
- 扮演黑白脸角色，既要肯定优点，也要毫不留情地指出缺点
- 不要参考作者自己在文档中提到的优缺点
- 从逻辑严密性、论证充分性、实用价值、创新性、可操作性等维度分析
- 每个点都要具体、有建设性"""

        # 调用AI生成分析
        # 通过统一模型工厂调用（DocumentService 已无 .client）；失败时退还积分
        analysis_text = await _generate_text_or_refund(
            db, credit_service, current_user.id, document_id, prompt, temperature=0.7, max_tokens=2000
        )
        
        # 创建笔记并保存
        note = db_models.Note(
            user_id=current_user.id,
            document_id=document_id,
            title="优缺点分析",
            content=analysis_text,
            type='pros_cons'
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        
        logger.info(f"✅ 优缺点分析生成成功并保存为笔记: {note.id}")
        
        return {
            "success": True,
            "message": "优缺点分析已生成并保存到笔记",
            "data": {
                "note_id": note.id,
                "content": analysis_text
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 生成优缺点分析失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="生成失败", details=str(e))

@router.post("/api/document/{document_id}/generate-simple-example", tags=["文档学习"])
async def generate_simple_example(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    基于文档生成通俗易懂的例子，用小白能理解的方式解释核心方法/流程
    """
    try:
        service = get_document_service(db)
        
        # 获取文档
        document = await service.get_document_by_id(document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_highlights(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="积分不足", details=e.message)
        
        # 构建通俗例子的prompt
        prompt = f"""你是一位擅长用简单例子讲解复杂概念的老师。请用一个通俗易懂的例子，给小白解释以下文档的核心方法/流程。

文档标题：{document.filename}
文档内容：
{document.original_text[:3000]}

请按以下结构输出：

## 核心方法/流程是什么？
用1-2句话总结文档的核心方法或流程。

## 通俗例子
用一个日常生活中的具体例子来类比说明：
- 例子要贴近生活，让完全不懂的人也能理解
- 要把文档中的抽象概念映射到例子的具体元素上
- 要说明每个步骤在例子中对应什么

## 回到文档
把例子中的概念对应回文档，说明文档中的关键步骤/概念：
1. 第一个关键点
2. 第二个关键点
3. 第三个关键点
（以此类推）

## 小白总结
用最简单的话，一句话总结这个文档想要教什么。

注意：
- 例子要真的很简单，像给5岁小孩解释一样
- 避免使用专业术语，如果必须用要立刻解释
- 例子要有趣、生动、容易记住"""

        # 调用AI生成通俗例子
        # 通过统一模型工厂调用（DocumentService 已无 .client）；失败时退还积分
        example_text = await _generate_text_or_refund(
            db, credit_service, current_user.id, document_id, prompt, temperature=0.8, max_tokens=2500
        )
        
        # 创建笔记并保存
        note = db_models.Note(
            user_id=current_user.id,
            document_id=document_id,
            title="通俗举例子",
            content=example_text,
            type='simple_example'
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)
        
        logger.info(f"✅ 通俗例子生成成功并保存为笔记: {note.id}")
        
        return {
            "success": True,
            "message": "通俗例子已生成并保存到笔记",
            "data": {
                "note_id": note.id,
                "content": example_text
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 生成通俗例子失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="生成失败", details=str(e))

@router.get("/api/document/{document_id}/quiz/{quiz_id}", tags=["文档学习"])
async def get_quiz_by_id(
    document_id: int,
    quiz_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取特定版本的测验"""
    try:
        record = await db.get(db_models.DocumentQuiz, quiz_id)
        if not record or record.document_id != document_id or record.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="测验不存在")

        raw_quiz = record.quiz_data
        if isinstance(raw_quiz, list):
            quiz_items = raw_quiz
        elif isinstance(raw_quiz, dict):
            quiz_items = raw_quiz.get("quiz")
            if not isinstance(quiz_items, list):
                quiz_items = []
        else:
            quiz_items = []

        return {"success": True, "data": quiz_items}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取测验失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.get("/api/document/{document_id}/flowchart-history", tags=["文档学习"])
async def get_flowchart_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的学习路径生成历史（来自 DocumentLearningPath）。"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "flowchart")
        if cached:
            return cached

        result = await db.execute(
            select(db_models.DocumentLearningPath)
            .filter(
                db_models.DocumentLearningPath.document_id == document_id,
                db_models.DocumentLearningPath.user_id == current_user.id
            )
            .order_by(db_models.DocumentLearningPath.created_at.desc())
        )
        records = result.scalars().all()
        
        history = []
        for record in records:
            path_data = record.learning_path_data if isinstance(record.learning_path_data, dict) else {}
            node_count = record.node_count or len(path_data.get("nodes", []))
            
            history.append({
                "id": record.id,
                "title": record.title or "学习路径",
                "node_count": node_count,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "learning_path_data": path_data
            })
        
        response = {"success": True, "data": history}
        await set_document_history_cache(current_user.id, document_id, "flowchart", response, ttl=120)
        return response
    except Exception as e:
        logger.error(f"❌ 获取流程图历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.get("/api/document/{document_id}/flashcards-history", tags=["文档学习"])
async def get_flashcards_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的闪卡生成历史（来自 DocumentFlashcards）。"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "flashcards")
        if cached:
            return cached

        result = await db.execute(
            select(db_models.DocumentFlashcards)
            .filter(
                db_models.DocumentFlashcards.document_id == document_id,
                db_models.DocumentFlashcards.user_id == current_user.id
            )
            .order_by(db_models.DocumentFlashcards.created_at.desc())
        )
        records = result.scalars().all()
        
        history = []
        for record in records:
            raw_flashcards = record.flashcards_data
            if isinstance(raw_flashcards, list):
                flashcards_list = raw_flashcards
            elif isinstance(raw_flashcards, dict):
                candidate = raw_flashcards.get("flashcards")
                flashcards_list = candidate if isinstance(candidate, list) else []
            else:
                flashcards_list = []

            card_count = record.card_count or len(flashcards_list)
            
            history.append({
                "id": record.id,
                "title": record.title or "闪卡",
                "card_count": card_count,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None
            })
        
        response = {"success": True, "data": history}
        await set_document_history_cache(current_user.id, document_id, "flashcards", response, ttl=120)
        return response
    except Exception as e:
        logger.error(f"❌ 获取闪卡历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.get("/api/document/{document_id}/flowchart/{flowchart_id}", tags=["文档学习"])
async def get_flowchart_by_id(
    document_id: int,
    flowchart_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """根据ID获取特定版本的流程图"""
    try:
        record = await db.get(db_models.DocumentLearningPath, flowchart_id)
        if not record or record.document_id != document_id or record.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="流程图不存在")
        
        data = record.learning_path_data if isinstance(record.learning_path_data, dict) else {}
        
        return {"success": True, "data": data}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取流程图失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.get("/api/document/{document_id}/flashcards/{flashcards_id}", tags=["文档学习"])
async def get_flashcards_by_id(
    document_id: int,
    flashcards_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """根据ID获取特定版本的闪卡"""
    try:
        record = await db.get(db_models.DocumentFlashcards, flashcards_id)
        if not record or record.document_id != document_id or record.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="闪卡不存在")

        raw_flashcards = record.flashcards_data
        if isinstance(raw_flashcards, list):
            flashcards_list = raw_flashcards
        elif isinstance(raw_flashcards, dict):
            candidate = raw_flashcards.get("flashcards")
            flashcards_list = candidate if isinstance(candidate, list) else []
        else:
            flashcards_list = []

        return {"success": True, "data": flashcards_list}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取闪卡失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.delete("/api/document/{document_id}/flowchart/{flowchart_id}", tags=["历史记录管理"])
async def delete_flowchart(
    document_id: int,
    flowchart_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除流程图历史记录"""
    try:
        return await generic_delete_resource(
            db=db, model_cls=db_models.DocumentLearningPath, resource_id=flowchart_id,
            document_id=document_id, user_id=current_user.id,
            cache_names=["flowchart"], resource_label="流程图",
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除流程图失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="删除失败", details=str(e))

@router.put("/api/document/{document_id}/flowchart/{flowchart_id}/rename", tags=["历史记录管理"])
async def rename_flowchart(
    document_id: int,
    flowchart_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名流程图"""
    return await generic_rename_resource(
        db=db,
        model_cls=db_models.DocumentLearningPath,
        resource_id=flowchart_id,
        document_id=document_id,
        user_id=current_user.id,
        new_title=title,
        cache_names=["flowchart"]
    )

@router.delete("/api/document/{document_id}/flashcards/{flashcards_id}", tags=["历史记录管理"])
async def delete_flashcards(
    document_id: int,
    flashcards_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除闪卡历史记录"""
    try:
        return await generic_delete_resource(
            db=db, model_cls=db_models.DocumentFlashcards, resource_id=flashcards_id,
            document_id=document_id, user_id=current_user.id,
            cache_names=["flashcards"], resource_label="闪卡",
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除闪卡失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="删除失败", details=str(e))

@router.put("/api/document/{document_id}/flashcards/{flashcards_id}/rename", tags=["历史记录管理"])
async def rename_flashcards(
    document_id: int,
    flashcards_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名闪卡"""
    return await generic_rename_resource(
        db=db,
        model_cls=db_models.DocumentFlashcards,
        resource_id=flashcards_id,
        document_id=document_id,
        user_id=current_user.id,
        new_title=title,
        cache_names=["flashcards"]
    )

@router.delete("/api/document/{document_id}/quiz/{quiz_id}", tags=["历史记录管理"])
async def delete_quiz(
    document_id: int,
    quiz_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除测验历史记录"""
    try:
        return await generic_delete_resource(
            db=db, model_cls=db_models.DocumentQuiz, resource_id=quiz_id,
            document_id=document_id, user_id=current_user.id,
            cache_names=["quiz"], resource_label="测验",
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除测验失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="删除失败", details=str(e))

@router.delete("/api/document/{document_id}/mindmap/{mindmap_id}", tags=["历史记录管理"])
async def delete_mindmap(
    document_id: int,
    mindmap_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除思维导图历史记录"""
    try:
        return await generic_delete_resource(
            db=db, model_cls=db_models.DocumentMindmap, resource_id=mindmap_id,
            document_id=document_id, user_id=current_user.id,
            cache_names=["mindmap"], resource_label="思维导图",
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除思维导图失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="删除失败", details=str(e))

@router.put("/api/document/{document_id}/mindmap/{mindmap_id}/rename", tags=["历史记录管理"])
async def rename_mindmap(
    document_id: int,
    mindmap_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名思维导图"""
    def transform_mindmap_json(data: Dict[str, Any], new_title: str) -> Dict[str, Any]:
        # 保持与原有逻辑一致，同时更新顶层 title 和 root.title
        data["title"] = new_title
        # 更新 root.title (前端显示的中心主题)
        if "root" in data and isinstance(data["root"], dict):
            data["root"]["title"] = new_title
        return data

    return await generic_rename_resource(
        db=db,
        model_cls=db_models.DocumentMindmap,
        resource_id=mindmap_id,
        document_id=document_id,
        user_id=current_user.id,
        new_title=title,
        cache_names=["mindmap"],
        json_field_name="mindmap_data",
        json_transformer=transform_mindmap_json
    )

@router.post("/api/document/{document_id}/generate-highlights", tags=["文档学习"])
async def generate_document_highlights(
    document_id: int,
    body: Opt[HighlightsGenerateRequest] = Body(None),
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    生成重点提炼（异步版本）
    
    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_highlights:{document_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_heavy", settings.celery_generation_heavy_queue_max_pending)

        # 验证文档存在
        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        # 🆕 积分检查和扣费
        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_highlights(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="请求失败", details=e.message)

        # 提交 Celery 任务
        from tasks.generation_tasks import generate_highlights
        try:
            task = generate_highlights.apply_async(
                kwargs={
                    "document_id": document_id,
                    "user_id": current_user.id,
                    "point_count": body.point_count if body else None,
                    "difficulty": body.difficulty if body else None,
                    "requirements": body.requirements if body else None,
                },
                queue="generation_heavy",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.HIGHLIGHTS, "highlights", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        log_info(f"✅ 重点提炼任务已提交: task_id={task.id}")

        response_payload = {
            "success": True,
            "message": "重点提炼任务已提交",
            "task_id": task.id,
            "task_type": "highlights",
            "credits_deducted": CreditCost.HIGHLIGHTS,
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise


@router.post("/api/document/{document_id}/generate-report", tags=["文档学习"])
async def generate_document_report(
    document_id: int,
    body: Opt[ReportGenerateRequest] = Body(None),
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    生成长报告（异步版本）

    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_report:{document_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_heavy", settings.celery_generation_heavy_queue_max_pending)

        document = await db.get(db_models.Document, document_id)
        if not document:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在")

        if document.user_id != current_user.id:
            raise_http_error(status_code=403, code=ErrorCode.GENERATION_403_MESSAGE, message="无权访问此文档")

        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_report(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="请求失败", details=e.message)

        from tasks.generation_tasks import generate_report
        try:
            task = generate_report.apply_async(
                kwargs={
                    "document_id": document_id,
                    "user_id": current_user.id,
                    "report_type": body.report_type if body else None,
                    "custom_style": body.custom_style if body else None,
                    "focus_points": body.focus_points if body else None,
                    "requirements": body.requirements if body else None,
                },
                queue="generation_heavy",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.REPORT, "report", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        log_info(f"✅ 长报告生成任务已提交: task_id={task.id}")

        response_payload = {
            "success": True,
            "message": "长报告生成任务已提交",
            "task_id": task.id,
            "task_type": "report",
            "credits_deducted": CreditCost.REPORT,
        }
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise

# ============== 对话上下文生成（闪卡/测验/提炼） ==============

from pydantic import BaseModel as _BaseModel

class _ChatGenRequest(_BaseModel):
    session_id: str

@router.post("/api/document/{document_id}/generate-chat-flashcards", tags=["文档学习"])
async def generate_chat_flashcards(
    document_id: int,
    body: _ChatGenRequest,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """基于当前对话记录生成闪卡"""
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_chat_flashcards:{document_id}:{body.session_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_chat", settings.celery_generation_chat_queue_max_pending)

        document = await db.get(db_models.Document, document_id)
        if not document or document.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在或无权访问")

        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_flashcard(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="积分不足", details=e.message)

        from tasks.generation_tasks import generate_chat_flashcards as _task
        try:
            task = _task.apply_async(
                kwargs={"document_id": document_id, "user_id": current_user.id, "session_id": body.session_id},
                queue="generation_chat",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.FLASHCARD, "flashcard", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        response_payload = {"success": True, "message": "对话闪卡生成任务已提交", "task_id": task.id, "task_type": "flashcards"}
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise


@router.post("/api/document/{document_id}/generate-chat-quiz", tags=["文档学习"])
async def generate_chat_quiz(
    document_id: int,
    body: _ChatGenRequest,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """基于当前对话记录生成测验"""
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_chat_quiz:{document_id}:{body.session_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_chat", settings.celery_generation_chat_queue_max_pending)

        document = await db.get(db_models.Document, document_id)
        if not document or document.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在或无权访问")

        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_quiz(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="积分不足", details=e.message)

        from tasks.generation_tasks import generate_chat_quiz as _task
        try:
            task = _task.apply_async(
                kwargs={"document_id": document_id, "user_id": current_user.id, "session_id": body.session_id},
                queue="generation_chat",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.QUIZ, "quiz", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        response_payload = {"success": True, "message": "对话测验生成任务已提交", "task_id": task.id, "task_type": "quiz"}
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise


@router.post("/api/document/{document_id}/generate-chat-highlights", tags=["文档学习"])
async def generate_chat_highlights(
    document_id: int,
    body: _ChatGenRequest,
    http_request: Request = None,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """基于当前对话记录生成重点提炼"""
    idempotency_ctx = None
    try:
        idempotency_ctx = await begin_idempotency(
            idempotency_key=http_request.headers.get("Idempotency-Key") if http_request else None,
            user_id=current_user.id,
            scope=f"generate_chat_highlights:{document_id}:{body.session_id}",
        )
        if idempotency_ctx.replay_response is not None:
            return idempotency_ctx.replay_response

        await _ensure_queue_capacity("generation_chat", settings.celery_generation_chat_queue_max_pending)

        document = await db.get(db_models.Document, document_id)
        if not document or document.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="文档不存在或无权访问")

        from credit_service import CreditService, CreditCost, InsufficientCreditsError
        credit_service = CreditService(db)
        try:
            await credit_service.deduct_for_highlights(current_user.id, document_id)
            await db.commit()
        except InsufficientCreditsError as e:
            raise_http_error(status_code=402, code=ErrorCode.GENERATION_402_EXCEPTION, message="积分不足", details=e.message)

        from tasks.generation_tasks import generate_chat_highlights as _task
        try:
            task = _task.apply_async(
                kwargs={"document_id": document_id, "user_id": current_user.id, "session_id": body.session_id},
                queue="generation_chat",
                expires=settings.celery_task_expires_seconds,
            )
        except Exception as e:
            # 任务提交失败：退还已扣积分
            await _refund_generation_charge(db, credit_service, current_user.id, CreditCost.HIGHLIGHTS, "highlights", document_id, "任务提交失败退款")
            if isinstance(e, HTTPException):
                raise
            raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="任务提交失败，积分已退还", details=str(e))

        response_payload = {"success": True, "message": "对话提炼生成任务已提交", "task_id": task.id, "task_type": "highlights"}
        return await commit_idempotency(idempotency_ctx, response_payload)
    except HTTPException:
        await rollback_idempotency(idempotency_ctx)
        raise
    except Exception:
        await rollback_idempotency(idempotency_ctx)
        raise


@router.get("/api/document/{document_id}/highlights-history", tags=["文档学习"])
async def get_highlights_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的重点提炼历史（来自 DocumentHighlights）。"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "highlights")
        if cached:
            return cached

        result = await db.execute(
            select(db_models.DocumentHighlights).filter(
                db_models.DocumentHighlights.document_id == document_id,
                db_models.DocumentHighlights.user_id == current_user.id
            ).order_by(db_models.DocumentHighlights.created_at.desc())
        )
        records = result.scalars().all()
        
        history = []
        for record in records:
            highlights_data = record.highlights_data
            point_count = 0
            if isinstance(highlights_data, dict):
                for key in ['key_points', 'innovations', 'limitations', 'applications']:
                    if key in highlights_data and isinstance(highlights_data[key], list):
                        point_count += len(highlights_data[key])
                
            history.append({
                "id": record.id,
                "title": record.title or "重点提炼",
                "point_count": point_count,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None,
                "highlights_data": highlights_data
            })
        
        response = {
            "success": True,
            "data": history
        }
        await set_document_history_cache(current_user.id, document_id, "highlights", response, ttl=120)
        return response
        
    except Exception as e:
        logger.error(f"❌ 获取重点提炼历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))


@router.get("/api/document/{document_id}/report-history", tags=["文档学习"])
async def get_report_history(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取文档的长报告历史（来自 DocumentReport）。"""
    try:
        cached = await get_document_history_cache(current_user.id, document_id, "report")
        if cached:
            return cached

        result = await db.execute(
            select(db_models.DocumentReport)
            .filter(
                db_models.DocumentReport.document_id == document_id,
                db_models.DocumentReport.user_id == current_user.id
            )
            .order_by(db_models.DocumentReport.created_at.desc())
        )
        records = result.scalars().all()

        history = []
        for record in records:
            word_count = record.word_count
            if not word_count and isinstance(record.report_markdown, str):
                word_count = len(record.report_markdown.strip())

            history.append({
                "id": record.id,
                "title": record.title or "长报告",
                "word_count": word_count or 0,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            })

        response = {"success": True, "data": history}
        await set_document_history_cache(current_user.id, document_id, "report", response, ttl=120)
        return response
    except Exception as e:
        logger.error(f"❌ 获取长报告历史失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))


@router.get("/api/document/{document_id}/report/{report_id}", tags=["文档学习"])
async def get_report_by_id(
    document_id: int,
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取特定版本的长报告"""
    try:
        cache_key = f"report:{report_id}"
        cached = await get_document_data_cache(current_user.id, document_id, cache_key)
        if cached:
            return cached

        record = await db.get(db_models.DocumentReport, report_id)
        if not record or record.document_id != document_id or record.user_id != current_user.id:
            raise_http_error(status_code=404, code=ErrorCode.GENERATION_404_MESSAGE, message="长报告不存在")

        response = {
            "success": True,
            "data": {
                "id": record.id,
                "title": record.title,
                "report_markdown": record.report_markdown,
                "report_data": record.report_data,
                "word_count": record.word_count,
                "version": record.version,
                "is_current": record.is_current,
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }
        }
        await set_document_data_cache(current_user.id, document_id, cache_key, response, ttl=180)
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ 获取长报告失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="获取失败", details=str(e))

@router.delete("/api/document/{document_id}/highlights/{highlights_id}", tags=["历史记录管理"])
async def delete_highlights(
    document_id: int,
    highlights_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除重点提炼历史记录"""
    try:
        return await generic_delete_resource(
            db=db, model_cls=db_models.DocumentHighlights, resource_id=highlights_id,
            document_id=document_id, user_id=current_user.id,
            cache_names=["highlights"], resource_label="重点提炼",
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除重点提炼失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="删除失败", details=str(e))


@router.delete("/api/document/{document_id}/report/{report_id}", tags=["历史记录管理"])
async def delete_report(
    document_id: int,
    report_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除长报告历史记录"""
    try:
        return await generic_delete_resource(
            db=db, model_cls=db_models.DocumentReport, resource_id=report_id,
            document_id=document_id, user_id=current_user.id,
            cache_names=["report"], resource_label="长报告",
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"❌ 删除长报告失败: {e}")
        raise_http_error(status_code=500, code=ErrorCode.GENERATION_500_EXCEPTION, message="删除失败", details=str(e))

@router.put("/api/document/{document_id}/highlights/{highlights_id}/rename", tags=["历史记录管理"])
async def rename_highlights(
    document_id: int,
    highlights_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名重点提炼"""
    return await generic_rename_resource(
        db=db,
        model_cls=db_models.DocumentHighlights,
        resource_id=highlights_id,
        document_id=document_id,
        user_id=current_user.id,
        new_title=title,
        cache_names=["highlights"]
    )


@router.put("/api/document/{document_id}/report/{report_id}/rename", tags=["历史记录管理"])
async def rename_report(
    document_id: int,
    report_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名长报告"""
    return await generic_rename_resource(
        db=db,
        model_cls=db_models.DocumentReport,
        resource_id=report_id,
        document_id=document_id,
        user_id=current_user.id,
        new_title=title,
        cache_names=["report"]
    )

@router.put("/api/document/{document_id}/quiz/{quiz_id}/rename", tags=["历史记录管理"])
async def rename_quiz(
    document_id: int,
    quiz_id: int,
    title: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """重命名测验"""
    return await generic_rename_resource(
        db=db,
        model_cls=db_models.DocumentQuiz,
        resource_id=quiz_id,
        document_id=document_id,
        user_id=current_user.id,
        new_title=title,
        cache_names=["quiz"]
    )
