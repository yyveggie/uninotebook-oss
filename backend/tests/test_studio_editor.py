"""创作空间：AI 编辑失败必须退款且不能把报错当成结果，输入有上限，续写只要新内容；草稿文件名校验"""
from types import SimpleNamespace

import pytest

import credit_service
from credit_service import CreditCost
from document_service.editor import AIEditorService
from routes.editor import EDITOR_MAX_SELECTION_CHARS, EDITOR_STREAM_ERROR_MARKER


class RecordingCredits:
    """本地版的积分服务是空实现，这里记录扣费与退款调用"""
    calls = []

    def __init__(self, db):
        pass

    async def deduct_credits(self, user_id, amount, transaction_type, description=None, **kwargs):
        RecordingCredits.calls.append(("deduct", amount, transaction_type))

    async def refund_credits(self, user_id, amount, original_transaction_type, reason=None, reference_id=None):
        RecordingCredits.calls.append(("refund", amount, original_transaction_type))


class FakeLLM:
    def __init__(self):
        self.chunks = []
        self.fail_at = None
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        for index, chunk in enumerate(self.chunks):
            if index == self.fail_at:
                raise RuntimeError("upstream exploded")
            yield SimpleNamespace(content=chunk)
        if self.fail_at == len(self.chunks):
            raise RuntimeError("upstream exploded")


@pytest.fixture
def llm(monkeypatch):
    RecordingCredits.calls = []
    monkeypatch.setattr(credit_service, "CreditService", RecordingCredits)
    fake = FakeLLM()
    monkeypatch.setattr(AIEditorService, "_create_llm", lambda self: fake)
    return fake


def _edit(client, **body):
    return client.post("/api/editor/ai-edit", json={"selection": "原文", "action_key": "expand", **body})


def test_successful_edit_streams_text_and_keeps_the_charge(client, llm):
    llm.chunks = ["扩写后", "的内容"]
    response = _edit(client)
    assert response.status_code == 200
    assert response.text == "扩写后的内容"
    # 开源版不计费，CreditCost 都是 0；按常量比较，两个版本都适用
    assert RecordingCredits.calls == [("deduct", CreditCost.QA_BASE, "editor")]


def test_failure_before_any_output_is_an_error_and_refunded(client, llm):
    llm.fail_at = 0
    response = _edit(client)
    assert response.status_code == 502
    assert "编辑失败" not in response.text.split('"message"')[0]  # 不是 200 的「结果」
    assert response.json()["message"] == "AI 编辑失败，请稍后重试"
    assert [call[0] for call in RecordingCredits.calls] == ["deduct", "refund"]


def test_no_output_is_an_error_and_refunded(client, llm):
    response = _edit(client)
    assert response.status_code == 502
    assert [call[0] for call in RecordingCredits.calls] == ["deduct", "refund"]


@pytest.mark.parametrize("chunks, fail_at", [
    (["一部分"], 1),       # 输出到一半中断
    (["\n", "  "], None),  # 只有空白
])
def test_broken_or_empty_stream_ends_with_error_marker_and_refund(client, llm, chunks, fail_at):
    llm.chunks, llm.fail_at = chunks, fail_at
    response = _edit(client)
    assert response.status_code == 200
    assert response.text.endswith(EDITOR_STREAM_ERROR_MARKER)
    assert "upstream exploded" not in response.text
    assert [call[0] for call in RecordingCredits.calls] == ["deduct", "refund"]


def test_oversized_selection_is_rejected_before_charging(client, llm):
    response = _edit(client, selection="字" * (EDITOR_MAX_SELECTION_CHARS + 1))
    assert response.status_code == 422
    assert RecordingCredits.calls == []


def test_client_cannot_pick_the_model(client, llm):
    # 旧客户端仍可能带 model 字段：忽略即可，模型按功能映射选择
    llm.chunks = ["好"]
    assert _edit(client, model="qwen-preview").status_code == 200


def test_continue_asks_only_for_new_text_and_works_at_the_cursor(client, llm):
    llm.chunks = ["新的一段"]
    response = _edit(client, selection="", action_key="continue", context_before="前文已经写到这里。")
    assert response.status_code == 200
    system_prompt, user_prompt = (message.content for message in llm.messages)
    assert "只输出新写的内容" in system_prompt
    assert "前文已经写到这里" in user_prompt


def test_continue_with_nothing_to_continue_is_rejected_before_charging(client, llm):
    response = _edit(client, selection="", action_key="continue", context_before="  ")
    assert response.status_code == 400
    assert RecordingCredits.calls == []


def _create_draft(client, filename="草稿.md"):
    response = client.post("/api/editor/drafts", json={"filename": filename, "content": "正文"})
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_rename_draft_trims_and_validates(client):
    draft = _create_draft(client)
    url = f"/api/editor/drafts/{draft['id']}"

    response = client.put(url, json={"filename": "  论文初稿.tex \n"})
    assert response.status_code == 200
    assert response.json()["data"]["filename"] == "论文初稿.tex"

    assert client.put(url, json={"filename": "   "}).status_code == 422
    assert client.put(url, json={"filename": "名" * 256}).status_code == 422
    # 只改名不动正文
    drafts = {d["id"]: d for d in client.get("/api/editor/drafts").json()["data"]}
    assert drafts[draft["id"]]["content"] == "正文"


def test_long_imported_filename_is_truncated(client):
    assert len(_create_draft(client, filename="长" * 300)["filename"]) == 255
