"""
示例文档

新用户打开应用是一片空白，不知道能做什么。本地版在数据目录还没有文档时、
线上版在用户注册时，写入几篇示例文章：摘要、关键术语、思维导图、闪卡和测验都是预先生成好的，
不配置 API Key 也能浏览；三篇文章的关键术语互有交叉，知识网络里能直接看到连线。

正文都短于 SHORT_TEXT_VECTOR_THRESHOLD 且类型为纯文本，问答走全文直读，
因此只要配置了对话模型就能直接提问，不依赖向量模型。

只写一次，用户删除示例后不会再出现：线上版只在注册时写入；本地版完成
（或发现已有文档）后在数据目录留下标记文件。设置 SAMPLE_DOCUMENTS_ENABLED=false 可关闭。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

from sqlalchemy import func, select, update

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"
MARKER_FILENAME = "samples_seeded"
DISPLAY_PREFIX = "示例 · "

# 写入顺序；最后一篇的时间最新，排在首页最前
SAMPLE_SLUGS = ("photosynthesis", "attention", "learning")


def load_sample(slug: str) -> dict:
    """读取一篇示例：正文来自 <slug>.md，摘要与学习产物来自 <slug>.json"""
    meta = json.loads((SAMPLES_DIR / f"{slug}.json").read_text(encoding="utf-8"))
    meta["text"] = (SAMPLES_DIR / f"{slug}.md").read_text(encoding="utf-8").strip()
    return meta


def _count_nodes(node: dict) -> int:
    return 1 + sum(_count_nodes(child) for child in node.get("children") or [])


async def seed_sample_documents(user_id: int) -> int:
    """为没有任何文档的用户写入示例文档，返回写入篇数（无需写入时为 0）"""
    if not settings.sample_documents_enabled:
        return 0

    import db_models
    from database import AsyncSessionLocal
    from document_service.core import format_summary_markdown
    from document_service.schema import DocumentSummaryResponse, KeyTerm
    from knowledge_graph.service import update_knowledge_graph_for_document

    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(func.count()).select_from(db_models.Document).where(db_models.Document.user_id == user_id)
        )
        if existing:
            return 0

        created: List[Tuple[db_models.Document, List[KeyTerm]]] = []
        # 首页按最近访问时间倒序：时间错开，「如何高效学习」排在最前。
        now = datetime.now(timezone.utc)
        for index, slug in enumerate(SAMPLE_SLUGS):
            stamp = now - timedelta(seconds=len(SAMPLE_SLUGS) - index)
            sample = load_sample(slug)
            key_terms = [KeyTerm(**item) for item in sample["key_terms"]]
            summary = DocumentSummaryResponse(overview=sample["overview"], key_terms=key_terms)
            document = db_models.Document(
                user_id=user_id,
                filename=f"{sample['title']}.md",
                display_name=f"{DISPLAY_PREFIX}{sample['title']}",
                extracted_title=sample["title"],
                file_type="text",
                original_text=sample["text"],
                summary=format_summary_markdown(summary),
                file_size=len(sample["text"].encode("utf-8")),
                chunk_count=0,
                # 短纯文本不向量化，问答直接读全文（与上传短文本的处理一致）
                vector_store_id="text_direct_pending",
                processing_status="completed",
                processing_progress=100,
                created_at=stamp,
                last_accessed_at=stamp,
            )
            db.add(document)
            await db.flush()
            # 集合名要用到自增 id，只能插入后再改。用 Core UPDATE 并显式带上时间：
            # ORM 会把「值没变」的列从 UPDATE 中省略，last_accessed_at 的 onupdate 就会把时间刷成当前时刻
            vector_store_id = f"text_direct_{document.id}"
            await db.execute(
                update(db_models.Document)
                .where(db_models.Document.id == document.id)
                .values(vector_store_id=vector_store_id, last_accessed_at=stamp)
            )

            db.add(db_models.DocumentMindmap(
                document_id=document.id, user_id=user_id, title="思维导图",
                mindmap_data=sample["mindmap"], node_count=_count_nodes(sample["mindmap"]["root"]),
            ))
            db.add(db_models.DocumentFlashcards(
                document_id=document.id, user_id=user_id, title="闪卡",
                flashcards_data={"flashcards": sample["flashcards"]}, card_count=len(sample["flashcards"]),
            ))
            db.add(db_models.DocumentQuiz(
                document_id=document.id, user_id=user_id, title="测验",
                quiz_data={"quiz": sample["quiz"]}, question_count=len(sample["quiz"]),
            ))
            created.append((document, key_terms))
        await db.commit()

        # 知识网络：与正常处理文档时同一入口（内部自行提交）
        for document, key_terms in created:
            await update_knowledge_graph_for_document(db, document, key_terms)

    logger.info("📚 已写入 %d 篇示例文档 (user_id=%s)", len(created), user_id)
    return len(created)


async def seed_local_samples_once(user_id: int) -> int:
    """本地版启动时调用：每个数据目录只尝试一次，已在使用的数据目录不插入示例"""
    if not settings.sample_documents_enabled:
        return 0
    marker = settings.data_dir / MARKER_FILENAME
    if marker.exists():
        return 0
    count = await seed_sample_documents(user_id)
    marker.write_text(f"seeded {count} sample documents\n", encoding="utf-8")
    return count
