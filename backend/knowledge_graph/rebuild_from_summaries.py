import asyncio
import os
import sys
import re
from dataclasses import dataclass
from typing import List

from sqlalchemy import select
from utils.logger import get_logger
logger = get_logger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from database import AsyncSessionLocal
from db_models import Document
from .service import update_knowledge_graph_for_document


@dataclass
class KeyTermStub:
    """与 document_service.schema.KeyTerm 兼容的轻量结构，
    只需 term / description 两个属性即可被 update_knowledge_graph_for_document 使用。"""
    term: str
    description: str


TERM_BLOCK_HEADER = "## 🔑 关键术语"


def parse_key_terms_from_summary(summary: str) -> List[KeyTermStub]:
    """从 markdown 摘要中的"## 🔑 关键术语"段解析 KeyTerm 列表。

    约定格式示例：
    - **Pretrained transformers** / `预训练转换器`
      经过大规模数据预训练的神经网络模型...
    """
    if not summary:
        return []

    lines = summary.splitlines()
    try:
        start_idx = next(i for i, line in enumerate(lines) if TERM_BLOCK_HEADER in line)
    except StopIteration:
        return []

    i = start_idx + 1
    terms: List[KeyTermStub] = []

    bullet_pattern = re.compile(r"^-\s+(.*)$")

    while i < len(lines):
        line = lines[i].rstrip()
        if line.startswith("## ") and TERM_BLOCK_HEADER not in line:
            break

        m = bullet_pattern.match(line)
        if m:
            title_line = m.group(1).strip()
            desc = ""
            if i + 1 < len(lines):
                desc = lines[i + 1].strip()

            term_text = title_line

            if " / " in term_text:
                term_text = term_text.replace("**", "")
            else:
                term_text = term_text.replace("**", "")
                term_text = term_text.replace("`", "")
                term_text = term_text.strip("- ")

            if term_text:
                terms.append(KeyTermStub(term=term_text, description=desc or term_text))
                i += 2
                continue

        i += 1

    return terms


async def rebuild_for_user(user_id: int | None = None) -> None:
    """从 documents.summary 中解析关键词，重建知识图谱。

    如果传入 user_id，则只处理该用户的文档。
    直接复用 service.update_knowledge_graph_for_document，避免逻辑重复。
    """
    async with AsyncSessionLocal() as db:
        q = select(Document).where(Document.summary.isnot(None))
        if user_id is not None:
            q = q.where(Document.user_id == user_id)

        result = await db.execute(q)
        docs: List[Document] = result.scalars().all()

        logger.info(f"发现 {len(docs)} 篇带 summary 的文档，开始重建知识图谱...")

        for doc in docs:
            key_terms = parse_key_terms_from_summary(doc.summary or "")
            if not key_terms:
                logger.info(f"[跳过] 文档 {doc.id} ({doc.filename}) 未解析到关键术语")
                continue

            logger.info(f"[处理] 文档 {doc.id} ({doc.filename})，解析出 {len(key_terms)} 个术语")
            await update_knowledge_graph_for_document(db, doc, key_terms)

        logger.info("✅ 重建完成")


if __name__ == "__main__":
    asyncio.run(rebuild_for_user())
