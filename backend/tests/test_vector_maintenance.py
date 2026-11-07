"""孤立向量集合清理：只删「文档管线创建、无人引用、且足够老」的集合"""
import time

import pytest


@pytest.mark.asyncio
async def test_sweep_removes_only_old_unreferenced_pipeline_collections(client):
    import db_models
    from database import AsyncSessionLocal
    from document_service.vector_maintenance import sweep_orphan_collections
    from document_service.vector_store import vector_store_manager

    chroma = vector_store_manager.client
    old = int(time.time()) - 7 * 3600

    def make(name, **meta):
        chroma.get_or_create_collection(name=name, metadata={"document_id": "1", **meta})

    make("doc_901_aaaaaaaa", created_at=old)               # 孤立且够老 → 删除
    make("doc_902_bbbbbbbb", created_at=int(time.time()))  # 刚创建，可能正在写入 → 保留
    make("doc_903_cccccccc")                               # 旧版本创建、无创建时间 → 自动清理跳过
    make("doc_904_dddddddd", created_at=old)               # 仍被文档引用 → 保留
    make("notes_cache_1", created_at=old)                  # 非文档管线命名 → 不碰

    async with AsyncSessionLocal() as db:
        db.add(db_models.Document(
            user_id=1, filename="kept.txt", file_type="txt", original_text="x",
            vector_store_id="doc_904_dddddddd",
        ))
        await db.commit()

    assert await sweep_orphan_collections(AsyncSessionLocal, dry_run=True) == ["doc_901_aaaaaaaa"]
    assert set(await sweep_orphan_collections(AsyncSessionLocal, include_legacy=True, dry_run=True)) == {
        "doc_901_aaaaaaaa", "doc_903_cccccccc",
    }

    await sweep_orphan_collections(AsyncSessionLocal)
    remaining = {
        item if isinstance(item, str) else item.name for item in chroma.list_collections()
    }
    assert "doc_901_aaaaaaaa" not in remaining
    assert {"doc_902_bbbbbbbb", "doc_903_cccccccc", "doc_904_dddddddd", "notes_cache_1"} <= remaining


def test_new_collections_record_creation_time(client):
    from document_service.vector_store import vector_store_manager

    name = vector_store_manager.create_collection(905)
    metadata = vector_store_manager.client.get_collection(name).metadata
    assert abs(metadata["created_at"] - time.time()) < 60
    vector_store_manager.delete_collection(name)
