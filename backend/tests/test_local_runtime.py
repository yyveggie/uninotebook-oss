import asyncio
import json
import threading
from concurrent.futures import Future
from types import SimpleNamespace

import pytest


def test_core_only_startup(client):
    assert client.get("/api/system/capabilities").status_code == 200
    assert client.get("/api/auth/me").status_code == 200
    caps = client.get("/api/system/capabilities").json()["data"]["capabilities"]
    assert not caps["podcast"]["available"]
    assert client.post("/api/document/upload", files={"file": ("test.pdf", b"test")}).status_code == 503


@pytest.mark.parametrize("provider", ["moonshot", "deepseek", "openai", "qwen"])
def test_one_provider_covers_every_feature(credentials, provider):
    from llm_service.llm_config import FEATURE_MODEL_MAP, get_llm_for_feature
    credentials.set_credentials(provider, api_key="test-secret", model="custom-model", base_url="http://localhost:11434/v1")
    for feature in FEATURE_MODEL_MAP:
        llm = get_llm_for_feature(feature)
        assert llm.model_name == "custom-model"
        assert llm.openai_api_key.get_secret_value() == "test-secret"
        assert not llm.model_kwargs  # no Kimi/Qwen-specific parameters sent to custom endpoints
        # 只有明确是通义千问的卡片才发送 Qwen 的关闭思考参数
        assert (llm.extra_body or {}) == ({"enable_thinking": False} if provider == "qwen" else {})


def test_profile_persists_no_auth_and_preserves_key(client, credentials):
    from runtime.credentials import CredentialStore
    result = client.put("/api/system/credentials", json={"provider": "openai", "api_key": "keep-this-key", "model": "first"})
    assert result.status_code == 200
    result = client.put("/api/system/credentials", json={"provider": "openai", "model": "second"})
    assert result.status_code == 200
    assert credentials.api_key("openai") == "keep-this-key"
    result = client.put("/api/system/credentials", json={
        "provider": "dashscope", "base_url": "http://localhost:11434/v1", "model": "local-embed",
        "auth_required": False, "dimensions": 384,
    })
    assert result.status_code == 200
    restored = CredentialStore()
    assert restored.resolve("dashscope", "auth_required") is False
    assert restored.model("dashscope") == "local-embed"
    assert restored.api_key("dashscope") == "local-no-auth"
    assert restored.resolve("dashscope", "dimensions") == 384
    assert "keep-this-key" not in json.dumps(result.json())


def test_runtime_info_lists_paths_and_limits_without_secrets(client, credentials):
    from config import settings

    credentials.set_credentials("deepseek", api_key="info-secret-key")
    info = client.get("/api/system/info").json()["data"]
    assert info["data_dir"] == str(settings.data_dir)
    assert info["credentials_file"].endswith("credentials.json")
    by_env = {item["env"]: item["value"] for item in info["settings"]}
    assert by_env["MAX_UPLOAD_MB"] == settings.max_upload_mb
    assert "LOCAL_ALLOWED_HOSTS" in by_env
    assert "info-secret-key" not in json.dumps(info)


def test_connection_test_errors_carry_codes(client):
    assert client.post("/api/system/credentials/test", json={"provider": "image_gen"}).json()["code"] == "TEST_NOT_SUPPORTED"
    assert client.post("/api/system/credentials/test", json={"provider": "deepseek"}).json()["code"] == "CREDENTIALS_NOT_SAVED"


def _chat_session_rows(document_id):
    import db_models
    from database import AsyncSessionLocal
    from sqlalchemy import select

    async def query():
        async with AsyncSessionLocal() as db:
            return (await db.execute(select(db_models.DocumentChatHistory.session_id).where(
                db_models.DocumentChatHistory.document_id == document_id))).scalars().all()

    return asyncio.run(query())


def _create_document(text):
    import db_models
    from database import AsyncSessionLocal

    async def create():
        async with AsyncSessionLocal() as db:
            document = db_models.Document(user_id=1, filename="t.txt", file_type="text", original_text=text,
                                          vector_store_id="text_direct_test", processing_status="completed")
            db.add(document)
            await db.commit()
            return document.id

    return asyncio.run(create())


def test_timestamps_are_returned_with_a_timezone(client, credentials):
    from datetime import datetime

    document_id = _create_document("带时区的时间")
    client.post(f"/api/document/{document_id}/sessions", json={"session_id": "tz-session"})
    sessions = client.get(f"/api/document/{document_id}/sessions").json()["data"]
    parsed = datetime.fromisoformat(sessions[0]["last_activity_time"])
    assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def test_only_one_empty_session_survives_and_real_ones_are_kept(client, credentials):
    import db_models
    from database import AsyncSessionLocal

    document_id = _create_document("空会话清理")

    async def add_real_answer():
        async with AsyncSessionLocal() as db:
            db.add(db_models.DocumentChatHistory(
                user_id=1, document_id=document_id, session_id="real", question="问题", answer="真实回答", relevant_chunks=[]))
            await db.commit()

    client.post(f"/api/document/{document_id}/sessions", json={"session_id": "real"})
    asyncio.run(add_real_answer())
    for name in ("empty-a", "empty-b", "empty-c"):
        assert client.post(f"/api/document/{document_id}/sessions", json={"session_id": name}).status_code == 200
    assert set(_chat_session_rows(document_id)) == {"real", "empty-c"}
    # 重新打开已有的真实会话时，顺带清掉其余空会话
    client.post(f"/api/document/{document_id}/sessions", json={"session_id": "real"})
    assert set(_chat_session_rows(document_id)) == {"real"}


def test_rejected_key_gets_an_actionable_message(credentials):
    from runtime.features import looks_like_missing_credentials, public_model_error

    credentials.set_credentials("deepseek", api_key="k")
    credentials.set_credentials("dashscope", api_key="k")
    raw = Exception("Error code: 401 - Invalid API-key provided. For details, see: https://example.com#apikey-error")
    assert looks_like_missing_credentials(raw)
    message = public_model_error(raw)
    assert "设置" in message and "401" not in message and "Invalid" not in message
    assert public_model_error(Exception("连接超时")) == "连接超时"  # 本地版其余错误保留原文，便于排查


def test_request_sessions_and_key_rotation(credentials):
    from document_service.service import DocumentService
    from document_service.generators.interview import InterviewManager
    credentials.set_credentials("openai", api_key="old-key", model="test", base_url="http://localhost:8001/v1")
    a, b = DocumentService(db=object()), DocumentService(db=object())
    assert a is not b
    assert a.processor.db is a.db
    assert b.processor.db is b.db
    assert a.processor.db is not b.processor.db
    interview = InterviewManager()
    old = a.lc_client
    credentials.set_credentials("openai", api_key="new-key")
    assert a.lc_client is not old
    assert b.lc_client.openai_api_key.get_secret_value() == "new-key"
    assert interview.lc_client.openai_api_key.get_secret_value() == "new-key"


def test_future_terminal_states():
    from runtime.tasks import LocalAsyncResult
    for error, state in [(None, "SUCCESS"), (ValueError("failed"), "FAILURE")]:
        future = Future()
        future.set_exception(error) if error else future.set_result(42)
        result = LocalAsyncResult(f"unregistered-{state}", future)
        assert result.ready()
        assert result.state == state


def test_durable_tasks_progress_and_retry(client):
    from celery_app import celery_app
    from tasks.base import AsyncTask
    from runtime.tasks import get_local_task_result, retry_local_task
    from runtime import task_store

    @celery_app.task(bind=True, base=AsyncTask, name="tests.runtime.success", autoretry_for=())
    def success(self, user_id=1):
        self.update_state(state="PROGRESS", meta={"progress": 50, "stage": "testing"})
        return {"success": True, "data": {"flashcards": ["verified"]}}

    result = success.delay(user_id=1)
    assert result.get(timeout=5)["success"]
    assert result.state == "SUCCESS"
    assert get_local_task_result(result.id).result["success"]
    response = client.get(f"/api/generation-task/{result.id}/status")
    assert response.status_code == 200
    assert response.json()["data"] == ["verified"]
    public = client.get(f"/api/system/tasks/{result.id}").json()["data"]
    assert "args" not in public and "kwargs" not in public and "traceback" not in public

    @celery_app.task(bind=True, base=AsyncTask, name="tests.runtime.retry", autoretry_for=())
    def retrying(self):
        if self.request.retries == 0:
            self.retry(exc=ValueError("first attempt"), countdown=0.01)
        return {"success": True}

    retry_result = retrying.delay()
    assert retry_result.get(timeout=5)["success"]
    assert task_store.get(retry_result.id)["attempts"] == 2

    task_store.update(result.id, status="FAILURE", error="retry me")
    assert retry_local_task(result.id).get(timeout=5)["success"]


def test_running_cancel_and_timeout(client):
    from celery_app import celery_app
    from tasks.base import AsyncTask
    started = threading.Event()
    finished = threading.Event()

    @celery_app.task(bind=True, base=AsyncTask, name="tests.runtime.slow", autoretry_for=())
    def slow(self):
        async def work():
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished.set()
        return self.run_async(work())

    task = slow.delay()
    assert started.wait(3)
    assert client.post(f"/api/system/tasks/{task.id}/cancel").status_code == 200
    task._future.result(timeout=3)
    assert task.state == "REVOKED"
    assert finished.is_set()
    task = slow.apply_async(time_limit=0.1)
    task._future.result(timeout=3)
    assert task.state == "FAILURE"
    assert "超过" in task.result


def test_recovery_resumes_queued_and_marks_interrupted():
    from celery_app import celery_app
    from tasks.base import AsyncTask
    from runtime.tasks import initialize_local_tasks, get_local_task_result
    from runtime import task_store

    @celery_app.task(base=AsyncTask, name="tests.runtime.recover", autoretry_for=())
    def recover():
        return {"success": True}

    task_store.create("recover-pending", recover.name, [], {}, {})
    task_store.create("recover-started", recover.name, [], {}, {})
    task_store.update("recover-started", status="STARTED")
    initialize_local_tasks()
    assert get_local_task_result("recover-pending").get(timeout=5)["success"]
    assert task_store.get("recover-started")["status"] == "INTERRUPTED"


@pytest.mark.asyncio
async def test_schema_upgrade_preserves_rows_and_backs_up(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import MetaData, Table, Column, Integer, String
    from sqlalchemy.ext.asyncio import create_async_engine
    from config import settings
    from runtime.migrations import upgrade_local_database
    monkeypatch.setattr(settings, "local_data_dir", str(tmp_path))
    path = tmp_path / "uninotebook.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body VARCHAR NOT NULL)")
        db.execute("INSERT INTO notes VALUES (1, 'keep my notes')")
    metadata = MetaData()
    Table("notes", metadata, Column("id", Integer, primary_key=True), Column("body", String, nullable=False),
          Column("extra", String), Column("count", Integer, nullable=False, default=0))
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await upgrade_local_database(engine, metadata)
        await upgrade_local_database(engine, metadata)
    finally:
        await engine.dispose()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT body,count FROM notes").fetchone() == ("keep my notes", 0)
    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as db:
        assert db.execute("SELECT body FROM notes").fetchone()[0] == "keep my notes"
        assert "extra" not in {c[1] for c in db.execute("PRAGMA table_info(notes)")}


def test_storage_rejects_escape(credentials):
    from runtime.storage import local_storage
    with pytest.raises(ValueError):
        local_storage.resolve("../../outside")
    path = local_storage.save_document(1, 999, "../../unsafe.pdf", b"original bytes")
    assert path.name == "original.pdf"
    assert path.read_bytes() == b"original bytes"


def _sample_pdf():
    import io
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, DictionaryObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 40 700 Td (A study document about retrieval augmented generation and reliable local storage.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def test_pdf_upload_preview_and_reindex(client, credentials, monkeypatch):
    from document_service.core import DocumentProcessor
    from document_service.schema import DocumentFullSummaryResponse
    from document_service.vector_store import vector_store_manager as vectors, EmbeddingReindexRequired
    from runtime import task_store
    from runtime.tasks import get_local_task_result
    credentials.set_credentials('openai', api_key='fake-key', model='test-chat', base_url='http://localhost:8888/v1')
    credentials.set_credentials('dashscope', base_url='http://localhost:8888/v1', model='test-embed', auth_required=False)

    async def summary(self, content, filename):
        return DocumentFullSummaryResponse(overview='Verified summary from the original document.', key_terms=[])

    class Embeddings:
        async def create(self, input, **kwargs):
            inputs = input if isinstance(input, list) else [input]
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0, 0.1, 0.2, 0.3]) for _ in inputs])

    monkeypatch.setattr(DocumentProcessor, '_generate_full_summary_response', summary)
    monkeypatch.setattr(vectors, '_embedding_client', lambda config: SimpleNamespace(embeddings=Embeddings()))
    content = _sample_pdf()
    response = client.post('/api/document/upload', files={'file': ('paper.pdf', content, 'application/pdf')})
    assert response.status_code == 200, response.text
    tasks = [r for r in task_store.list_tasks() if r['name'] == 'tasks.document.process_full']
    assert tasks
    task = tasks[0]
    get_local_task_result(task['id']).get(timeout=10)
    document_id = task['kwargs']['document_id']
    info = client.get(f'/api/document/{document_id}/file/info')
    assert info.json()['data']['available'] is True
    assert client.get(f'/api/document/{document_id}/file/proxy').content == content
    assert client.get(f'/api/document/{document_id}/file/url').status_code == 200
    source_content = 'A secondary source explains durable storage and reliable task recovery in detail.'
    source = client.post(f'/api/document/{document_id}/sources', json={
        'source_type': 'text', 'source_name': 'Secondary source', 'content': source_content,
    })
    assert source.status_code == 200, source.text
    from runtime.storage import local_storage
    assert local_storage.source_text(1, source.json()['data']['id']) == source_content
    collection_id = next(c.name for c in vectors.client.list_collections() if c.name.startswith(f'doc_{document_id}_'))
    collection = vectors.client.get_collection(collection_id)
    assert collection.metadata['embedding_dimensions'] == 4
    credentials.set_credentials('dashscope', model='different-embed')
    with pytest.raises(EmbeddingReindexRequired):
        vectors.ensure_collection_compatible(collection)
    result = client.post('/api/system/embeddings/reindex')
    assert result.status_code == 200, result.text
    for task_id in result.json()['data']['task_ids']:
        get_local_task_result(task_id).get(timeout=10)
    assert client.get(f'/api/document/{document_id}/file/proxy').content == content


def test_saas_dispatch_is_unchanged(monkeypatch):
    from config import settings
    from runtime.tasks import LocalDispatchMixin
    from llm_service.llm_config import get_model_config
    monkeypatch.setattr(settings, 'deploy_mode', 'saas')
    class Base:
        def apply_async(self, *args, **kwargs):
            return args, kwargs
    class Dispatch(LocalDispatchMixin, Base):
        pass
    args, options = Dispatch().apply_async([1], {'user_id': 2}, countdown=12, queue='generation_heavy')
    assert args == ([1], {'user_id': 2})
    assert options == {'countdown': 12, 'queue': 'generation_heavy'}
    assert get_model_config('moonshot')['model'] == settings.moonshot_model


def test_qwen_card_has_its_own_defaults_and_model_option(client, credentials):
    from llm_service.llm_config import get_model_config, get_qa_llm

    credentials.set_credentials("qwen", api_key="qwen-key")
    described = client.get("/api/system/credentials").json()["data"]["qwen"]
    assert described["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert described["model"] == "qwen3-max"
    caps = client.get("/api/system/capabilities").json()["data"]["capabilities"]
    assert caps["chat"]["available"]

    # 通义千问与通用 OpenAI 兼容端点并存时，问答下拉可以分别选到
    credentials.set_credentials("openai", api_key="gateway-key", model="gateway-model", base_url="http://localhost:11434/v1")
    assert get_qa_llm("qwen").model_name == "qwen3-max"
    assert get_qa_llm("openai").model_name == "gateway-model"
    assert get_model_config("qwen")["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert client.put("/api/system/credentials", json={"provider": "qwen", "use_for_chat": True}).status_code == 200
    models = client.get("/api/models").json()["data"]
    assert {option["value"] for option in models["options"]} == {"qwen", "openai"}
    assert models["default"] == "qwen"


def test_default_provider_and_explicit_qa_selection(credentials):
    from llm_service.llm_config import get_llm_for_feature, get_qa_llm, get_model_config
    credentials.set_credentials('openai', api_key='openai-test', model='default-model', use_for_chat=True)
    credentials.set_credentials('deepseek', api_key='deepseek-test', model='selected-model')
    assert get_llm_for_feature('doc_parsing').model_name == 'default-model'
    assert get_model_config('moonshot')['base_url'] == 'https://api.openai.com/v1'
    assert get_qa_llm('deepseek').model_name == 'selected-model'
    credentials.set_credentials('openai', auth_required=False, base_url='http://localhost:11434/v1')
    assert credentials.api_key('openai') == 'local-no-auth'
    assert 'openai-test' != get_llm_for_feature('doc_general').openai_api_key.get_secret_value()


def test_queued_cancellation_and_queue_limit(monkeypatch):
    from fastapi import HTTPException
    from config import settings
    from celery_app import celery_app
    from tasks.base import AsyncTask
    from runtime import tasks
    monkeypatch.setattr(tasks, 'LOCAL_TASK_WORKERS', 1)
    started, release, ran = threading.Event(), threading.Event(), threading.Event()

    @celery_app.task(base=AsyncTask, name='tests.runtime.blocker', autoretry_for=())
    def blocker():
        started.set()
        release.wait(5)
        return True

    @celery_app.task(base=AsyncTask, name='tests.runtime.queued', autoretry_for=())
    def queued():
        ran.set()
        return True

    first = blocker.delay()
    try:
        assert started.wait(2)
        second = queued.delay()
        tasks.cancel_local_task(second.id)
        assert second.state == 'REVOKED'
        assert not ran.is_set()
        monkeypatch.setattr(settings, 'local_task_queue_limit', 1)
        with pytest.raises(HTTPException) as exc:
            queued.delay()
        assert exc.value.status_code == 429
    finally:
        release.set()
    assert first.get(timeout=5) is True


def test_celery_autoretry_uses_local_executor():
    from celery_app import celery_app
    from tasks.base import AsyncTask
    from runtime import task_store

    @celery_app.task(bind=True, base=AsyncTask, name='tests.runtime.autoretry',
                     autoretry_for=(ValueError,), retry_backoff=False, default_retry_delay=0.01)
    def automatic(self):
        if self.request.retries == 0:
            raise ValueError('transient')
        return {'success': True}

    task = automatic.delay()
    assert task.get(timeout=5)['success']
    assert task_store.get(task.id)['attempts'] == 2


@pytest.mark.asyncio
async def test_unsafe_schema_upgrade_does_not_modify_data(tmp_path, monkeypatch):
    import sqlite3
    from sqlalchemy import MetaData, Table, Column, Integer, String
    from sqlalchemy.ext.asyncio import create_async_engine
    from config import settings
    from runtime.migrations import upgrade_local_database
    monkeypatch.setattr(settings, 'local_data_dir', str(tmp_path))
    path = tmp_path / 'uninotebook.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE notes(id INTEGER PRIMARY KEY, body VARCHAR)')
        db.execute("INSERT INTO notes VALUES(1,'preserve')")
    metadata = MetaData()
    Table('notes', metadata, Column('id', Integer, primary_key=True), Column('body', String),
          Column('safe', String), Column('required', String, nullable=False))
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    try:
        with pytest.raises(RuntimeError, match='新增必填列'):
            await upgrade_local_database(engine, metadata)
    finally:
        await engine.dispose()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT body FROM notes').fetchone()[0] == 'preserve'
        assert [r[1] for r in db.execute('PRAGMA table_info(notes)')] == ['id', 'body']


@pytest.mark.asyncio
@pytest.mark.parametrize('provider', ['openai', 'moonshot', 'deepseek'])
async def test_structured_generation_through_compatible_http_endpoint(credentials, provider):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from llm_service.llm_config import get_llm_for_feature
    from document_service.schema import DocumentFullSummaryResponse
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            tool = request['tools'][0]['function']['name']
            payload = {'id': 'chatcmpl-test', 'object': 'chat.completion', 'created': 1,
                       'model': request['model'], 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
                       'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
                       'id': 'call_test', 'type': 'function', 'function': {'name': tool,
                       'arguments': json.dumps({'overview': 'A verified summary.', 'key_terms': []})}}]}}],
                       'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2}}
            content = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        credentials.set_credentials(provider, api_key='http-test', model='actual-local-model',
                                    base_url=f'http://127.0.0.1:{server.server_port}/v1')
        for feature in ['doc_parsing', 'flashcards', 'report']:
            llm = get_llm_for_feature(feature)
            result = await llm.with_structured_output(DocumentFullSummaryResponse, method='function_calling').ainvoke('Summarize the document.')
            assert result.overview == 'A verified summary.'
        assert len(requests) == 3
        assert all(r['model'] == 'actual-local-model' for r in requests)
        assert all('thinking' not in r and 'enable_thinking' not in r for r in requests)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_startup_with_optional_imports_blocked(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    script = '''
import importlib.abc, sys
class NoExtras(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'unstructured', 'dashscope', 'pydub', 'qcloud_cos', 'lightrag', 'reportlab', 'fitz', 'camelot', 'pix2tex'}:
            raise ModuleNotFoundError('optional dependency intentionally unavailable: ' + fullname)
sys.meta_path.insert(0, NoExtras())
from fastapi.testclient import TestClient
from main import app
with TestClient(app) as client:
    assert client.get('/api/system/capabilities').status_code == 200
    assert client.get('/api/auth/me').status_code == 200
    assert client.get('/api/system/tasks').status_code == 200
print('CORE_ONLY_STARTUP_OK')
'''
    env = {**os.environ, 'LOCAL_DATA_DIR': str(tmp_path), 'DEPLOY_MODE': 'local'}
    result = subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-4000:]
    assert 'CORE_ONLY_STARTUP_OK' in result.stdout


def test_native_service_never_uses_a_custom_embedding_key(credentials):
    credentials.set_credentials('dashscope', api_key='other-vendor-secret',
                                base_url='http://localhost:11434/v1', model='custom-embedding')
    assert credentials.api_key('dashscope_native') is None
    credentials.set_credentials('dashscope_native', api_key='test-native')
    assert credentials.api_key('dashscope_native') == 'test-native'
    credentials.clear('dashscope_native')
    credentials.set_credentials('dashscope', api_key='test-official',
                                base_url='https://dashscope.aliyuncs.com/compatible-mode/v1')
    assert credentials.api_key('dashscope_native') == 'test-official'


def test_blank_optional_dimensions_in_env():
    from config import Settings
    assert Settings(_env_file=None, embedding_dimensions='').embedding_dimensions is None


def test_pipeline_state_does_not_persist_keys(tmp_path):
    from document_service.engines.paper2slides.core.state import create_state, save_state
    state = create_state({'model': 'custom', 'api_key': 'never-persist', 'nested': {'authorization': 'private'}})
    state['legacy'] = {'openai_api_key': 'old-secret'}
    save_state(tmp_path, state)
    payload = (tmp_path / 'state.json').read_text()
    assert 'never-persist' not in payload
    assert 'old-secret' not in payload
    assert 'private' not in payload
    assert json.loads(payload)['config']['model'] == 'custom'


def test_podcast_checks_system_tools(credentials, monkeypatch):
    from runtime import features
    credentials.set_credentials('openai', api_key='chat-test', model='test')
    credentials.set_credentials('dashscope_native', api_key='native-test')
    monkeypatch.setattr(features, 'has_package', lambda name: True)
    monkeypatch.setattr(features.shutil, 'which', lambda name: None)
    capability = features.describe_capabilities()['podcast']
    assert not capability['available']
    assert capability['missing_packages'] == []
    assert capability['missing_commands'] == ['ffmpeg', 'ffprobe']
