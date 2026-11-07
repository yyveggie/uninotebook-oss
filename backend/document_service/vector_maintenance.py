"""
孤立向量集合清理

文档 / 来源入库时先创建向量集合，写完向量后才把集合 ID 记到数据库。处理过程中
worker 被直接杀掉（OOM、重启部署）时，这个集合既不会被异常分支清理，也不再被任何
记录引用，会永久占用向量库空间。

本模块找出这类集合并删除，判定条件同时满足才删：
1. 名称符合文档管线的格式 `doc_<文档ID>_<8位十六进制>`（不碰其他用途的集合）；
2. 不被任何 Document / DocumentSource 的 vector_store_id 引用；
3. 创建时间早于阈值（默认 6 小时，远超任何入库任务的耗时），避免误删正在写入的集合。

早期版本创建的集合没有创建时间，无法判断是否仍在写入，自动清理一律跳过；
需要时用 `python scripts/cleanup_orphan_vectors.py --include-legacy` 手动清理。
"""
from __future__ import annotations

import re
import time
from typing import Callable, List, Tuple

from sqlalchemy import select

from utils.logger import get_logger

logger = get_logger(__name__)

COLLECTION_NAME_RE = re.compile(r"^doc_\d+_[0-9a-f]{8}$")
ORPHAN_MIN_AGE_SECONDS = 6 * 60 * 60


def _list_pipeline_collections(client) -> List[Tuple[str, dict]]:
    """列出文档管线创建的集合及其 metadata（兼容新旧版 chromadb 的返回格式）"""
    result = []
    for item in client.list_collections():
        # chromadb 0.6+ 只返回名称，更早的版本返回 Collection 对象
        name = item if isinstance(item, str) else getattr(item, "name", None)
        if not name or not COLLECTION_NAME_RE.match(name):
            continue
        metadata = None if isinstance(item, str) else getattr(item, "metadata", None)
        if metadata is None:
            try:
                metadata = client.get_collection(name).metadata
            except Exception:
                continue
        result.append((name, metadata or {}))
    return result


async def sweep_orphan_collections(
    session_factory: Callable,
    min_age_seconds: float = ORPHAN_MIN_AGE_SECONDS,
    include_legacy: bool = False,
    dry_run: bool = False,
) -> List[str]:
    """
    删除孤立的向量集合，返回被判定为孤立的集合名（dry_run 时只返回、不删除）。

    session_factory: 调用后得到可 `async with` 的数据库会话
    """
    import db_models
    from document_service.vector_store import vector_store_manager

    candidates = await vector_store_manager._run_in_executor(
        _list_pipeline_collections, vector_store_manager.client
    )
    if not candidates:
        return []

    async with session_factory() as db:
        referenced = set((await db.execute(
            select(db_models.Document.vector_store_id)
        )).scalars().all())
        referenced |= set((await db.execute(
            select(db_models.DocumentSource.vector_store_id).where(
                db_models.DocumentSource.vector_store_id.isnot(None)
            )
        )).scalars().all())

    now = time.time()
    orphans = []
    for name, metadata in candidates:
        if name in referenced:
            continue
        created_at = metadata.get("created_at")
        if created_at is None:
            if not include_legacy:
                continue
        elif now - float(created_at) < min_age_seconds:
            continue
        orphans.append(name)

    if dry_run:
        return orphans

    for name in orphans:
        try:
            await vector_store_manager._run_in_executor(vector_store_manager.delete_collection, name)
        except Exception as exc:
            logger.warning("⚠️ 删除孤立向量集合失败 %s: %s", name, exc)
    if orphans:
        logger.info("🧹 已清理 %d 个孤立向量集合（入库中途被中断遗留）", len(orphans))
    return orphans
