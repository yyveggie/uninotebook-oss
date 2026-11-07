"""Tests run against temporary data and never use the developer's API credentials."""
import atexit
import os
import sys
import tempfile
from pathlib import Path

import pytest

_data = tempfile.TemporaryDirectory(prefix="uninb-tests-")
atexit.register(_data.cleanup)
os.environ.update(LANGCHAIN_TRACING_V2="false", LANGSMITH_TRACING="false", DEPLOY_MODE="local", LOCAL_DATA_DIR=_data.name, REDIS_ENABLED="false", APP_ENV="test")
# TestClient 默认以 Host: testserver 发请求，需显式放行本地访问守卫的 Host 校验
os.environ["LOCAL_ALLOWED_HOSTS"] = "testserver"
# 示例文档会改变「空数据目录」的前提；需要它的测试显式开启
os.environ["SAMPLE_DOCUMENTS_ENABLED"] = "false"
for key in ("OPENAI_API_KEY", "QWEN_API_KEY", "MOONSHOT_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "DASHSCOPE_NATIVE_API_KEY", "IMAGE_GEN_API_KEY"):
    os.environ[key] = ""
os.environ.update(
    OPENAI_BASE_URL="", MOONSHOT_BASE_URL="https://api.moonshot.cn/v1",
    DEEPSEEK_BASE_URL="https://api.deepseek.com/v1",
    DASHSCOPE_BASE_URL="https://dashscope.aliyuncs.com/compatible-mode/v1",
    DEFAULT_CHAT_PROVIDER="",
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def credentials():
    from runtime.credentials import credential_store
    credential_store.clear()
    yield credential_store
    from runtime.tasks import shutdown_local_executor
    shutdown_local_executor(wait=True)
    credential_store.clear()


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as session:
        yield session
