"""知识节点合并：手动合并、批量合并（AI 整理建议）与按归一化规则自动合并共用这里的逻辑。

不放在 api.py 里，是因为 Celery 任务也要调用；从 api.py 导入会连带加载整个 routes 包。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DocumentKnowledgeTerm, KnowledgeTerm
from .utils import TERM_MAX_CHARS, format_display_term, normalize_term, recount_document_counts


@dataclass
class MergeOutcome:
    results: List[dict] = field(default_factory=list)       # 每条合并请求的处理结果，顺序与输入一致
    final_target: Dict[int, int] = field(default_factory=dict)  # 被删节点 → 最终并入的节点
    moved_relations: int = 0
    deleted_relations: int = 0


def _is_id(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


async def merge_terms(db: AsyncSession, user_id: int, pairs: Sequence[Tuple[object, object]]) -> MergeOutcome:
    """按顺序执行一组「source 合并到 target」并提交。

    - 链式（A→B、B→C）：A、B 的文档关系最终都落到 C；
    - 成环（A→B、B→A）：后一条已没有意义，跳过——否则两个节点会被一起删掉，连同它们的文档关系；
    - 多个 source 并入同一 target 且出现在同一篇文档：只保留一条关系（uq_doc_term）。
    """
    outcome = MergeOutcome()
    terms = {
        term.id: term
        for term in (await db.execute(select(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id))).scalars().all()
    }
    parent: Dict[int, int] = {}

    def find(term_id: int) -> int:
        root = term_id
        while root in parent:
            root = parent[root]
        while term_id in parent and parent[term_id] != root:
            parent[term_id], term_id = root, parent[term_id]
        return root

    for source_id, target_id in pairs:
        entry = {"source_id": source_id, "target_id": target_id}
        if not _is_id(source_id) or not _is_id(target_id):
            entry["skipped"] = "invalid ids"
        elif source_id not in terms or target_id not in terms:
            entry["skipped"] = "not found"
        elif source_id in parent:
            entry["skipped"] = "already merged"
        elif find(target_id) == source_id:
            # 合并到自身，或目标此前已并入本节点（成环）
            entry["skipped"] = "same node"
        else:
            parent[source_id] = find(target_id)
        outcome.results.append(entry)

    if not parent:
        return outcome

    final_target = {term_id: find(term_id) for term_id in parent}
    roots = set(final_target.values())
    for entry in outcome.results:
        if "skipped" not in entry:
            entry["merged_into"] = final_target[entry["source_id"]]

    dt = DocumentKnowledgeTerm
    rows = (await db.execute(
        select(dt.id, dt.term_id, dt.document_id)
        .where(dt.user_id == user_id, dt.term_id.in_(set(final_target) | roots))
        .order_by(dt.id)
    )).all()
    linked_docs: Dict[int, set] = defaultdict(set)
    for _, term_id, document_id in rows:
        if term_id in roots:
            linked_docs[term_id].add(document_id)

    to_delete: List[int] = []
    to_move: Dict[int, List[int]] = defaultdict(list)
    for relation_id, term_id, document_id in rows:
        root = final_target.get(term_id)
        if root is None:
            continue
        if document_id in linked_docs[root]:
            to_delete.append(relation_id)
        else:
            linked_docs[root].add(document_id)
            to_move[root].append(relation_id)

    # 描述：目标没有时沿用第一个有描述的被合并节点
    for source_id, root in final_target.items():
        if not terms[root].description and terms[source_id].description:
            terms[root].description = terms[source_id].description

    if to_delete:
        await db.execute(delete(dt).where(dt.id.in_(to_delete)))
    for root, relation_ids in to_move.items():
        await db.execute(update(dt).where(dt.id.in_(relation_ids)).values(term_id=root))
    await db.execute(delete(KnowledgeTerm).where(KnowledgeTerm.id.in_(list(final_target))))
    await recount_document_counts(db, user_id, roots)
    await db.commit()

    outcome.final_target = final_target
    outcome.moved_relations = sum(len(ids) for ids in to_move.values())
    outcome.deleted_relations = len(to_delete)
    return outcome


async def auto_merge_terms(db: AsyncSession, user_id: int) -> Dict[str, int]:
    """按当前的归一化规则重新计算每个节点的 key，把 key 相同的节点合并，并修正 key / 展示名。

    归一化规则变化后（例如修复了中文引号的处理），旧数据里会留下本应是同一个概念的重复节点。
    没有需要处理的节点时不写数据库。
    """
    terms = (await db.execute(select(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id))).scalars().all()
    groups: Dict[str, List[KnowledgeTerm]] = {}
    for term in terms:
        normalized = normalize_term((term.display_term or term.term or "").strip())
        if normalized:
            groups.setdefault(normalized, []).append(term)

    pairs: List[Tuple[int, int]] = []
    canonical_by_key: Dict[str, KnowledgeTerm] = {}
    for normalized, group in groups.items():
        canonical = next((node for node in group if node.term == normalized), None) or min(group, key=lambda t: t.id)
        canonical_by_key[normalized] = canonical
        pairs.extend((node.id, canonical.id) for node in group if node.id != canonical.id)

    merged = 0
    if pairs:
        outcome = await merge_terms(db, user_id, pairs)
        merged = len(outcome.final_target)

    renamed = [(node, key) for key, node in canonical_by_key.items() if node.term != key]
    redisplayed = []
    for node in canonical_by_key.values():
        cleaned = format_display_term((node.display_term or node.term or "").strip())[:TERM_MAX_CHARS]
        if cleaned and cleaned != node.display_term:
            redisplayed.append((node, cleaned))

    if renamed or redisplayed:
        # 两个节点可能互换 key：先改成临时值再写最终值，避免中途撞上 uq_user_term
        for node, _ in renamed:
            node.term = f"__kg_merging__:{node.id}"  # PostgreSQL 文本不允许 NUL，临时值用普通字符
        await db.flush()
        for node, key in renamed:
            node.term = key
        for node, cleaned in redisplayed:
            node.display_term = cleaned
        await db.commit()

    return {
        "normalized_groups": len(groups),
        "canonical_nodes": len(canonical_by_key),
        "merged_nodes": merged,
        "renamed_nodes": len(renamed) + len(redisplayed),
    }
