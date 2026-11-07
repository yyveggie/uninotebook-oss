"""知识网络：关键词归一化、节点合并 / 改名 / 批量删除、文档删除后的同步、灵感对话、知识间隙与 AI 整理的辅助逻辑"""
import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest


def _run(coro):
    return asyncio.run(coro)


def _create_document(title=None):
    async def create():
        from database import AsyncSessionLocal
        from db_models import Document

        async with AsyncSessionLocal() as db:
            document = Document(
                user_id=1, filename=f"{uuid.uuid4().hex}.txt", file_type="text", original_text="x",
                vector_store_id=f"test-{uuid.uuid4().hex}", processing_status="completed",
                file_hash=uuid.uuid4().hex, extracted_title=title,
            )
            db.add(document)
            await db.commit()
            return document.id

    return _run(create())


def _save(client, document_id, *terms):
    response = client.put(
        f"/api/knowledge-graph/document/{document_id}/terms",
        json={"terms": [{"term": term} for term in terms]},
    )
    assert response.status_code == 200, response.text


def _terms(client, document_id):
    response = client.get(f"/api/knowledge-graph/document/{document_id}/terms")
    assert response.status_code == 200, response.text
    return {item["term"]: item["id"] for item in response.json()["data"]}


def _graph_labels(client, prefix):
    graph = client.get("/api/knowledge-graph/nodes-edges").json()
    nodes = graph.get("nodes", []) + graph.get("orphan_nodes", [])
    return {node["label"]: node["document_count"] for node in nodes if node["label"].startswith(prefix)}


def _db_terms(prefix):
    async def load():
        from database import AsyncSessionLocal
        from sqlalchemy import select
        from knowledge_graph.models import KnowledgeTerm

        async with AsyncSessionLocal() as db:
            rows = (await db.execute(
                select(KnowledgeTerm.display_term, KnowledgeTerm.document_count)
                .where(KnowledgeTerm.display_term.like(f"{prefix}%"))
            )).all()
            return dict(rows)

    return _run(load())


# ---------- 归一化 ----------

def test_curly_quoted_keywords_are_the_same_concept(client):
    document = _create_document()
    _save(client, document, "归一测试", "“归一测试”", "「归一测试」")
    assert list(_terms(client, document)) == ["归一测试"]


def test_renormalize_merges_legacy_quote_duplicates(client):
    """旧数据里因引号处理失效留下的重复节点，打开知识网络时调用的整理接口会把它们合并"""
    first, second = _create_document(), _create_document()

    async def seed_legacy():
        from database import AsyncSessionLocal
        from knowledge_graph.models import KnowledgeTerm, DocumentKnowledgeTerm

        async with AsyncSessionLocal() as db:
            clean = KnowledgeTerm(user_id=1, term="遗留重复", display_term="遗留重复", document_count=1)
            legacy = KnowledgeTerm(user_id=1, term="遗留重复”", display_term="“遗留重复”", document_count=1)
            db.add_all([clean, legacy])
            await db.flush()
            db.add_all([
                DocumentKnowledgeTerm(user_id=1, document_id=first, term_id=clean.id),
                DocumentKnowledgeTerm(user_id=1, document_id=second, term_id=legacy.id),
            ])
            await db.commit()

    _run(seed_legacy())
    response = client.post("/api/knowledge-graph/maintenance/renormalize-terms")
    assert response.status_code == 200, response.text
    assert response.json()["merged_nodes"] >= 1
    assert _db_terms("遗留重复") == {"遗留重复": 2}
    assert list(_terms(client, second)) == ["遗留重复"]


# ---------- 合并 ----------

def test_batch_merge_into_one_target_keeps_one_link_per_document(client):
    shared, other = _create_document(), _create_document()
    _save(client, shared, "MergeT one", "MergeT two", "MergeT unrelated")
    _save(client, other, "MergeT target", "MergeT unrelated")
    ids = {**_terms(client, shared), **_terms(client, other)}

    response = client.post("/api/knowledge-graph/term/merge-batch", json={"merges": [
        {"source_id": ids["MergeT one"], "target_id": ids["MergeT target"]},
        {"source_id": ids["MergeT two"], "target_id": ids["MergeT target"]},
    ]})
    assert response.status_code == 200, response.text
    assert response.json()["merged"] == 2
    assert sorted(_terms(client, shared)) == ["MergeT target", "MergeT unrelated"]
    assert _db_terms("MergeT target") == {"MergeT target": 2}


def test_batch_merge_cycle_keeps_one_of_the_two_nodes(client):
    document = _create_document()
    _save(client, document, "Cycle alpha", "Cycle beta", "Cycle gamma")
    ids = _terms(client, document)

    response = client.post("/api/knowledge-graph/term/merge-batch", json={"merges": [
        {"source_id": ids["Cycle alpha"], "target_id": ids["Cycle beta"]},
        {"source_id": ids["Cycle beta"], "target_id": ids["Cycle alpha"]},
    ]})
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert "skipped" not in results[0] and results[1]["skipped"] == "same node"
    assert sorted(_terms(client, document)) == ["Cycle beta", "Cycle gamma"]


def test_batch_merge_chain_lands_on_the_last_target(client):
    first, second = _create_document(), _create_document()
    _save(client, first, "Chain a", "Chain other")
    _save(client, second, "Chain b", "Chain c")
    ids = {**_terms(client, first), **_terms(client, second)}

    response = client.post("/api/knowledge-graph/term/merge-batch", json={"merges": [
        {"source_id": ids["Chain a"], "target_id": ids["Chain b"]},
        {"source_id": ids["Chain b"], "target_id": ids["Chain c"]},
    ]})
    assert response.status_code == 200, response.text
    assert sorted(_terms(client, first)) == ["Chain c", "Chain other"]
    assert sorted(_terms(client, second)) == ["Chain c"]
    assert _db_terms("Chain c") == {"Chain c": 2}


def test_single_merge_reports_errors(client):
    document = _create_document()
    _save(client, document, "Single x", "Single y")
    ids = _terms(client, document)
    url = "/api/knowledge-graph/term/merge"
    assert client.post(url, json={"source_id": ids["Single x"], "target_id": ids["Single x"]}).status_code == 400
    assert client.post(url, json={"source_id": ids["Single x"], "target_id": 99999999}).status_code == 404
    response = client.post(url, json={"source_id": ids["Single x"], "target_id": ids["Single y"]})
    assert response.status_code == 200, response.text
    assert response.json()["document_count"] == 1
    assert list(_terms(client, document)) == ["Single y"]


# ---------- 改名 / 描述 / 批量删除 ----------

def test_rename_onto_an_existing_name_asks_to_merge(client):
    document = _create_document()
    _save(client, document, "Rename source", "Rename taken")
    ids = _terms(client, document)

    response = client.put(f"/api/knowledge-graph/term/{ids['Rename source']}", json={"display_term": "Rename taken"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "KG_409_TERM_EXISTS"
    assert body["details"]["existing_id"] == ids["Rename taken"]

    response = client.put(f"/api/knowledge-graph/term/{ids['Rename source']}", json={"display_term": "Rename fresh"})
    assert response.status_code == 200, response.text
    assert "Rename fresh" in _terms(client, document)


def test_description_is_kept_in_full_and_not_wiped_by_a_rename(client):
    document = _create_document()
    _save(client, document, "Describe me")
    term_id = _terms(client, document)["Describe me"]
    url = f"/api/knowledge-graph/term/{term_id}"

    long_description = "描述" * 200  # 400 个字符，旧实现会截到 255
    assert client.put(url, json={"display_term": "Describe me", "description": long_description}).status_code == 200
    assert client.put(url, json={"display_term": "Describe renamed"}).status_code == 200
    response = client.get(f"/api/knowledge-graph/document/{document}/terms")
    assert response.json()["data"][0]["description"] == long_description

    assert client.put(url, json={"description": "长" * 2001}).status_code == 400


def test_batch_delete_removes_nodes_in_one_request(client):
    document = _create_document()
    _save(client, document, "Bulk one", "Bulk two", "Bulk keep")
    ids = _terms(client, document)

    response = client.post("/api/knowledge-graph/term/delete-batch", json={
        "term_ids": [ids["Bulk one"], ids["Bulk two"], 99999999],
    })
    assert response.status_code == 200, response.text
    assert sorted(response.json()["deleted_ids"]) == sorted([ids["Bulk one"], ids["Bulk two"]])
    assert response.json()["missing_ids"] == [99999999]
    assert list(_terms(client, document)) == ["Bulk keep"]


# ---------- 文档删除 / 重新解析 ----------

def test_deleting_a_document_recounts_and_drops_unused_terms(client):
    keep, gone = _create_document(), _create_document()
    _save(client, keep, "Stale shared", "Stale keep")
    _save(client, gone, "Stale shared", "Stale gone")

    assert client.delete(f"/api/document/{gone}").status_code == 200
    assert _db_terms("Stale") == {"Stale shared": 1, "Stale keep": 1}
    assert _graph_labels(client, "Stale") == {"Stale shared": 1, "Stale keep": 1}


def test_reparsing_a_document_recounts_terms_it_no_longer_has(client):
    keep, reparsed = _create_document(), _create_document()
    _save(client, keep, "Reparse shared")
    _save(client, reparsed, "Reparse shared", "Reparse dropped")

    async def reparse():
        from database import AsyncSessionLocal
        from db_models import Document
        from knowledge_graph.service import update_knowledge_graph_for_document

        async with AsyncSessionLocal() as db:
            document = await db.get(Document, reparsed)
            await update_knowledge_graph_for_document(db, document, [SimpleNamespace(term="Reparse new", description=None)])

    _run(reparse())
    assert _db_terms("Reparse") == {"Reparse shared": 1, "Reparse new": 1}


# ---------- 灵感 ----------

def _create_moment(title="测试灵感"):
    async def create():
        from database import AsyncSessionLocal
        from knowledge_graph.models import EurekaMoment

        async with AsyncSessionLocal() as db:
            moment = EurekaMoment(user_id=1, title=title, content="内容", source_nodes=[{"id": 1, "label": "A"}])
            db.add(moment)
            await db.commit()
            return moment.id

    return _run(create())


def _add_history(moment_id, session_id, rounds):
    async def add():
        from database import AsyncSessionLocal
        from db_models import EurekaChatHistory

        async with AsyncSessionLocal() as db:
            rows = [
                EurekaChatHistory(eureka_id=moment_id, user_id=1, session_id=session_id, question=f"问题{i}", answer=f"回答{i}")
                for i in range(rounds)
            ]
            db.add_all(rows)
            await db.commit()
            return [row.id for row in rows]

    return _run(add())


class FakeChatLLM:
    def __init__(self, chunks=("灵感", "回答"), error=None):
        self.chunks, self.error, self.messages = chunks, error, None

    async def astream(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield SimpleNamespace(content=chunk)


@pytest.fixture
def chat_llm(monkeypatch):
    import llm_service

    holder = SimpleNamespace(llm=FakeChatLLM())
    monkeypatch.setattr(llm_service, "get_llm_for_feature", lambda *args, **kwargs: holder.llm)
    return holder


def _sse_events(text):
    return [json.loads(line[6:]) for line in text.split("\n\n") if line.startswith("data: ")]


def test_eureka_chat_uses_the_latest_rounds_and_returns_the_record_id(client, chat_llm):
    moment = _create_moment()
    _add_history(moment, "s-latest", 25)

    response = client.post(f"/api/knowledge-graph/eureka/{moment}/chat", json={"question": "继续", "session_id": "s-latest"})
    assert response.status_code == 200
    events = _sse_events(response.text)
    assert "".join(e["data"] for e in events if e["event"] == "token") == "灵感回答"
    done = [e for e in events if e["event"] == "done"][0]["data"]
    assert done["message_id"]

    contents = [message.content for message in chat_llm.llm.messages]
    assert "问题24" in contents and "问题5" in contents and "问题4" not in contents  # 最近 20 轮
    assert contents[-1] == "继续"

    history = client.get(f"/api/knowledge-graph/eureka/{moment}/history", params={"session_id": "s-latest", "limit": 3}).json()["data"]
    assert [item["question"] for item in history] == ["问题23", "问题24", "继续"]


def test_eureka_chat_failure_is_reported_and_not_saved(client, chat_llm):
    moment = _create_moment()
    chat_llm.llm = FakeChatLLM(error=RuntimeError("upstream exploded"))

    response = client.post(f"/api/knowledge-graph/eureka/{moment}/chat", json={"question": "你好", "session_id": "s-fail"})
    events = _sse_events(response.text)
    assert events[-1]["event"] == "error"
    assert client.get(f"/api/knowledge-graph/eureka/{moment}/history", params={"session_id": "s-fail"}).json()["data"] == []


def test_eureka_chat_rejects_oversized_or_blank_questions(client, chat_llm):
    moment = _create_moment()
    url = f"/api/knowledge-graph/eureka/{moment}/chat"
    assert client.post(url, json={"question": "问" * 4001}).status_code == 422
    assert client.post(url, json={"question": "   "}).status_code == 422


def test_editing_an_eureka_message_deletes_it_and_what_followed(client):
    moment = _create_moment()
    first, second, third = _add_history(moment, "s-edit", 3)
    _add_history(moment, "s-other", 1)

    response = client.delete(f"/api/knowledge-graph/eureka/{moment}/history/after/{second}")
    assert response.status_code == 200, response.text
    assert response.json()["deleted_count"] == 2
    remaining = client.get(f"/api/knowledge-graph/eureka/{moment}/history", params={"session_id": "s-edit"}).json()["data"]
    assert [item["id"] for item in remaining] == [first]
    assert len(client.get(f"/api/knowledge-graph/eureka/{moment}/history", params={"session_id": "s-other"}).json()["data"]) == 1
    assert client.delete(f"/api/knowledge-graph/eureka/{moment}/history/after/{third}").status_code == 404


def test_deleting_an_eureka_removes_its_chats_and_notes(client):
    moment = _create_moment()
    _add_history(moment, "s-delete", 2)

    async def add_note():
        from database import AsyncSessionLocal
        from db_models import EurekaNote

        async with AsyncSessionLocal() as db:
            db.add(EurekaNote(eureka_id=moment, user_id=1, title="笔记", content="正文"))
            await db.commit()

    async def leftovers():
        from database import AsyncSessionLocal
        from sqlalchemy import func, select
        from db_models import EurekaChatHistory, EurekaNote

        async with AsyncSessionLocal() as db:
            chats = (await db.execute(select(func.count()).select_from(EurekaChatHistory).where(EurekaChatHistory.eureka_id == moment))).scalar_one()
            notes = (await db.execute(select(func.count()).select_from(EurekaNote).where(EurekaNote.eureka_id == moment))).scalar_one()
            return chats, notes

    _run(add_note())
    assert client.delete(f"/api/knowledge-graph/eureka/{moment}").status_code == 200
    assert _run(leftovers()) == (0, 0)


def test_saving_an_eureka_with_a_long_title_truncates_it(client):
    response = client.post("/api/knowledge-graph/eureka/save", json={
        "idea": "题" * 400, "reasoning": "理由", "source_nodes": [{"id": 1, "label": "A"}],
    })
    assert response.status_code == 200, response.text
    saved = [m for m in client.get("/api/knowledge-graph/eureka/list").json()["data"] if m["id"] == response.json()["data"]["id"]]
    assert len(saved[0]["title"]) == 255


def test_eureka_request_is_validated_before_anything_runs(client):
    url = "/api/knowledge-graph/eureka"
    assert client.post(url, json={"node_ids": [1]}).status_code == 400
    assert client.post(url, json={"node_ids": list(range(1, 8))}).status_code == 400
    assert client.post(url, json={"node_ids": "1,2"}).status_code == 400
    assert client.post(url, json={"node_ids": [99999998, 99999999]}).status_code == 400


# ---------- 知识间隙 / AI 整理的辅助逻辑 ----------

def test_communities_split_weakly_linked_clusters():
    from knowledge_graph.communities import detect_communities, summarize

    left, right = [1, 2, 3, 4], [11, 12, 13, 14]
    edges = [(a, b, 3) for group in (left, right) for i, a in enumerate(group) for b in group[i + 1:]]
    edges.append((4, 11, 1))  # 两个主题之间只有一条弱连接：只看连通分量会认为「没有间隙」

    groups = detect_communities(left + right, edges)
    assert sorted(map(sorted, groups)) == [left, right]

    labels = {term_id: f"T{term_id}" for term_id in left + right}
    analysis = summarize(groups, edges, labels, {term_id: 1 for term_id in labels})
    assert analysis.needs_ai
    assert analysis.pairs == [{"community_a": 0, "community_b": 1, "links": 1, "strength": 0.0625}]


def test_small_or_fully_connected_graphs_need_no_ai(client):
    from knowledge_graph.communities import detect_communities, summarize

    clique = [1, 2, 3, 4]
    edges = [(a, b, 1) for i, a in enumerate(clique) for b in clique[i + 1:]]
    analysis = summarize(detect_communities(clique, edges), edges, {i: str(i) for i in clique}, {})
    assert not analysis.needs_ai and analysis.message


def test_gap_output_is_cleaned_before_rendering():
    from tasks.generation_tasks import _clean_gaps

    gaps = _clean_gaps({"gaps": [
        {"community_a": "0", "community_b": 1, "bridge_concepts": "概念甲，概念乙", "explanation": "解释"},
        {"community_a": 0, "community_b": 0},
        {"community_a": 0, "community_b": 9},
        "not a gap",
    ]}, community_count=2)
    assert gaps == [{
        "community_a": 0, "community_b": 1, "theme_a": "", "theme_b": "",
        "bridge_concepts": ["概念甲", "概念乙"], "explanation": "解释", "suggested_topics": [],
    }]


def test_merge_suggestions_drop_cycles_self_merges_and_unknown_ids():
    from knowledge_graph.merge_candidates import sanitize_suggestions

    labels = {1: "A", 2: "B", 3: "C"}
    cleaned = sanitize_suggestions([
        {"source_id": 1, "target_id": 2, "reason": "同义"},
        {"source_id": 2, "target_id": 1, "reason": "同义"},
        {"source_id": 3, "target_id": 3, "reason": "自身"},
        {"source_id": 3, "target_id": 7, "reason": "未知"},
        {"source_id": 1, "target_id": 3, "reason": "重复的 source"},
        {"source_id": 3, "target_id": 2, "reason": "不建议合并"},
    ], labels)
    assert [(s["source_id"], s["target_id"]) for s in cleaned] == [(1, 2)]


def test_large_libraries_are_batched_by_similarity():
    from knowledge_graph import merge_candidates as mc

    terms = [(i, f"term {i}") for i in range(mc.FULL_LIST_LIMIT + 50)]

    async def embed(labels):
        # 每两个概念互为近邻：(0,1)、(2,3)…；其余方向两两正交
        vectors = []
        for index in range(len(labels)):
            vector = [0.0] * len(labels)
            vector[index // 2 * 2] = 1.0
            vector[index // 2 * 2 + 1] = 0.05 * (index % 2)
            vectors.append(vector)
        return vectors

    batches = _run(mc.plan_batches(terms, embed=embed))
    assert 1 <= len(batches) <= mc.MAX_BATCHES
    assert all(len(batch) <= mc.BATCH_SIZE for batch in batches)
    first = {term_id for term_id, _ in batches[0]}
    assert all((term_id ^ 1) in first for term_id in first)  # 相似的一对总在同一批

    async def broken(labels):
        raise RuntimeError("embedding service down")

    fallback = _run(mc.plan_batches(terms, embed=broken))
    assert sum(len(batch) for batch in fallback) == len(terms)
    assert _run(mc.plan_batches(terms[:10], embed=broken)) == [terms[:10]]


def test_knowledge_task_failure_refunds_and_explains(monkeypatch):
    import tasks.utils
    from tasks.generation_tasks import _KnowledgeTaskError, _run_knowledge_task

    refunds = []

    async def fake_refund(**kwargs):
        refunds.append(kwargs)
        return True

    monkeypatch.setattr(tasks.utils, "refund_task_credits_once", fake_refund)
    task = SimpleNamespace(request=SimpleNamespace(id="task-1"))

    async def failing():
        raise _KnowledgeTaskError("节点不足")

    result = _run(_run_knowledge_task(
        task, failing(), user_id=1, charged_credits=4, credit_type="ai_eureka",
        default_amount=4, fallback="失败", extra={},
    ))
    assert result == {"success": False, "message": "节点不足（本次积分已退还）"}
    assert refunds[0]["amount"] == 4 and refunds[0]["reference_id"] == "task:task-1"

    async def upstream():
        raise RuntimeError("secret provider detail")

    refunds.clear()
    result = _run(_run_knowledge_task(
        task, upstream(), user_id=1, charged_credits=0, credit_type="ai_eureka",
        default_amount=4, fallback="AI 灵感生成失败", extra={"suggestions": []},
    ))
    assert result["success"] is False and result["suggestions"] == []
    assert result["message"].startswith("AI 灵感生成失败")
    assert refunds == []  # 没有扣费（本地版）就不退款
