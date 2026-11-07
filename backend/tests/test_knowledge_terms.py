"""文档关键词编辑：增删后保存必须真正落库（会话 autoflush=False，清理孤立节点前要先 flush）"""
import asyncio
import uuid


def _create_document():
    async def create():
        from database import AsyncSessionLocal
        from db_models import Document

        async with AsyncSessionLocal() as db:
            document = Document(
                user_id=1, filename="keywords.txt", file_type="text", original_text="x",
                vector_store_id=f"test-{uuid.uuid4().hex}", processing_status="completed",
            )
            db.add(document)
            await db.commit()
            return document.id

    return asyncio.run(create())


def _save(client, document_id, *terms):
    response = client.put(
        f"/api/knowledge-graph/document/{document_id}/terms",
        json={"terms": [{"term": term} for term in terms]},
    )
    assert response.status_code == 200, response.text


def _saved(client, document_id):
    response = client.get(f"/api/knowledge-graph/document/{document_id}/terms")
    assert response.status_code == 200, response.text
    return [item["term"] for item in response.json()["data"]]


def test_added_and_removed_keywords_are_saved(client):
    document_id = _create_document()

    _save(client, document_id, "Academic procrastination / 学业拖延", "School climate / 校园氛围")
    assert _saved(client, document_id) == ["Academic procrastination / 学业拖延", "School climate / 校园氛围"]

    # 删除：剩下的关键词只属于这篇文档，不能被当成孤立节点清掉
    _save(client, document_id, "Academic procrastination / 学业拖延")
    assert _saved(client, document_id) == ["Academic procrastination / 学业拖延"]

    # 新增；同一概念的另一种写法归一化后重复，只保留一条（不能触发 uq_doc_term）
    _save(client, document_id, "Academic procrastination / 学业拖延", "Cross-lagged analysis / 交叉滞后分析", "学业拖延")
    assert _saved(client, document_id) == ["Academic procrastination / 学业拖延", "Cross-lagged analysis / 交叉滞后分析"]


def test_keywords_shared_with_another_document_survive_removal(client):
    first, second = _create_document(), _create_document()
    _save(client, first, "School climate / 校园氛围")
    _save(client, second, "School climate / 校园氛围", "Ecological systems theory / 生态系统理论")

    _save(client, second)
    assert _saved(client, second) == []
    assert _saved(client, first) == ["School climate / 校园氛围"]
