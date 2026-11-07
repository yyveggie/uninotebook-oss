from __future__ import annotations

from typing import Iterable

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import Document
from document_service.schema import KeyTerm
from .models import KnowledgeTerm, DocumentKnowledgeTerm
from .utils import (
    TERM_DESCRIPTION_MAX_CHARS,
    TERM_MAX_CHARS,
    normalize_term,
    format_display_term,
    invalidate_kg_cache,
    refresh_terms,
)
from utils.logger import get_logger
logger = get_logger(__name__)


async def update_knowledge_graph_for_document(
  db: AsyncSession,
  document: Document,
  key_terms: Iterable[KeyTerm],
) -> None:
  """根据一篇文档的关键术语更新用户知识图谱.

  - 按 (user_id, term) 维度维护唯一的 `KnowledgeTerm` 节点;
  - 为当前文档写入 `DocumentKnowledgeTerm` 关系;
  - 重新统计本次涉及到的 term 的 `document_count`。
  """

  if db is None or document is None:
    return

  key_terms = list(key_terms or [])
  if not key_terms:
    logger.warning(f"⚠️ [KG] key_terms 为空，跳过更新 (doc_id={document.id if document else 'None'})")
    return

  user_id = document.user_id
  doc_id = document.id
  logger.info(f"🔍 [KG] 开始更新知识图谱: doc_id={doc_id}, terms数量={len(key_terms)}")

  if not user_id or not doc_id:
    logger.error(f"❌ [KG] user_id 或 doc_id 为空")
    return

  # 1. 先删除这篇文档已有的所有关系（防止重复生成摘要导致脏数据）；
  #    记下原来关联的节点，重建后要重算它们的计数并清理不再被引用的节点
  previous_term_ids = set((await db.execute(
    select(DocumentKnowledgeTerm.term_id).where(DocumentKnowledgeTerm.document_id == doc_id)
  )).scalars().all())
  try:
    await db.execute(
      delete(DocumentKnowledgeTerm).where(
        DocumentKnowledgeTerm.document_id == doc_id
      )
    )
    logger.info(f"✅ [KG] 已清理旧关系 (doc_id={doc_id})")
  except Exception as e:
    logger.error(f"❌ [KG] 清理旧关系失败: {e}")
    return

  # 2. 批量预加载该用户所有现有术语，避免逐条 SELECT 的 N+1 问题
  existing_result = await db.execute(
    select(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id)
  )
  term_map: dict[str, KnowledgeTerm] = {
    t.term: t for t in existing_result.scalars().all()
  }

  touched_term_ids: set[int] = set()
  new_nodes: list[KnowledgeTerm] = []

  for kt in key_terms:
    raw_term = getattr(kt, "term", None) or ""
    normalized = normalize_term(raw_term)

    if not normalized:
      logger.warning(f"⚠️ [KG] 术语归一化后为空，跳过: raw='{raw_term}'")
      continue

    node = term_map.get(normalized)

    if node is None:
      display_raw = raw_term.strip() or normalized
      display = format_display_term(display_raw) or normalized
      description = getattr(kt, "description", None)
      node = KnowledgeTerm(
        user_id=user_id,
        term=normalized,
        display_term=display[:TERM_MAX_CHARS],
        description=(description or "").strip()[:TERM_DESCRIPTION_MAX_CHARS] or None,
        document_count=0,
      )
      db.add(node)
      new_nodes.append(node)
      term_map[normalized] = node
    else:
      if not node.description:
        description = getattr(kt, "description", None)
        if description:
          node.description = description.strip()[:TERM_DESCRIPTION_MAX_CHARS]

  # 3. 一次性 flush 所有新节点，获取自增 ID
  if new_nodes:
    await db.flush()

  # 4. 批量建立文档-术语关系
  seen_term_ids: set[int] = set()
  for kt in key_terms:
    raw_term = getattr(kt, "term", None) or ""
    normalized = normalize_term(raw_term)
    if not normalized:
      continue
    node = term_map.get(normalized)
    if node is None or node.id is None:
      continue
    if node.id in seen_term_ids:
      continue
    seen_term_ids.add(node.id)

    rel = DocumentKnowledgeTerm(
      user_id=user_id,
      document_id=doc_id,
      term_id=node.id,
      importance=1.0,
    )
    db.add(rel)
    touched_term_ids.add(node.id)

  try:
      await db.commit()
      logger.info(f"✅ [KG] 知识图谱关系提交成功: 插入 {len(touched_term_ids)} 条关系")
  except Exception as e:
      logger.error(f"❌ [KG] 提交知识图谱关系失败: {e}")
      return

  affected_term_ids = touched_term_ids | previous_term_ids
  if affected_term_ids:
    await refresh_terms(db, user_id, affected_term_ids)
    await db.commit()

  await invalidate_kg_cache(document.user_id)


async def cleanup_orphan_terms_for_user(db: AsyncSession, user_id: int) -> None:
  """清理当前用户下不再被任何文档引用的知识图谱节点。"""
  from .utils import cleanup_orphan_terms

  if db is None or not user_id:
    return
  await cleanup_orphan_terms(db, user_id)
  await db.commit()
