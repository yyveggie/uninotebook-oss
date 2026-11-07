"""上传处理不再被摘要、长任务或重试拖住：专用执行线程、摘要后补、短超时、失败即终态"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest


def test_document_tasks_have_their_own_executor_lane():
    from runtime.tasks import _lane, get_local_executor

    for name in ("tasks.document.process_full", "tasks.document.process_url", "tasks.source.process_file"):
        assert _lane(name) == "document"
    for name in ("tasks.ppt.generate", "tasks.podcast.generate", "tasks.generation.report", "tasks.document.reindex"):
        assert _lane(name) == "general"
    assert get_local_executor("document") is not get_local_executor("general")


def test_document_tasks_fail_once_instead_of_auto_retrying():
    from tasks.document_tasks import process_document_full, process_url
    from tasks.generation_tasks import generate_flashcards
    from tasks.source_tasks import process_source_file

    for task in (process_document_full, process_url, process_source_file):
        assert tuple(task.autoretry_for) == (), task.name
        assert not hasattr(task, "_orig_run"), task.name
    # 其他任务的自动重试保持不变
    assert getattr(generate_flashcards, "_orig_run", None) is not None


def test_no_feature_is_routed_to_kimi_any_more():
    from llm_service.llm_config import FEATURE_MODEL_MAP

    assert not [f for f, key in FEATURE_MODEL_MAP.items() if key.startswith(("moonshot", "kimi"))]


@pytest.mark.asyncio
async def test_summary_call_has_a_hard_time_limit(monkeypatch):
    from document_service import core

    class HangingLLM:
        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            await asyncio.sleep(30)

    monkeypatch.setattr(core, "SUMMARY_TIMEOUT_SECONDS", 0.2)
    processor = core.DocumentProcessor(lc_client=HangingLLM(), db=None)
    started = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await processor._generate_full_summary_response("内容" * 100, "paper.txt")
    assert time.monotonic() - started < 2


@pytest.mark.asyncio
async def test_plain_text_reply_instead_of_structured_summary_is_a_failure_not_none():
    """模型用纯文本作答时 LangChain 返回 None；必须按失败处理，否则整个文档被判为处理失败"""
    from document_service import core

    class PlainTextLLM:
        def with_structured_output(self, *args, **kwargs):
            return self

        async def ainvoke(self, messages):
            return None

    processor = core.DocumentProcessor(lc_client=PlainTextLLM(), db=None)
    with pytest.raises(ValueError):
        await processor._generate_full_summary_response("内容" * 100, "paper.txt")
    summary = await processor.generate_document_summary("内容" * 100, "paper.txt")
    assert "paper.txt" in summary


@pytest.mark.asyncio
async def test_timeout_is_recorded_as_a_clear_failure(monkeypatch):
    from tasks import utils

    recorded = []

    async def capture(db, model_class, record_id, **fields):
        recorded.append(fields)

    monkeypatch.setattr(utils, "update_task_status", capture)
    with pytest.raises(asyncio.CancelledError):
        async with utils.TaskStatusUpdater(None, object, 1):
            raise asyncio.CancelledError()
    assert recorded[-1]["status"] == "failed"
    assert recorded[-1]["error_message"] == "处理超时，请稍后重试"


def test_document_is_ready_before_the_summary_and_summary_fills_in_later(client, credentials, monkeypatch):
    from document_service.core import DocumentProcessor
    from document_service.schema import DocumentFullSummaryResponse
    from document_service.vector_store import vector_store_manager as vectors
    from runtime import task_store
    from runtime.tasks import get_local_task_result

    credentials.set_credentials('openai', api_key='fake-key', model='test-chat', base_url='http://localhost:8888/v1')
    credentials.set_credentials('dashscope', base_url='http://localhost:8888/v1', model='test-embed', auth_required=False)

    release = threading.Event()
    summary_threads = []

    async def slow_summary(self, content, filename):
        summary_threads.append(threading.current_thread().name)
        while not release.is_set():
            await asyncio.sleep(0.05)
        return DocumentFullSummaryResponse(overview='Late summary written after the document was usable.', key_terms=[])

    class Embeddings:
        async def create(self, input, **kwargs):
            inputs = input if isinstance(input, list) else [input]
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.1, 0.2, 0.3]) for _ in inputs])

    monkeypatch.setattr(DocumentProcessor, '_generate_full_summary_response', slow_summary)
    monkeypatch.setattr(vectors, '_embedding_client', lambda config: SimpleNamespace(embeddings=Embeddings()))

    text = "Durable storage and reliable task recovery. " * 400
    response = client.post('/api/document/upload', files={'file': ('notes.txt', text.encode(), 'text/plain')})
    assert response.status_code == 200, response.text
    task = next(r for r in task_store.list_tasks() if r['name'] == 'tasks.document.process_full')
    document_id = task['kwargs']['document_id']

    try:
        deadline = time.monotonic() + 15
        status = {}
        while time.monotonic() < deadline:
            status = client.get(f'/api/document/{document_id}/status').json()['data']
            if status['processing_status'] in ('completed', 'failed'):
                break
            time.sleep(0.1)
        # 摘要还没返回，文档已经可用：上传页可以退出，前端继续等摘要
        assert status['processing_status'] == 'completed', status
        assert not status['summary']
        assert status['summary_pending'] is True
        assert summary_threads and summary_threads[0].startswith('uninb-document')
    finally:
        release.set()

    get_local_task_result(task['id']).get(timeout=15)
    status = client.get(f'/api/document/{document_id}/status').json()['data']
    assert 'Late summary' in status['summary']
    assert status['summary_pending'] is False


@pytest.mark.asyncio
async def test_stage_log_shows_where_processing_is_stuck(caplog):
    import logging
    from tasks.utils import log_stage

    caplog.set_level(logging.INFO)
    async with log_stage("解析 slow.pdf", "文档 1", heartbeat_seconds=0.05):
        await asyncio.sleep(0.2)
    with pytest.raises(RuntimeError):
        async with log_stage("向量化与摘要", "文档 1", heartbeat_seconds=10):
            raise RuntimeError("embedding endpoint unreachable")

    text = caplog.text
    assert "解析 slow.pdf：开始" in text and "解析 slow.pdf：仍在进行" in text and "解析 slow.pdf：完成" in text
    assert "向量化与摘要：失败" in text and "embedding endpoint unreachable" in text


@pytest.mark.asyncio
async def test_hung_parser_fails_fast_with_the_likely_cause(monkeypatch):
    from tasks import document_tasks

    class HangingService:
        async def extract_structured_elements_from_file(self, content, filename):
            await asyncio.sleep(30)

    monkeypatch.setattr(document_tasks, "PARSE_TIMEOUT_SECONDS", 0.1)
    started = time.monotonic()
    with pytest.raises(ValueError) as exc:
        await document_tasks._parse_with_limits(HangingService(), b"x", "slides.pptx", 1)
    assert time.monotonic() - started < 2
    assert "解析超过" in str(exc.value) and "Unstructured" in str(exc.value)
