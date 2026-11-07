"""AI 整理：决定把哪些概念放在同一批交给模型找重复。

概念不多时整张列表一次交给模型，召回最好。概念很多时一次放不下（原先只取按字母排序的
前 500 个，其余永远不会被检查），于是先用向量相似度把「可能重复」的概念聚到一起，
再分批交给模型确认；向量服务不可用时退回按名称排序分批。
"""
from __future__ import annotations

from typing import Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

Term = Tuple[int, str]  # (term_id, 展示名)

FULL_LIST_LIMIT = 400        # 不超过这么多概念时整表一次分析
BATCH_SIZE = 300             # 每批最多多少个概念
MAX_BATCHES = 4              # 单次整理最多调用几次模型
SIMILARITY_THRESHOLD = 0.82  # 余弦相似度达到这个值才算「可能重复」
NEIGHBORS = 5                # 每个概念最多取几个近邻
_BLOCK_ROWS = 1024           # 相似度矩阵分块计算，避免概念很多时一次分配 n×n 内存


def similar_clusters(vectors, threshold: float = SIMILARITY_THRESHOLD, neighbors: int = NEIGHBORS) -> List[List[int]]:
    """按向量相似度把下标聚成簇（只返回至少两个成员的簇），最相似的簇排在前面。"""
    import numpy as np

    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or len(matrix) < 2:
        return []
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = matrix / np.where(norms == 0, 1, norms)

    parent = list(range(len(matrix)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    best: Dict[int, float] = {}
    k = min(neighbors + 1, len(matrix))
    for start in range(0, len(matrix), _BLOCK_ROWS):
        block = matrix[start:start + _BLOCK_ROWS] @ matrix.T
        top = np.argpartition(-block, k - 1, axis=1)[:, :k]
        for row, candidates in enumerate(top):
            i = start + row
            for j in candidates:
                j = int(j)
                score = float(block[row, j])
                if j == i or score < threshold:
                    continue
                parent[find(i)] = find(j)
                best[i] = max(best.get(i, 0.0), score)
                best[j] = max(best.get(j, 0.0), score)

    clusters: Dict[int, List[int]] = {}
    for i in best:
        clusters.setdefault(find(i), []).append(i)
    groups = [sorted(members) for members in clusters.values() if len(members) >= 2]
    groups.sort(key=lambda members: -max(best[i] for i in members))
    return groups


def pack_batches(groups: Sequence[Sequence[Term]], batch_size: int = BATCH_SIZE, max_batches: int = MAX_BATCHES) -> List[List[Term]]:
    """把若干簇装进批次：同一簇尽量放在同一批；超大的簇拆开。"""
    batches: List[List[Term]] = []
    current: List[Term] = []
    for group in groups:
        for offset in range(0, len(group), batch_size):
            piece = list(group[offset:offset + batch_size])
            if len(piece) < 2:
                continue
            if current and len(current) + len(piece) > batch_size:
                batches.append(current)
                current = []
                if len(batches) >= max_batches:
                    return batches
            current.extend(piece)
    if current and len(batches) < max_batches:
        batches.append(current)
    return batches


async def plan_batches(
    terms: Sequence[Term],
    embed: Optional[Callable[[List[str]], Awaitable[List[List[float]]]]] = None,
) -> List[List[Term]]:
    terms = list(terms)
    if len(terms) <= FULL_LIST_LIMIT:
        return [terms] if len(terms) >= 2 else []

    if embed is not None:
        try:
            vectors = await embed([label for _, label in terms])
            if vectors and len(vectors) == len(terms) and all(v is not None for v in vectors):
                groups = similar_clusters(vectors)
                return pack_batches([[terms[i] for i in group] for group in groups])
            logger.warning("⚠️ [KG] 概念向量数量不匹配，改用按名称分批")
        except Exception as exc:
            logger.warning(f"⚠️ [KG] 概念向量化失败，改用按名称分批: {type(exc).__name__}: {exc}")

    ordered = sorted(terms, key=lambda term: term[1].lower())
    return [ordered[i:i + BATCH_SIZE] for i in range(0, len(ordered), BATCH_SIZE)][:MAX_BATCHES]


_REJECTION_HINTS = ("不建议", "保留两者", "无需合并", "不需要合并")


def sanitize_suggestions(raw: Sequence, labels: Dict[int, str]) -> List[dict]:
    """清洗模型给出的合并建议：去掉自合并、未知 ID、重复项和成环的反向建议。"""
    seen_sources: set = set()
    seen_pairs: set = set()
    cleaned: List[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        try:
            source, target = int(item.get("source_id")), int(item.get("target_id"))
        except (TypeError, ValueError):
            continue
        reason = str(item.get("reason") or "").strip()
        if (
            source == target
            or source not in labels
            or target not in labels
            or source in seen_sources          # 同一节点只能被合并一次
            or (target, source) in seen_pairs  # A→B 与 B→A 同时出现：保留先出现的
            or any(hint in reason for hint in _REJECTION_HINTS)
        ):
            continue
        seen_sources.add(source)
        seen_pairs.add((source, target))
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        cleaned.append({
            "source_id": source,
            "target_id": target,
            "source_label": labels[source],
            "target_label": labels[target],
            "reason": reason[:300],
            "confidence": confidence,
        })
    return cleaned
