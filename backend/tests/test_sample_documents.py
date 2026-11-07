"""首次启动的示例文档：内容合法、只写一次、用户已有文档时不插入。"""
import pytest

from runtime import samples


@pytest.mark.parametrize("slug", samples.SAMPLE_SLUGS)
def test_sample_content_matches_the_generator_schemas(slug):
    from document_service.core import SHORT_TEXT_VECTOR_THRESHOLD
    from document_service.schema import DocumentSummaryResponse, FlashcardsResponse, MindMapResponse, QuizResponse

    sample = samples.load_sample(slug)
    # 短纯文本走全文直读：只配对话模型就能提问，不需要向量模型
    assert len(sample["text"]) <= SHORT_TEXT_VECTOR_THRESHOLD
    DocumentSummaryResponse(overview=sample["overview"], key_terms=sample["key_terms"])
    FlashcardsResponse.model_validate({"flashcards": sample["flashcards"]})
    QuizResponse.model_validate({"quiz": sample["quiz"]})
    MindMapResponse.model_validate(sample["mindmap"])

    ids = []

    def walk(node):
        ids.append(node["id"])
        for child in node.get("children") or []:
            walk(child)

    walk(sample["mindmap"]["root"])
    assert len(ids) == len(set(ids))


def test_samples_share_terms_so_the_knowledge_network_has_links():
    terms = {slug: {t["term"] for t in samples.load_sample(slug)["key_terms"]} for slug in samples.SAMPLE_SLUGS}
    assert {"Working Memory / 工作记忆", "Cognitive Load / 认知负荷"} <= terms["learning"] & terms["attention"]
    # 光合作用自成一簇，「知识间隙」与「灵感」才有跨领域可连
    assert not terms["photosynthesis"] & (terms["learning"] | terms["attention"])


@pytest.mark.asyncio
async def test_seeding_happens_once_and_never_over_existing_documents(client, monkeypatch):
    # client：启动应用以建表
    import db_models
    from config import settings
    from database import AsyncSessionLocal
    from knowledge_graph.models import KnowledgeTerm
    from sqlalchemy import delete, select

    user_id = 4242
    marker = settings.data_dir / samples.MARKER_FILENAME
    marker.unlink(missing_ok=True)
    monkeypatch.setattr(settings, "sample_documents_enabled", True)
    async with AsyncSessionLocal() as db:
        db.add(db_models.User(id=user_id, email="samples@test.local", username="samples-test",
                              hashed_password="!", full_name="samples", is_active=True))
        await db.commit()

    try:
        assert await samples.seed_local_samples_once(user_id) == 3
        async with AsyncSessionLocal() as db:
            docs = (await db.execute(select(db_models.Document).where(db_models.Document.user_id == user_id))).scalars().all()
            assert sorted(d.display_name for d in docs) == sorted(
                samples.DISPLAY_PREFIX + samples.load_sample(s)["title"] for s in samples.SAMPLE_SLUGS)
            for doc in docs:
                assert doc.processing_status == "completed"
                assert doc.vector_store_id == f"text_direct_{doc.id}"
                assert doc.summary.startswith("## 📋 核心内容")
            newest = max(docs, key=lambda d: d.last_accessed_at)
            assert "如何高效学习" in newest.display_name  # 首页排在最前
            for model in (db_models.DocumentMindmap, db_models.DocumentFlashcards, db_models.DocumentQuiz):
                rows = (await db.execute(select(model).where(model.user_id == user_id))).scalars().all()
                assert len(rows) == 3
            shared = (await db.execute(select(KnowledgeTerm).where(
                KnowledgeTerm.user_id == user_id, KnowledgeTerm.display_term.contains("工作记忆")))).scalars().one()
            assert shared.document_count == 2

        # 只写一次：标记存在时不再写；标记丢失但已有文档时也不写
        assert await samples.seed_local_samples_once(user_id) == 0
        marker.unlink()
        assert await samples.seed_local_samples_once(user_id) == 0
        assert marker.exists()
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(db_models.Document).where(db_models.Document.user_id == user_id))
            await db.execute(delete(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id))
            await db.execute(delete(db_models.User).where(db_models.User.id == user_id))
            await db.commit()
        marker.unlink(missing_ok=True)


def test_interview_manager_is_ready_without_any_key(client):
    # 启动时没有任何 Key，之后在设置页填写：AI 面试不能因为启动时未创建管理器而报错
    from document_service.generators import interview

    assert interview.interview_manager is not None
