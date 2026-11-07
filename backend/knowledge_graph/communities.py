"""知识间隙分析：在关键词共现图上划分知识社区，找出彼此联系薄弱的社区对。

只看连通分量是不够的：真实的资料库里几乎所有文档都会通过某个共同关键词连在一起，
整张图往往只有一个连通分量，于是永远「没有间隙」。这里改用 Louvain 社区划分，
按共现强度把图切成若干主题群，再比较群与群之间的连接密度。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from itertools import combinations
from typing import Dict, Iterable, List, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import KnowledgeTerm
from .utils import format_display_term, load_term_cooccurrence

MIN_TERMS = 4            # 少于这么多（仍被文档引用的）概念时不做分析
MIN_COMMUNITY_SIZE = 3   # 太小的社区不当作一个「知识领域」
MAX_COMMUNITIES = 6      # 交给 AI 分析的社区数上限
MAX_PAIRS = 8            # 交给 AI 的候选社区对上限（联系最弱的优先）
TOP_TERMS = 8            # 每个社区展示的代表性概念数

TOO_FEW_TERMS_MESSAGE = "节点太少，暂无法分析间隙"
WELL_CONNECTED_MESSAGE = "你的知识图谱高度连通，没有发现明显的知识间隙！"


@dataclass
class CommunityAnalysis:
    communities: List[dict] = field(default_factory=list)  # [{"id", "size", "top_terms"}]
    pairs: List[dict] = field(default_factory=list)        # [{"community_a", "community_b", "links", "strength"}]
    message: str = ""

    @property
    def needs_ai(self) -> bool:
        """至少有两个社区才有「间隙」可分析；否则不必调用模型（也就不该扣费）"""
        return len(self.communities) >= 2


def detect_communities(term_ids: Iterable[int], edges: Sequence[Tuple[int, int, int]]) -> List[List[int]]:
    """Louvain 社区划分，按社区大小降序返回。固定随机种子，同一张图结果稳定。"""
    import networkx as nx

    graph = nx.Graph()
    graph.add_nodes_from(term_ids)
    graph.add_weighted_edges_from(edges)
    if graph.number_of_edges() == 0:
        return [[node] for node in graph.nodes]
    groups = nx.community.louvain_communities(graph, weight="weight", seed=42)
    return sorted((sorted(group) for group in groups), key=lambda group: (-len(group), group[0]))


def summarize(
    groups: List[List[int]],
    edges: Sequence[Tuple[int, int, int]],
    labels: Dict[int, str],
    doc_counts: Dict[int, int],
) -> CommunityAnalysis:
    significant = [group for group in groups if len(group) >= MIN_COMMUNITY_SIZE][:MAX_COMMUNITIES]
    if len(significant) < 2:
        return CommunityAnalysis(
            communities=[_community(i, group, labels, doc_counts, {}) for i, group in enumerate(significant)],
            message=WELL_CONNECTED_MESSAGE,
        )

    community_of = {term_id: index for index, group in enumerate(significant) for term_id in group}
    inner_degree: Dict[int, int] = {}
    links: Dict[Tuple[int, int], int] = {}
    for source, target, weight in edges:
        a, b = community_of.get(source), community_of.get(target)
        if a is None or b is None:
            continue
        if a == b:
            inner_degree[source] = inner_degree.get(source, 0) + weight
            inner_degree[target] = inner_degree.get(target, 0) + weight
        else:
            key = (min(a, b), max(a, b))
            links[key] = links.get(key, 0) + weight

    communities = [_community(i, group, labels, doc_counts, inner_degree) for i, group in enumerate(significant)]
    pairs = []
    for a, b in combinations(range(len(significant)), 2):
        count = links.get((a, b), 0)
        pairs.append({
            "community_a": a,
            "community_b": b,
            "links": count,
            # 按两边规模归一化：大社区之间偶尔几条连接，仍然算联系薄弱
            "strength": round(count / (len(significant[a]) * len(significant[b])), 4),
        })
    pairs.sort(key=lambda pair: (pair["strength"], pair["links"]))
    return CommunityAnalysis(communities=communities, pairs=pairs[:MAX_PAIRS])


def _community(index, group, labels, doc_counts, inner_degree) -> dict:
    # 代表性概念：社区内部连接最多的优先，其次是出现的文档多的
    ranked = sorted(group, key=lambda term_id: (-inner_degree.get(term_id, 0), -doc_counts.get(term_id, 0), term_id))
    return {
        "id": index,
        "size": len(group),
        "top_terms": [labels[term_id] for term_id in ranked[:TOP_TERMS] if labels.get(term_id)],
    }


async def analyze_term_communities(db: AsyncSession, user_id: int) -> CommunityAnalysis:
    terms = (await db.execute(
        select(KnowledgeTerm.id, KnowledgeTerm.display_term, KnowledgeTerm.term)
        .where(KnowledgeTerm.user_id == user_id)
    )).all()
    edges, doc_counts = await load_term_cooccurrence(db, user_id)

    # 只统计仍被文档引用的概念：已删除文档留下的节点不应影响分析
    labels = {
        term_id: format_display_term(display or term)
        for term_id, display, term in terms
        if doc_counts.get(term_id, 0) > 0
    }
    if len(labels) < MIN_TERMS:
        return CommunityAnalysis(message=TOO_FEW_TERMS_MESSAGE)

    edges = [(s, t, w) for s, t, w in edges if s in labels and t in labels]
    # Louvain 是纯 Python 计算，大图要几秒：放到线程里，不阻塞事件循环
    groups = await asyncio.to_thread(detect_communities, list(labels), edges)
    return summarize(groups, edges, labels, doc_counts)
