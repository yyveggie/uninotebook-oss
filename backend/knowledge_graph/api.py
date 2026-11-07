from typing import List, Dict, Any, Optional
from datetime import datetime
import json
import time

from fastapi import APIRouter, Depends, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func

from database import get_db, AsyncSessionLocal
import auth
from db_models import Document, User, EurekaChatHistory, EurekaNote
from .models import KnowledgeTerm, DocumentKnowledgeTerm, EurekaMoment, KnowledgeGapAnalysis
from .communities import analyze_term_communities
from .merging import auto_merge_terms, merge_terms as merge_term_pairs
from .utils import (
    TERM_DESCRIPTION_MAX_CHARS,
    TERM_MAX_CHARS,
    _kg_cache_key,
    invalidate_kg_cache,
    format_display_term,
    normalize_term,
    load_term_cooccurrence,
    refresh_terms,
)
from document_service.utils import parse_json_safely  # noqa: F401  兼容旧代码从这里导入
from utils.latex import fix_latex_format
from utils.refunds import one_off_refund_reference
from cache_service import cache_get_bytes, cache_set_bytes
from cache_service import CACHE_TTL
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field, field_validator
from config import settings
from credit_service import CreditService, CreditCost
from credit_service.exceptions import InsufficientCreditsError
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from utils.logger import get_logger
logger = get_logger(__name__)

router = APIRouter(prefix="/api/knowledge-graph", tags=["知识图谱"])

EUREKA_MIN_NODES = 2
EUREKA_MAX_NODES = 6
EUREKA_INNOVATION_TYPES = {"auto", "product", "theory", "perspective", "paper", "metaphor"}
EUREKA_TITLE_MAX_CHARS = 255  # EurekaMoment.title 的列宽
EUREKA_CONTENT_MAX_CHARS = 20000
EUREKA_CHAT_MAX_QUESTION_CHARS = 4000
EUREKA_CHAT_CONTEXT_ROUNDS = 20  # 带给模型的最近对话轮数
EUREKA_HISTORY_MAX_LIMIT = 200
TERM_BATCH_MAX = 500
DOCUMENT_TERMS_MAX = 200


class MergeSuggestion(BaseModel):
    source_id: int = Field(..., description="应该被合并（被删除）的节点ID")
    target_id: int = Field(..., description="保留的目标节点ID")
    reason: str = Field(..., description="合并原因说明")


class MergeAnalysisResult(BaseModel):
    suggestions: List[MergeSuggestion] = Field(default_factory=list, description="合并建议列表")


class EurekaResponse(BaseModel):
    idea: str = Field(..., description="一句话概括这个创新点子（标题），充满洞察力")
    reasoning: str = Field(..., description="详细阐述如何将这些概念连接起来，以及这个点子的价值所在。用激情洋溢的语调。")


class SaveEurekaRequest(BaseModel):
    idea: str = Field(..., max_length=2000)
    reasoning: str = Field(..., max_length=EUREKA_CONTENT_MAX_CHARS)
    source_nodes: List[Dict[str, Any]] = Field(default_factory=list, max_length=20)

    @field_validator("idea", "reasoning")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("内容不能为空")
        return value


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_ids(value, max_items: int, too_many_message: str) -> List[int]:
    if not isinstance(value, list) or not all(_is_id(item) for item in value):
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="节点 ID 必须是整数数组")
    ids = list(dict.fromkeys(value))
    if len(ids) > max_items:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message=too_many_message)
    return ids


async def _get_owned_term(db: AsyncSession, term_id: int, user_id: int) -> KnowledgeTerm:
    term = await db.get(KnowledgeTerm, term_id)
    if term is None or term.user_id != user_id:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="术语不存在或无权访问")
    return term


async def _count_linked_terms(db: AsyncSession, user_id: int, term_ids: Optional[List[int]] = None) -> int:
    """仍被至少一篇文档引用的节点数"""
    dt = DocumentKnowledgeTerm
    query = select(func.count(func.distinct(dt.term_id))).where(dt.user_id == user_id)
    if term_ids is not None:
        query = query.where(dt.term_id.in_(term_ids))
    return int((await db.execute(query)).scalar_one() or 0)


def _public_ai_error(exc: BaseException, fallback: str) -> str:
    """给用户看的模型调用失败原因：线上不带服务商原始报错，本地版保留以便排查"""
    from runtime.features import looks_like_missing_credentials, missing_credential_hint

    if looks_like_missing_credentials(exc):
        return missing_credential_hint()
    if settings.is_local_mode:
        return f"{fallback}（{type(exc).__name__}: {str(exc)[:200]}）"
    return fallback


async def _charge(db: AsyncSession, user_id: int, amount: int, transaction_type: str, description: str) -> int:
    """扣费并立即提交。请求的会话结束时不会自动提交，只 flush 的扣费会被回滚。"""
    if amount <= 0:
        return 0
    try:
        await CreditService(db).deduct_credits(
            user_id=user_id, amount=amount, transaction_type=transaction_type, description=description
        )
        await db.commit()
    except InsufficientCreditsError as exc:
        await db.rollback()
        raise_http_error(status_code=402, code=ErrorCode.KG_402_EXCEPTION, message=exc.message)
    return amount


async def _refund(db: AsyncSession, user_id: int, amount: int, transaction_type: str, reason: str) -> None:
    if amount <= 0:
        return
    try:
        await CreditService(db).refund_credits(
            user_id=user_id,
            amount=amount,
            original_transaction_type=transaction_type,
            reason=reason,
            reference_id=one_off_refund_reference(f"kg-user-{user_id}"),
        )
        await db.commit()
    except Exception as refund_error:
        await db.rollback()
        logger.error(f"❌ {transaction_type} 退款失败: user_id={user_id}, err={refund_error}")


async def _start_paid_task(
    db: AsyncSession,
    user_id: int,
    *,
    amount: int,
    transaction_type: str,
    description: str,
    task,
    kwargs: Dict[str, Any],
    task_type: str,
    message: str,
) -> Dict[str, Any]:
    """扣费 → 提交后台任务；提交失败则退款。任务失败时由任务自己按 charged_credits 退款。"""
    charged = await _charge(db, user_id, amount, transaction_type, description)
    try:
        async_result = task.delay(**kwargs, charged_credits=charged)
    except Exception as exc:
        logger.exception(f"❌ {task_type} 任务提交失败: user_id={user_id}")
        await _refund(db, user_id, charged, transaction_type, "任务提交失败退款")
        raise_http_error(
            status_code=503,
            code=ErrorCode.KG_503_EXCEPTION,
            message="任务提交失败，积分已退还，请稍后重试",
            details=str(exc),
        )
    return {
        "success": True,
        "message": message,
        "task_id": async_result.id,
        "task_type": task_type,
        "credits_deducted": charged,
    }


@router.post("/maintenance/renormalize-terms")
async def renormalize_terms_for_current_user(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """按最新的归一化规则合并重复节点、修正 key 与展示名（无需处理时不写库）。

    前端打开知识网络时会调用一次：归一化规则修复后（如中文引号），旧数据里的重复节点由此合并。
    """
    try:
        stats = await auto_merge_terms(db, current_user.id)
    except IntegrityError:
        # 同一用户的另一个请求正在做同样的整理
        await db.rollback()
        return {"success": True, "merged_nodes": 0, "renamed_nodes": 0, "busy": True}
    if stats["merged_nodes"] or stats["renamed_nodes"]:
        await invalidate_kg_cache(current_user.id)
    return {"success": True, **stats}


@router.get("/nodes-edges")
async def get_knowledge_graph(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取当前用户的知识图谱节点和共现边。

    - 节点：仍被文档引用的 `KnowledgeTerm`，document_count 为去重后的实时文档数；
    - 边：在同一篇文档中共现的术语对，按共现文档数计权；
    - 结果按 user_id 缓存（10 分钟 TTL，写操作时主动失效）。
    """
    cache_key = _kg_cache_key(current_user.id)
    cached = await cache_get_bytes(cache_key)
    if cached:
        try:
            return json.loads(cached.decode("utf-8"))
        except Exception:
            pass

    terms: List[KnowledgeTerm] = (await db.execute(
        select(KnowledgeTerm).where(KnowledgeTerm.user_id == current_user.id)
    )).scalars().all()
    edges, doc_counts = await load_term_cooccurrence(db, current_user.id)
    connected = {source for source, _, _ in edges} | {target for _, target, _ in edges}

    def _term_to_dict(term: KnowledgeTerm) -> Dict[str, Any]:
        return {
            "id": term.id,
            "label": term.display_term,
            "normalized": term.term,
            "description": term.description,
            "document_count": doc_counts.get(term.id, 0),
        }

    result_data = {
        "nodes": [_term_to_dict(t) for t in terms if t.id in connected],
        "edges": [{"source": source, "target": target, "weight": weight} for source, target, weight in edges],
        # 没有任何共现边、但仍被文档引用的节点；已不被引用的节点不再显示
        "orphan_nodes": [_term_to_dict(t) for t in terms if t.id not in connected and doc_counts.get(t.id, 0) > 0],
    }

    try:
        await cache_set_bytes(
            cache_key,
            json.dumps(result_data).encode("utf-8"),
            ttl=CACHE_TTL.KNOWLEDGE_GRAPH,
        )
    except Exception:
        pass  # 缓存写入失败不影响正常返回

    return result_data


@router.get("/document/{document_id}/terms")
async def get_terms_for_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取某篇文档当前在知识图谱中的关键术语列表。"""

    document = await db.get(Document, document_id)
    if not document or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="文档不存在")

    dt = DocumentKnowledgeTerm
    result = await db.execute(
        select(KnowledgeTerm, dt)
        .join(dt, dt.term_id == KnowledgeTerm.id)
        .where(
            dt.document_id == document_id,
            dt.user_id == current_user.id,
        )
        .order_by(KnowledgeTerm.id)
    )

    rows = result.all()
    terms_data: List[Dict[str, Any]] = []
    seen_ids = set()
    for term, _rel in rows:
        if term.id in seen_ids:
            continue
        seen_ids.add(term.id)
        terms_data.append(
            {
                "id": term.id,
                "term": term.display_term,
                "normalized": term.term,
                "description": term.description,
            }
        )

    return {"success": True, "data": terms_data}


@router.put("/document/{document_id}/terms")
async def update_terms_for_document(
    document_id: int,
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """覆盖式更新某篇文档的关键术语，并同步到知识图谱。

    前端会提交一个术语数组，其中每个元素至少包含 term 字段，description 可选。
    重建关系后，会重算这篇文档原有和现有节点的文档数，并清理不再被任何文档引用的节点。
    """

    document = await db.get(Document, document_id)
    if not document or document.user_id != current_user.id:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="文档不存在")

    raw_terms = payload.get("terms") or []
    if not isinstance(raw_terms, list):
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="terms 字段必须是数组")
    if len(raw_terms) > DOCUMENT_TERMS_MAX:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message=f"每篇文档最多 {DOCUMENT_TERMS_MAX} 个关键词")

    # 预处理用户提交的术语列表
    term_items: List[tuple[str, str]] = []
    for item in raw_terms:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term") or "").strip()
        description = str(item.get("description") or "").strip()
        if not term:
            continue
        term_items.append((term, description))

    dt = DocumentKnowledgeTerm

    # 1. 删除当前文档已有的所有关系（与 service.py 保持一致），并记下原来关联的节点
    previous_term_ids = set((await db.execute(
        select(dt.term_id).where(dt.document_id == document_id)
    )).scalars().all())
    await db.execute(
        delete(dt).where(
            dt.document_id == document_id,
        )
    )

    touched_term_ids: set[int] = set()

    # 2. 逐个术语更新 / 创建节点并建立关系
    for raw_term, description in term_items:
        normalized = normalize_term(raw_term)
        if not normalized:
            continue

        result = await db.execute(
            select(KnowledgeTerm).where(
                KnowledgeTerm.user_id == current_user.id,
                KnowledgeTerm.term == normalized,
            ).limit(1)
        )
        node: KnowledgeTerm | None = result.scalar_one_or_none()

        if node is None:
            display = format_display_term(raw_term) or normalized
            node = KnowledgeTerm(
                user_id=current_user.id,
                term=normalized,
                display_term=display[:TERM_MAX_CHARS],
                description=description[:TERM_DESCRIPTION_MAX_CHARS] or None,
                document_count=0,
            )
            db.add(node)
            await db.flush()
        elif not node.description and description:
            # 之前没有描述、而这次提交带了描述：补充上
            node.description = description[:TERM_DESCRIPTION_MAX_CHARS]

        # 同一概念的不同写法归一化后相同，只建一条关系（uq_doc_term）
        if node.id in touched_term_ids:
            continue

        rel = DocumentKnowledgeTerm(
            user_id=current_user.id,
            document_id=document_id,
            term_id=node.id,
            importance=1.0,
        )
        db.add(rel)
        touched_term_ids.add(node.id)

    await refresh_terms(db, current_user.id, touched_term_ids | previous_term_ids)
    await db.commit()

    await invalidate_kg_cache(current_user.id)
    return {"success": True, "count": len(term_items)}


@router.put("/term/{term_id}")
async def update_term(
    term_id: int,
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """修改节点名称和 / 或描述。改名后与已有节点同名时返回 409，由前端询问是否合并。"""
    term = await _get_owned_term(db, term_id, current_user.id)

    display_term_raw = payload.get("display_term")
    if display_term_raw is not None and not isinstance(display_term_raw, str):
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="节点名称格式不正确")
    display_term_raw = (display_term_raw or "").strip()

    if "description" in payload:
        description = payload.get("description")
        if description is not None and not isinstance(description, str):
            raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="描述格式不正确")
        description = (description or "").strip()
        if len(description) > TERM_DESCRIPTION_MAX_CHARS:
            raise_http_error(
                status_code=400, code=ErrorCode.KG_400_MESSAGE, message=f"描述最多 {TERM_DESCRIPTION_MAX_CHARS} 个字符"
            )
        term.description = description or None

    if display_term_raw:
        if len(display_term_raw) > TERM_MAX_CHARS:
            raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message=f"节点名称最多 {TERM_MAX_CHARS} 个字符")
        new_normalized = normalize_term(display_term_raw)
        if not new_normalized:
            raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="节点名称不能为空")
        if new_normalized != term.term:
            clash = (await db.execute(
                select(KnowledgeTerm).where(
                    KnowledgeTerm.user_id == current_user.id,
                    KnowledgeTerm.term == new_normalized,
                    KnowledgeTerm.id != term.id,
                ).limit(1)
            )).scalar_one_or_none()
            if clash is not None:
                existing_id, existing_label = clash.id, clash.display_term  # rollback 会让 ORM 对象过期
                await db.rollback()
                raise_http_error(
                    status_code=409,
                    code=ErrorCode.KG_409_TERM_EXISTS,
                    message=f"已存在同名节点「{format_display_term(existing_label) or existing_label}」",
                    details={"existing_id": existing_id, "existing_label": existing_label},
                )
            term.term = new_normalized
        term.display_term = (format_display_term(display_term_raw) or display_term_raw)[:TERM_MAX_CHARS]

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise_http_error(status_code=409, code=ErrorCode.KG_409_TERM_EXISTS, message="已存在同名节点，请刷新后重试")
    await invalidate_kg_cache(current_user.id)

    return {
        "success": True,
        "id": term.id,
        "display_term": term.display_term,
        "description": term.description,
    }


@router.delete("/term/{term_id}")
async def delete_term(
    term_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    term = await _get_owned_term(db, term_id, current_user.id)

    dt = DocumentKnowledgeTerm
    await db.execute(
        delete(dt).where(
            dt.user_id == current_user.id,
            dt.term_id == term_id,
        )
    )
    await db.delete(term)
    await db.commit()
    await invalidate_kg_cache(current_user.id)

    return {"success": True}


@router.post("/term/delete-batch")
async def delete_terms_batch(
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """批量删除节点（只删图谱节点及其文档关系，不删文档）。"""
    term_ids = _require_ids(payload.get("term_ids"), TERM_BATCH_MAX, f"一次最多删除 {TERM_BATCH_MAX} 个节点")
    if not term_ids:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="请选择要删除的节点")

    owned = list((await db.execute(
        select(KnowledgeTerm.id).where(
            KnowledgeTerm.user_id == current_user.id,
            KnowledgeTerm.id.in_(term_ids),
        )
    )).scalars().all())
    if owned:
        dt = DocumentKnowledgeTerm
        await db.execute(delete(dt).where(dt.user_id == current_user.id, dt.term_id.in_(owned)))
        await db.execute(delete(KnowledgeTerm).where(KnowledgeTerm.id.in_(owned)))
        await db.commit()
        await invalidate_kg_cache(current_user.id)

    owned_set = set(owned)
    return {
        "success": True,
        "deleted_ids": owned,
        "missing_ids": [term_id for term_id in term_ids if term_id not in owned_set],
    }


@router.post("/analyze-merge-candidates")
async def analyze_merge_candidates(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """分析当前用户的知识图谱，提供节点合并建议（后台任务）。"""
    term_count = int((await db.execute(
        select(func.count(KnowledgeTerm.id)).where(KnowledgeTerm.user_id == current_user.id)
    )).scalar_one() or 0)
    if term_count < 2:
        # 没有可整理的节点：不调用模型，也不扣费
        return {"success": True, "suggestions": [], "message": "节点太少，无需整理"}

    from tasks.generation_tasks import generate_merge_suggestions_task

    return await _start_paid_task(
        db,
        current_user.id,
        amount=CreditCost.AI_ORGANIZE,
        transaction_type="ai_organize",
        description="知识图谱智能整理",
        task=generate_merge_suggestions_task,
        kwargs={"user_id": current_user.id},
        task_type="merge_suggestions",
        message="正在分析合并建议...",
    )


@router.post("/term/merge")
async def merge_terms(
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    source_id = payload.get("source_id")
    target_id = payload.get("target_id")

    if not _is_id(source_id) or not _is_id(target_id):
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="source_id 和 target_id 必须是整数")

    if source_id == target_id:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="不能合并到自身")

    outcome = await merge_term_pairs(db, current_user.id, [(source_id, target_id)])
    entry = outcome.results[0]
    if entry.get("skipped") == "not found":
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="术语不存在或无权访问")
    if entry.get("skipped"):
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="这两个节点无法合并")

    await invalidate_kg_cache(current_user.id)
    target = await db.get(KnowledgeTerm, entry["merged_into"])

    return {
        "success": True,
        "source_id": source_id,
        "target_id": target_id,
        "moved_relations": outcome.moved_relations,
        "deleted_relations": outcome.deleted_relations,
        "document_count": target.document_count if target else 0,
    }


@router.post("/term/merge-batch")
async def merge_terms_batch(
    payload: Dict[str, Any] = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """批量合并多组术语节点（链式、成环、多个节点并入同一目标都能正确处理）。

    payload.merges: [{"source_id": int, "target_id": int}, ...]
    """
    merges = payload.get("merges")
    if not isinstance(merges, list) or not merges:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="merges 字段必须是非空数组")
    if len(merges) > TERM_BATCH_MAX:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message=f"一次最多合并 {TERM_BATCH_MAX} 组")

    pairs = [
        (item.get("source_id"), item.get("target_id")) if isinstance(item, dict) else (None, None)
        for item in merges
    ]
    outcome = await merge_term_pairs(db, current_user.id, pairs)
    if outcome.final_target:
        await invalidate_kg_cache(current_user.id)
    return {"success": True, "results": outcome.results, "merged": len(outcome.final_target)}


@router.get("/term/{term_id}/documents")
async def get_documents_for_term(
    term_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> List[Dict[str, Any]]:
    """获取某个知识图谱术语下的所有文档列表。"""

    # 确认该术语属于当前用户
    await _get_owned_term(db, term_id, current_user.id)

    # 查询关联的文档
    dt = DocumentKnowledgeTerm
    result = await db.execute(
        select(Document)
        .join(dt, dt.document_id == Document.id)
        .where(
            dt.term_id == term_id,
            Document.user_id == current_user.id,
        )
        .order_by(Document.created_at.desc())
    )

    docs: List[Document] = result.scalars().all()

    # 去重：同一篇文章可能对应多个 Document 记录（例如复用向量时），
    # 这里按 file_hash / 提取标题 / 文件名 合并，只保留一条代表性记录。
    unique_docs: Dict[str, Document] = {}
    for d in docs:
        key = d.file_hash or (d.extracted_title or (d.display_name or d.filename))
        if not key:
            # 极端情况：没有任何可用键时，用 id 兜底，确保不会丢失
            unique_docs[f"id:{d.id}"] = d
            continue

        norm_key = key.strip().lower()
        existing = unique_docs.get(norm_key)
        if existing is None:
            unique_docs[norm_key] = d
        else:
            # 选择“更好”的那条：优先有自定义显示名的，其次创建时间更新的
            if (d.display_name and not existing.display_name) or (
                d.created_at
                and (not existing.created_at or d.created_at > existing.created_at)
            ):
                unique_docs[norm_key] = d

    # 按创建时间倒序返回，与原有语义保持一致
    ordered_docs = sorted(
        unique_docs.values(),
        key=lambda d: d.created_at or datetime.min,
        reverse=True,
    )

    return [
        {
            "document_id": d.id,
            "filename": d.filename,
            "display_name": d.display_name or d.filename,
            "extracted_title": d.extracted_title,
            "summary": d.summary,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in ordered_docs
    ]


@router.post("/eureka")
async def generate_eureka_moment(
    payload: Dict[str, Any] = Body(default={}),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """
    "有了！"核心功能（后台任务）
    返回 task_id，前端需轮询 /api/generation-task/{task_id}/status 获取结果
    """
    target_size = payload.get("size", 3)
    if not _is_id(target_size):
        target_size = 3
    target_size = max(EUREKA_MIN_NODES, min(EUREKA_MAX_NODES, target_size))
    innovation_type = payload.get("innovation_type")
    if innovation_type not in EUREKA_INNOVATION_TYPES:
        innovation_type = "auto"

    node_ids = _require_ids(payload.get("node_ids") or [], EUREKA_MAX_NODES, f"最多选择 {EUREKA_MAX_NODES} 个节点")
    # 先确认有足够的节点可以连接，再扣费：否则任务注定失败，却已经扣了积分
    if len(node_ids) == 1:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message=f"请至少选择 {EUREKA_MIN_NODES} 个节点")
    if node_ids:
        if await _count_linked_terms(db, current_user.id, node_ids) < EUREKA_MIN_NODES:
            raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="选中的节点已不存在，请刷新后重试")
    elif await _count_linked_terms(db, current_user.id) < EUREKA_MIN_NODES:
        raise_http_error(status_code=400, code=ErrorCode.KG_400_MESSAGE, message="知识网络里的概念太少，请先上传并解析更多文档")

    from tasks.generation_tasks import generate_eureka_task

    return await _start_paid_task(
        db,
        current_user.id,
        amount=CreditCost.AI_EUREKA,
        transaction_type="ai_eureka",
        description="知识图谱灵感生成",
        task=generate_eureka_task,
        kwargs={
            "user_id": current_user.id,
            "target_size": target_size,
            "node_ids": node_ids or None,
            "innovation_type": innovation_type,
        },
        task_type="eureka",
        message="灵感生成任务已提交",
    )


@router.post("/eureka/save")
async def save_eureka_moment(
    payload: SaveEurekaRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """保存 Eureka 灵感"""
    source_nodes = [
        {
            "id": node.get("id") if _is_id(node.get("id")) else None,
            "label": str(node.get("label") or node.get("term") or "")[:TERM_MAX_CHARS],
            "description": str(node.get("description") or "")[:500] or None,
        }
        for node in payload.source_nodes
        if isinstance(node, dict)
    ]
    moment = EurekaMoment(
        user_id=current_user.id,
        title=payload.idea[:EUREKA_TITLE_MAX_CHARS],
        content=payload.reasoning,
        source_nodes=source_nodes,
    )
    db.add(moment)
    await db.commit()
    await db.refresh(moment)

    return {
        "success": True,
        "data": {
            "id": moment.id,
            "created_at": moment.created_at
        }
    }


@router.get("/eureka/list")
async def list_eureka_moments(
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取保存的 Eureka 灵感列表"""
    result = await db.execute(
        select(EurekaMoment)
        .where(EurekaMoment.user_id == current_user.id)
        .order_by(EurekaMoment.created_at.desc())
        .limit(max(1, min(limit, 200)))
        .offset(max(0, offset))
    )
    moments = result.scalars().all()

    return {
        "success": True,
        "data": [
            {
                "id": m.id,
                "title": m.title,
                "content": m.content,
                "source_nodes": m.source_nodes,
                "created_at": m.created_at
            }
            for m in moments
        ]
    }


@router.delete("/eureka/{moment_id}")
async def delete_eureka_moment(
    moment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """删除 Eureka 灵感（连同它的对话记录和笔记：这两张表没有外键，不会级联删除）"""
    result = await db.execute(
        select(EurekaMoment)
        .where(EurekaMoment.id == moment_id, EurekaMoment.user_id == current_user.id)
    )
    moment = result.scalar_one_or_none()

    if not moment:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="未找到该记录")

    await db.execute(delete(EurekaChatHistory).where(
        EurekaChatHistory.eureka_id == moment_id,
        EurekaChatHistory.user_id == current_user.id,
    ))
    await db.execute(delete(EurekaNote).where(
        EurekaNote.eureka_id == moment_id,
        EurekaNote.user_id == current_user.id,
    ))
    await db.delete(moment)
    await db.commit()

    return {"success": True}


# ==================== 灵感对话 API ====================

class EurekaChatRequest(BaseModel):
    question: str = Field(..., max_length=EUREKA_CHAT_MAX_QUESTION_CHARS, description="用户问题")
    session_id: Optional[str] = Field(default=None, max_length=100, description="会话ID")
    model: Optional[str] = Field(default=None, description="旧客户端字段：模型按功能映射选择，不接受客户端指定")

    @field_validator("question")
    @classmethod
    def _question_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


@router.get("/eureka/{moment_id}")
async def get_eureka_moment(
    moment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取单个灵感详情"""
    result = await db.execute(
        select(EurekaMoment)
        .where(EurekaMoment.id == moment_id, EurekaMoment.user_id == current_user.id)
    )
    moment = result.scalar_one_or_none()

    if not moment:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="未找到该灵感")

    return {
        "success": True,
        "data": {
            "id": moment.id,
            "title": moment.title,
            "content": moment.content,
            "source_nodes": moment.source_nodes,
            "created_at": moment.created_at
        }
    }


async def _save_eureka_chat(moment_id: int, user_id: int, session_id: str, question: str, answer: str) -> Optional[int]:
    try:
        async with AsyncSessionLocal() as save_db:
            entry = EurekaChatHistory(
                eureka_id=moment_id,
                user_id=user_id,
                session_id=session_id,
                question=question,
                answer=answer,
            )
            save_db.add(entry)
            await save_db.commit()
            return entry.id
    except Exception as save_err:
        logger.warning(f"⚠️ 保存 Eureka 对话历史失败: {save_err}")
        return None


async def _charge_eureka_chat(user_id: int, moment_id: int, answer: str) -> int:
    """回答完整生成后按长度计费，与文档问答（快速模式）同价"""
    amount = CreditCost.calculate_qa_cost(len(answer), 0)
    if amount <= 0:
        return 0
    try:
        async with AsyncSessionLocal() as credit_db:
            await CreditService(credit_db).deduct_credits(
                user_id=user_id,
                amount=amount,
                transaction_type="qa",
                description=f"灵感对话 (回复{len(answer)}字)",
                reference_id=f"eureka:{moment_id}",
            )
            await credit_db.commit()
            return amount
    except Exception as credit_err:
        logger.warning(f"⚠️ [Eureka Credit] 扣费失败: {credit_err}")
        return 0


@router.post("/eureka/{moment_id}/chat")
async def chat_with_eureka(
    moment_id: int,
    request: EurekaChatRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
):
    """与灵感进行对话（流式响应）

    基于灵感内容生成上下文，回答用户问题；回答完整生成后按文档问答的价格计费。
    返回 SSE 流式响应
    """
    # 获取灵感详情
    result = await db.execute(
        select(EurekaMoment)
        .where(EurekaMoment.id == moment_id, EurekaMoment.user_id == current_user.id)
    )
    moment = result.scalar_one_or_none()

    if not moment:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="未找到该灵感")

    credits = await CreditService(db).get_user_credits(current_user.id)
    if credits.balance < CreditCost.QA_BASE:
        raise_http_error(
            status_code=402,
            code=ErrorCode.KG_402_EXCEPTION,
            message=f"积分不足：灵感对话需至少 {CreditCost.QA_BASE} 积分，当前余额 {credits.balance}",
        )

    # 构建灵感上下文
    source_nodes_text = ""
    if moment.source_nodes:
        nodes = moment.source_nodes if isinstance(moment.source_nodes, list) else []
        node_labels = [
            format_display_term(n.get("label") or n.get("term") or "")
            for n in nodes
            if isinstance(n, dict)
        ]
        source_nodes_text = "连接的节点: " + ", ".join(label for label in node_labels if label)

    eureka_context = f"""你正在与用户讨论一个你之前生成的创新灵感。

本项目的核心理念是"创新源于连接"——通过发现不同概念节点之间的潜在联系来激发创新灵感。

这个灵感是你基于用户知识图谱中的多个节点，通过发现它们之间隐藏的联系而产生的。

灵感标题: {moment.title}

你生成的灵感内容:
{moment.content}

{source_nodes_text}

现在用户想要与你深入讨论这个灵感。请以灵感创造者的身份，帮助用户：
1. 解释你当初是如何从这些节点中发现联系并产生这个灵感的
2. 探索这个想法的更多可能性和应用场景
3. 分析潜在的挑战和改进方向
4. 回答用户关于这个灵感的任何问题

📝 **回答规范**：
- 使用 Markdown 格式组织答案
- **Latex 格式规范**：
   - 行内公式使用 $...$ 包裹。
   - 独立公式使用 $$...$$ 包裹。
   - **严禁**使用 Unicode 数学符号（如 ∈, ∑, ∫, ×, → 等），必须使用对应的 Latex 命令（如 \\in, \\sum, \\int, \\times, \\rightarrow）。"""

    user_id = current_user.id
    session_id = request.session_id or f"eureka-session-{moment_id}-{user_id}"
    question = request.question

    # 加载当前会话**最近**的若干轮对话（按 id 倒序取，再恢复成时间顺序）
    history_records = (await db.execute(
        select(EurekaChatHistory)
        .where(
            EurekaChatHistory.eureka_id == moment_id,
            EurekaChatHistory.user_id == user_id,
            EurekaChatHistory.session_id == session_id,
        )
        .order_by(EurekaChatHistory.id.desc())
        .limit(EUREKA_CHAT_CONTEXT_ROUNDS)
    )).scalars().all()
    chat_history = []
    for record in reversed(history_records):
        chat_history.append(HumanMessage(content=record.question))
        chat_history.append(AIMessage(content=record.answer))

    async def generate_stream():
        """流式生成器"""
        start_time = time.time()
        full_answer = ""

        def sse_message(event_type: str, data) -> str:
            payload = {"event": event_type, "data": data}
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        try:
            yield sse_message("status", {"text": "我正在思考中...", "icon": "thinking", "phase": "init"})

            from llm_service import get_llm_for_feature
            llm = get_llm_for_feature("eureka_chat", streaming=True)

            # 构建消息：system prompt + 历史对话 + 当前问题
            messages = [SystemMessage(content=eureka_context)]
            messages.extend(chat_history)
            messages.append(HumanMessage(content=question))

            yield sse_message("status", {"text": "正在生成回答...", "icon": "generating", "phase": "generating"})

            async for chunk in llm.astream(messages):
                if chunk.content:
                    # 应用 LaTeX 格式修复
                    content = fix_latex_format(chunk.content)
                    full_answer += content
                    yield sse_message("token", content)
        except Exception as exc:
            logger.exception(f"❌ 灵感对话失败: moment_id={moment_id}, user_id={user_id}")
            yield sse_message("error", _public_ai_error(exc, "AI 回答失败，请稍后重试"))
            return

        if not full_answer.strip():
            yield sse_message("error", "AI 没有返回内容，请重试")
            return

        # 先保存记录、扣费，再通知前端完成：前端收到 done 后会刷新会话列表，并用 message_id 支持编辑
        history_id = await _save_eureka_chat(moment_id, user_id, session_id, question, full_answer)
        credits_deducted = await _charge_eureka_chat(user_id, moment_id, full_answer)
        yield sse_message("done", {
            "duration": round(time.time() - start_time, 2),
            "used_rag": False,
            "iterations": 0,
            "message_id": history_id,
            "credits_deducted": credits_deducted,
        })

    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@router.get("/eureka/{moment_id}/history")
async def get_eureka_chat_history(
    moment_id: int,
    session_id: str = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取灵感对话历史：返回**最近**的 limit 条，按时间顺序排列"""
    query = select(EurekaChatHistory).where(
        EurekaChatHistory.eureka_id == moment_id,
        EurekaChatHistory.user_id == current_user.id
    )

    if session_id:
        query = query.where(EurekaChatHistory.session_id == session_id)

    query = query.order_by(EurekaChatHistory.id.desc()).limit(max(1, min(limit, EUREKA_HISTORY_MAX_LIMIT)))

    result = await db.execute(query)
    history = list(reversed(result.scalars().all()))

    return {
        "success": True,
        "data": [
            {
                "id": h.id,
                "question": h.question,
                "answer": h.answer,
                "session_id": h.session_id,
                "created_at": h.created_at.isoformat() if h.created_at else None
            }
            for h in history
        ]
    }


@router.delete("/eureka/{moment_id}/history")
async def delete_eureka_chat_history(
    moment_id: int,
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """删除灵感对话历史"""
    await db.execute(
        delete(EurekaChatHistory).where(
            EurekaChatHistory.eureka_id == moment_id,
            EurekaChatHistory.user_id == current_user.id,
            EurekaChatHistory.session_id == session_id
        )
    )
    await db.commit()

    return {"success": True, "message": "对话历史已删除"}


@router.delete("/eureka/{moment_id}/history/after/{history_id}")
async def delete_eureka_chat_history_after(
    moment_id: int,
    history_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """删除指定消息及同一会话中之后的所有对话（编辑已发送的问题时使用）"""
    target = (await db.execute(
        select(EurekaChatHistory).where(
            EurekaChatHistory.id == history_id,
            EurekaChatHistory.eureka_id == moment_id,
            EurekaChatHistory.user_id == current_user.id,
        )
    )).scalar_one_or_none()
    if not target:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="消息不存在")

    # 按 id 判断先后：SQLite 的时间戳只精确到秒，同一秒内的消息无法区分
    result = await db.execute(
        delete(EurekaChatHistory).where(
            EurekaChatHistory.eureka_id == moment_id,
            EurekaChatHistory.user_id == current_user.id,
            EurekaChatHistory.session_id == target.session_id,
            EurekaChatHistory.id >= target.id,
        )
    )
    await db.commit()
    return {"success": True, "deleted_count": result.rowcount or 0}


@router.get("/eureka/{moment_id}/sessions")
async def get_eureka_sessions(
    moment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取灵感对话的会话列表"""
    from sqlalchemy import literal_column

    first_q_subq = (
        select(EurekaChatHistory.question)
        .where(
            EurekaChatHistory.eureka_id == moment_id,
            EurekaChatHistory.user_id == current_user.id,
            EurekaChatHistory.session_id == literal_column("eureka_chat_history.session_id"),
        )
        .order_by(EurekaChatHistory.created_at.asc())
        .limit(1)
        .correlate(EurekaChatHistory)
        .scalar_subquery()
        .label("first_question")
    )

    result = await db.execute(
        select(
            EurekaChatHistory.session_id,
            first_q_subq,
            func.count(EurekaChatHistory.id).label("message_count"),
            func.max(EurekaChatHistory.created_at).label("last_activity_time")
        )
        .where(
            EurekaChatHistory.eureka_id == moment_id,
            EurekaChatHistory.user_id == current_user.id
        )
        .group_by(EurekaChatHistory.session_id)
        .order_by(func.max(EurekaChatHistory.created_at).desc())
    )

    sessions = result.all()

    return {
        "success": True,
        "data": [
            {
                "session_id": s.session_id,
                "first_question": (s.first_question[:50] + "...") if s.first_question and len(s.first_question) > 50 else (s.first_question or ""),
                "message_count": s.message_count,
                "last_activity_time": s.last_activity_time.isoformat() if s.last_activity_time else None
            }
            for s in sessions
        ]
    }


# ==================== 知识间隙发现 ====================


@router.post("/knowledge-gaps")
async def analyze_knowledge_gaps(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """分析用户知识图谱中的知识间隙。

    先在共现图上做社区划分（不调用模型）；有至少两个知识社区时才扣费，
    并提交后台任务让 LLM 分析社区之间的间隙、给出桥梁概念与阅读建议。
    """
    analysis = await analyze_term_communities(db, current_user.id)
    if not analysis.needs_ai:
        return {
            "success": True,
            "gaps": [],
            "communities": analysis.communities,
            "message": analysis.message,
        }

    from tasks.generation_tasks import analyze_knowledge_gaps_task

    return await _start_paid_task(
        db,
        current_user.id,
        amount=CreditCost.AI_ORGANIZE,
        transaction_type="ai_organize",
        description="知识间隙分析",
        task=analyze_knowledge_gaps_task,
        kwargs={"user_id": current_user.id, "communities": analysis.communities, "pairs": analysis.pairs},
        task_type="knowledge_gaps",
        message="正在分析知识间隙...",
    )


class SaveGapAnalysisRequest(BaseModel):
    gaps: List[Dict[str, Any]] = Field(default_factory=list, max_length=20)
    communities: List[Dict[str, Any]] = Field(default_factory=list, max_length=20)


@router.post("/knowledge-gaps/save")
async def save_gap_analysis(
    payload: SaveGapAnalysisRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """保存知识间隙分析结果"""
    record = KnowledgeGapAnalysis(
        user_id=current_user.id,
        gaps=payload.gaps,
        communities=payload.communities,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "success": True,
        "id": record.id,
    }


@router.get("/knowledge-gaps/list")
async def list_gap_analyses(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """获取已保存的知识间隙分析列表"""
    result = await db.execute(
        select(KnowledgeGapAnalysis)
        .where(KnowledgeGapAnalysis.user_id == current_user.id)
        .order_by(KnowledgeGapAnalysis.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .offset(max(0, offset))
    )
    records = result.scalars().all()

    return {
        "success": True,
        "items": [
            {
                "id": r.id,
                "gaps": r.gaps,
                "communities": r.communities,
                "gap_count": len(r.gaps) if r.gaps else 0,
                "community_count": len(r.communities) if r.communities else 0,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in records
        ],
    }


@router.delete("/knowledge-gaps/{analysis_id}")
async def delete_gap_analysis(
    analysis_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(auth.get_current_user),
) -> Dict[str, Any]:
    """删除已保存的知识间隙分析"""
    result = await db.execute(
        select(KnowledgeGapAnalysis).where(
            KnowledgeGapAnalysis.id == analysis_id,
            KnowledgeGapAnalysis.user_id == current_user.id,
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise_http_error(status_code=404, code=ErrorCode.KG_404_MESSAGE, message="未找到该分析记录")

    await db.delete(record)
    await db.commit()
    return {"success": True}
