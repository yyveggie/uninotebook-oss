"""知识图谱共享工具函数.

集中管理术语归一化、展示格式化、缓存失效等跨模块复用的逻辑，
避免 api.py / service.py / rebuild_from_summaries.py 之间的代码重复。
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Tuple

from sqlalchemy import select, delete, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from cache_service import cache_delete

# 与 KnowledgeTerm.term / display_term 的列宽一致；归一化 key 必须先截断再用于查找，
# 否则超长术语每次都查不到已有节点，重复插入触发 uq_user_term
TERM_MAX_CHARS = 255
# 描述列是 Text，这里只防止异常超长的输入
TERM_DESCRIPTION_MAX_CHARS = 2000

# 中文引号用 \u 转义书写：直接写成弯引号的话，编辑器 / 格式化工具容易把它们换成直引号，
# 正则就不再处理中文引号（曾因此导致 “机器学习” 与 机器学习 成为两个节点）
_QUOTE_CHARS = "`'\"“”‘’「」『』＂＇"
_OUTER_QUOTES_START = re.compile(f"^[\\s{re.escape(_QUOTE_CHARS)}]+")
_OUTER_QUOTES_END = re.compile(f"[\\s{re.escape(_QUOTE_CHARS)}]+$")
_ANY_QUOTE = re.compile(f"[{re.escape(_QUOTE_CHARS)}]")


def _kg_cache_key(user_id: int) -> str:
    return f"kg:graph:{user_id}"


async def invalidate_kg_cache(user_id: int) -> None:
    await cache_delete(_kg_cache_key(user_id))


def strip_outer_quotes(text: str) -> str:
    """去掉字符串两侧多余的引号/反引号，用于清洗展示名称。

    只清理首尾的 ` ' " 以及中英文引号，不会影响中间的合法内容
    （例如 "Bayes' Rule" 中间的引号不会被移除）。
    """
    if not text:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    s = _OUTER_QUOTES_START.sub("", s)
    s = _OUTER_QUOTES_END.sub("", s)
    return s


def format_display_term(raw: str) -> str:
    """格式化展示名称：

    - 对 "英文 / 中文" 形式，保留英文部分，中文部分去掉外层引号；
    - 其他情况，整体去掉首尾多余引号。
    """
    if not raw:
        return ""

    s = str(raw).strip()
    if not s:
        return ""

    parts = re.split(r"\s*/\s*", s)
    if len(parts) >= 2:
        en = (parts[0] or "").strip()
        zh_part = " / ".join(p for p in parts[1:] if p is not None).strip()
        zh = strip_outer_quotes(zh_part)
        return f"{en} / {zh}" if zh else en

    return strip_outer_quotes(s)


def normalize_term(term: str) -> str:
    """对术语做归一化，用于在同一用户下合并"同一个概念"的不同写法。

    规则要点：
    - 激进地去除所有中英文引号，避免因引号格式不同导致无法合并；
    - 尽量从「英文 / 中文」或「中文 / 英文」等混合写法中提取出唯一的中文部分作为 key；
    - 如果完全没有中文，则对英文短语做小写、去括号/标点、合并空格和简单的单复数规约。
    """
    s = (term or "").strip()
    if not s:
        return ""

    s = _ANY_QUOTE.sub("", s)
    s = s.replace("，", ",").replace("、", ",")

    if "/" in s:
        parts = [p.strip() for p in s.split("/") if p.strip()]
        zh_parts = [p for p in parts if re.search(r"[\u4e00-\u9fff]", p)]

        if len(zh_parts) == 1:
            s = zh_parts[0]
        elif len(zh_parts) > 1:
            s = " ".join(zh_parts)
        else:
            s = parts[0]

    if re.search(r"[\u4e00-\u9fff]", s):
        match = re.search(r"[\u4e00-\u9fff].*", s)
        zh = match.group(0) if match else s
        zh = re.sub(r"[()（）]", "", zh)
        zh = re.sub(r"\s+", "", zh)
        return zh[:TERM_MAX_CHARS]

    s = s.lower()
    s = s.replace("-", " ")
    s = re.sub(r"[()（）\[\]【】]", " ", s)
    s = re.sub(r"[:,;·]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""

    if re.match(r"^[a-z\s]+$", s):
        tokens = s.split(" ")
        norm_tokens = []
        # 不应去 s 的后缀模式（会把 class→clas, status→statu 等搞坏）
        _no_strip_suffixes = {"ss", "us", "is", "os", "sis", "ous", "ies", "ses", "ves"}
        for tok in tokens:
            if len(tok) > 4 and tok.endswith("s"):
                if any(tok.endswith(sfx) for sfx in _no_strip_suffixes):
                    norm_tokens.append(tok)
                else:
                    norm_tokens.append(tok[:-1])
            else:
                norm_tokens.append(tok)
        s = " ".join(norm_tokens)

    return s[:TERM_MAX_CHARS]


async def recount_document_counts(
    db: AsyncSession,
    user_id: int,
    term_ids: Iterable[int],
) -> None:
    """根据 DocumentKnowledgeTerm 关系重新统计指定 term 的 document_count。"""
    from .models import KnowledgeTerm, DocumentKnowledgeTerm

    ids = set(term_ids)
    if not ids:
        return

    # 会话 autoflush=False：先 flush，否则统计不到调用方刚 add 的关系
    await db.flush()

    dt = DocumentKnowledgeTerm
    count_result = await db.execute(
        select(dt.term_id, func.count(dt.id))
        .where(dt.term_id.in_(ids))
        .group_by(dt.term_id)
    )
    counts: Dict[int, int] = dict(count_result.all())

    # 批量加载所有涉及的节点，避免逐个 db.get() 的 N+1
    nodes_result = await db.execute(
        select(KnowledgeTerm).where(KnowledgeTerm.id.in_(ids))
    )
    for node in nodes_result.scalars().all():
        node.document_count = counts.get(node.id, 0)


async def cleanup_orphan_terms(db: AsyncSession, user_id: int) -> None:
    """清理当前用户下不再被任何文档引用的知识图谱节点。"""
    from .models import KnowledgeTerm, DocumentKnowledgeTerm

    # 会话 autoflush=False：不先 flush 的话，刚 add 但未写入的关系看不见，
    # 只被它们引用的节点会被误删，提交时再插入这些关系就违反外键
    await db.flush()

    orphan_subq = (
        select(KnowledgeTerm.id)
        .outerjoin(
            DocumentKnowledgeTerm,
            KnowledgeTerm.id == DocumentKnowledgeTerm.term_id,
        )
        .where(
            KnowledgeTerm.user_id == user_id,
            DocumentKnowledgeTerm.id.is_(None),
        )
    )

    result = await db.execute(orphan_subq)
    orphan_ids = [row[0] for row in result.all()]
    if orphan_ids:
        await db.execute(
            delete(KnowledgeTerm).where(KnowledgeTerm.id.in_(orphan_ids))
        )


async def refresh_terms(db: AsyncSession, user_id: int, term_ids: Iterable[int]) -> None:
    """文档的关键词关系被删除或重建后调用：重算这些节点的 document_count，
    并删掉已不被任何文档引用的节点（否则灵感生成、AI 整理还会用到它们）。"""
    await recount_document_counts(db, user_id, term_ids)
    await cleanup_orphan_terms(db, user_id)


# 同一篇文章可能对应多条 Document 记录（重复上传、复用向量），
# 按 文件哈希 → 提取标题 → 显示名 → 文件名 归并后再统计共现
DOC_GROUPS_CTE = """WITH doc_groups AS (
        SELECT d.id AS doc_id,
               LOWER(TRIM(COALESCE(
                   NULLIF(d.file_hash, ''),
                   NULLIF(d.extracted_title, ''),
                   NULLIF(d.display_name, ''),
                   d.filename,
                   -- 用 CAST 而非 PostgreSQL 专有的类型转换语法，以兼容 SQLite
                   'id:' || CAST(d.id AS TEXT)
               ))) AS group_key
        FROM documents d
        WHERE d.user_id = :uid
    ),
    term_rels AS (
        SELECT dkt.term_id, dg.group_key
        FROM document_knowledge_terms dkt
        JOIN doc_groups dg ON dg.doc_id = dkt.document_id
        WHERE dkt.user_id = :uid
    )"""


async def load_term_cooccurrence(
    db: AsyncSession, user_id: int
) -> Tuple[List[Tuple[int, int, int]], Dict[int, int]]:
    """返回 (共现边 [(source, target, 共现文档数)], {term_id: 去重后的文档数})。"""
    edge_rows = (await db.execute(text(
        DOC_GROUPS_CTE + """
        SELECT a.term_id AS source, b.term_id AS target,
               COUNT(DISTINCT a.group_key) AS weight
        FROM term_rels a
        JOIN term_rels b ON a.group_key = b.group_key AND a.term_id < b.term_id
        GROUP BY a.term_id, b.term_id"""
    ), {"uid": user_id})).all()
    count_rows = (await db.execute(text(
        DOC_GROUPS_CTE + """
        SELECT term_id, COUNT(DISTINCT group_key) AS doc_count
        FROM term_rels
        GROUP BY term_id"""
    ), {"uid": user_id})).all()
    edges = [(int(source), int(target), int(weight)) for source, target, weight in edge_rows]
    counts = {int(term_id): int(doc_count) for term_id, doc_count in count_rows}
    return edges, counts
