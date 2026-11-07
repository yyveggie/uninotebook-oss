"""文档域安全 / 一致性回归测试（全部离线：DNS 与 HTTP 均为 mock）"""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest


PUBLIC_IP = "93.184.216.34"


def _resolver(mapping):
    return lambda host, port: mapping[host]


def test_safe_fetch_blocks_private_targets_and_redirect_hops():
    from utils.safe_fetch import UnsafeURLError, fetch_html, validate_url

    for url in ["http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest", "file:///etc/passwd", "gopher://x"]:
        with pytest.raises(UnsafeURLError):
            validate_url(url, allow_private=False)
    with pytest.raises(UnsafeURLError):  # 任一解析结果为内网即拒绝
        validate_url("http://mixed.example/", allow_private=False,
                     resolver=_resolver({"mixed.example": [PUBLIC_IP, "10.0.0.5"]}))

    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["host"]))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        return httpx.Response(200, text="<html>secret</html>")

    resolver = _resolver({"public.example": [PUBLIC_IP], "internal.example": ["10.1.2.3"]})
    with pytest.raises(UnsafeURLError):
        fetch_html("http://public.example/start", allow_private=False,
                   transport=httpx.MockTransport(handler), resolver=resolver)
    # 连接的是校验过的 IP，Host 头保持原域名；内网跳转从未被请求
    assert seen == [(f"http://{PUBLIC_IP}/start", "public.example")]


def test_safe_fetch_follows_safe_redirects_and_caps_size():
    from utils.safe_fetch import fetch_html

    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 2048)
        return httpx.Response(200, html="<html><body><p>你好</p></body></html>")

    resolver = _resolver({"ok.example": [PUBLIC_IP]})
    transport = httpx.MockTransport(handler)
    assert "你好" in fetch_html("http://ok.example/a", allow_private=False, transport=transport, resolver=resolver)
    assert fetch_html("http://ok.example/big", allow_private=False, max_bytes=1024,
                      transport=transport, resolver=resolver) is None
    # 本地版放行私有地址
    assert fetch_html("http://127.0.0.1/b", allow_private=True, transport=transport) is not None


def test_content_disposition_non_ascii_is_header_safe():
    from routes.document import build_content_disposition

    value = build_content_disposition('论文 "终稿".pdf', "inline")
    value.encode("latin-1")  # 不得抛 UnicodeEncodeError
    assert value.startswith('inline; filename="')
    assert "filename*=UTF-8''%E8%AE%BA%E6%96%87" in value
    assert 'filename="download.pdf"' in build_content_disposition("论文.pdf")


def test_podcast_audio_token_verification():
    from fastapi import HTTPException
    from jose import jwt
    from config import settings
    from document_service.generators.podcast import api as podcast_api

    token = podcast_api.create_podcast_audio_token(user_id=7, task_id=42)
    assert podcast_api.verify_podcast_audio_token(token, 42) == 7
    for bad, task_id, status in [(token, 43, 403), (token + "x", 42, 401), (None, 42, 401)]:
        with pytest.raises(HTTPException) as exc:
            podcast_api.verify_podcast_audio_token(bad, task_id)
        assert exc.value.status_code == status
    access_like = jwt.encode({"type": "access", "sub": "7", "task_id": 42}, settings.jwt_secret_key,
                             algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException):
        podcast_api.verify_podcast_audio_token(access_like, 42)


def test_list_documents_requires_and_applies_user_filter():
    from document_service.core import DocumentProcessor

    captured = []

    class FakeDB:
        async def execute(self, stmt):
            captured.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    processor = DocumentProcessor(lc_client=None, db=FakeDB(), parser=object())
    with pytest.raises(ValueError):
        asyncio.run(processor.list_documents(user_id=None))
    asyncio.run(processor.list_documents(user_id=5))
    assert "documents.user_id = 5" in captured[0]


def test_stream_token_frames_are_valid_json():
    from document_service.stream_handler import StreamHandler

    token = 'a\tb"c\\d\ne\x01'
    frame = StreamHandler.sse("token", token)
    assert frame.startswith("data: ") and frame.endswith("\n\n")
    assert json.loads(frame[6:]) == {"event": "token", "data": token}


def test_ppt_status_and_worker_share_config_dir():
    from document_service.engines.paper2slides.core import get_config_dir
    from document_service.generators.ppt.service import build_ppt_pipeline_config
    from pathlib import Path

    ppt = SimpleNamespace(style="academic|zh|paper|slides")
    worker = build_ppt_pipeline_config(ppt, input_path="/tmp/x.txt")
    status = build_ppt_pipeline_config(ppt)
    assert get_config_dir(Path("/base"), worker) == get_config_dir(Path("/base"), status)
    assert get_config_dir(Path("/base"), status).name == "slides_academic_medium"
