"""
系统配置路由

包含: 可用模型列表、运行时能力清单
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from urllib.parse import urlsplit

import auth
import db_models
from config import settings
from runtime.credentials import PROVIDER_SETTINGS_MAP

router = APIRouter()


@router.get("/api/system/capabilities", tags=["系统配置"])
async def get_capabilities():
    """
    运行时能力清单。

    前端据此把「缺 Key」或「缺可选依赖」的功能入口置灰并给出原因，
    而不是让用户点进去撞 500。

    不需要登录：本地版首次启动时用户尚未配置任何东西，
    前端需要先拿到这份清单才知道该引导用户做什么。
    """
    from runtime.features import describe_capabilities

    return {
        "success": True,
        "data": {
            "deploy_mode": "local" if settings.is_local_mode else "saas",
            "capabilities": describe_capabilities(),
        },
    }


# ==================== 本地版凭据管理 ====================
# 仅在 local 模式下开放。其他模式一律 404——
# 多用户部署下绝不能让任何人通过 HTTP 接口读写服务端 API Key。


class CredentialUpdate(BaseModel):
    """更新某 provider 的凭据。空字符串表示清除该项，回落环境变量。"""

    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = Field(default=None, max_length=255)
    dimensions: Optional[int] = Field(default=None, ge=0, le=65536)
    auth_required: Optional[bool] = None
    use_for_chat: Optional[bool] = None

    @field_validator("base_url")
    @classmethod
    def validate_endpoint(cls, value):
        if value:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                raise ValueError("Base URL 必须是 HTTP(S) 地址，不能包含账号或密码")
        return value


def _require_local_mode() -> None:
    if not settings.is_local_mode:
        raise HTTPException(status_code=404, detail="Not Found")


def _settings_error(status_code: int, code: str, message: str) -> HTTPException:
    """设置页的错误带稳定 code：前端按 code 显示中文或英文，message 作兜底"""
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


# 设置页「运行环境」展示的只读配置：(字段名, 对应环境变量)。
# 这些项通过 backend/.env 或环境变量修改，重启后生效，不提供在线修改。
RUNTIME_SETTINGS = (
    ("max_upload_mb", "MAX_UPLOAD_MB"),
    ("local_task_workers", "LOCAL_TASK_WORKERS"),
    ("local_document_task_workers", "LOCAL_DOCUMENT_TASK_WORKERS"),
    ("local_task_queue_limit", "LOCAL_TASK_QUEUE_LIMIT"),
    ("local_task_timeout_seconds", "LOCAL_TASK_TIMEOUT_SECONDS"),
    ("local_embedding_concurrency", "LOCAL_EMBEDDING_CONCURRENCY"),
    ("document_parser_strategy", "DOCUMENT_PARSER_STRATEGY"),
    ("local_allowed_hosts", "LOCAL_ALLOWED_HOSTS"),
)


@router.get("/api/system/info", tags=["系统配置"])
async def get_runtime_info():
    """
    本地版运行环境（只读）。

    设置页把 Key 之外的配置也集中展示：数据存在哪、读的是哪个配置文件、
    当前生效的上限与并发。只返回路径和数值，不含任何密钥。
    """
    _require_local_mode()
    import platform
    from config import _ACTIVE_ENV_FILE
    from runtime.credentials import _CREDENTIALS_FILENAME

    data_dir = settings.data_dir
    return {
        "success": True,
        "data": {
            "data_dir": str(data_dir),
            "credentials_file": str(data_dir / _CREDENTIALS_FILENAME),
            "env_file": {"path": str(_ACTIVE_ENV_FILE), "exists": _ACTIVE_ENV_FILE.exists()},
            "listen": {"host": settings.api_host, "port": settings.api_port},
            "python": platform.python_version(),
            "settings": [
                {"key": key, "env": env, "value": getattr(settings, key)}
                for key, env in RUNTIME_SETTINGS
            ],
        },
    }


@router.get("/api/system/credentials", tags=["系统配置"])
async def get_credentials():
    """
    获取各 provider 的凭据配置状态（**打码**，绝不返回明文 Key）。
    """
    _require_local_mode()
    from runtime.credentials import credential_store

    return {"success": True, "data": credential_store.describe()}


@router.put("/api/system/credentials", tags=["系统配置"])
async def update_credentials(payload: CredentialUpdate):
    """
    写入某 provider 的 API Key / Base URL，立即生效并持久化到本地。
    """
    _require_local_mode()
    from runtime.credentials import credential_store

    # 把端点改到另一个主机却不重填 Key，等于把已保存的 Key 发给新主机。
    # 正常用户换端点时本就需要对应的新 Key；要求重填可防止 Key 被静默转发出去。
    if payload.base_url and payload.api_key is None and payload.provider in PROVIDER_SETTINGS_MAP:
        # 未配置端点时 SDK 走官方默认地址，按该地址比较，避免误判为「换了主机」
        current_url = credential_store.base_url(payload.provider) or (
            "https://api.openai.com/v1" if payload.provider == "openai" else ""
        )
        keeps_auth = (
            payload.auth_required is not False
            and credential_store.resolve(payload.provider, "auth_required") is not False
        )
        if (
            keeps_auth
            and credential_store.resolve(payload.provider, "api_key")
            and (urlsplit(payload.base_url).hostname or "") != (urlsplit(current_url).hostname or "")
        ):
            raise _settings_error(400, "BASE_URL_HOST_CHANGED", "修改 Base URL 的主机时，请同时重新填写 API Key")

    try:
        credential_store.set_credentials(
            payload.provider,
            api_key=payload.api_key,
            base_url=payload.base_url,
            model=payload.model,
            dimensions=payload.dimensions,
            auth_required=payload.auth_required,
            use_for_chat=payload.use_for_chat,
        )
    except KeyError:
        raise _settings_error(400, "UNKNOWN_PROVIDER", f"未知 provider: {payload.provider}")
    except ValueError as exc:
        raise _settings_error(400, "INVALID_CREDENTIALS", str(exc))
    except OSError:
        raise _settings_error(500, "CREDENTIALS_SAVE_FAILED", "凭据保存失败，请检查数据目录权限")

    from runtime.features import describe_capabilities

    return {
        "success": True,
        "data": {
            "credentials": credential_store.describe(),
            # 改完 Key 后能力清单会变，一并返回省去前端二次请求
            "capabilities": describe_capabilities(),
        },
    }


class ConnectionTest(BaseModel):
    provider: str


@router.post("/api/system/embeddings/reindex", tags=["系统配置"])
async def reindex_local_documents(user: db_models.User = Depends(auth.get_current_user)):
    _require_local_mode()
    from sqlalchemy import select
    from database import AsyncSessionLocal
    from runtime.features import require_capability
    from tasks.document_tasks import reindex_document

    require_capability("document_ingest")
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(db_models.Document.id).where(
            db_models.Document.user_id == user.id,
            db_models.Document.original_text != "",
        ))).scalars().all()
    tasks = [reindex_document.delay(document_id=id_, user_id=user.id).id for id_ in ids]
    return {"success": True, "data": {"task_ids": tasks}}


def _owned_local_task(task_id, user):
    _require_local_mode()
    from runtime import task_store

    record = task_store.get(task_id)
    if not record or record["user_id"] != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return record


@router.get("/api/system/tasks", tags=["本地任务"])
def list_local_tasks(user: db_models.User = Depends(auth.get_current_user)):
    _require_local_mode()
    from runtime import task_store

    return {"success": True, "data": [task_store.public_task(r) for r in task_store.list_tasks(user.id)]}


@router.get("/api/system/tasks/{task_id}", tags=["本地任务"])
def local_task_detail(task_id: str, user: db_models.User = Depends(auth.get_current_user)):
    from runtime.task_store import public_task

    return {"success": True, "data": public_task(_owned_local_task(task_id, user))}


@router.post("/api/system/tasks/{task_id}/cancel", tags=["本地任务"])
def cancel_local_task(task_id: str, user: db_models.User = Depends(auth.get_current_user)):
    _owned_local_task(task_id, user)
    from runtime.tasks import cancel_local_task as cancel

    cancel(task_id)
    return local_task_detail(task_id, user)


@router.post("/api/system/tasks/{task_id}/retry", tags=["本地任务"])
def retry_local_task(task_id: str, user: db_models.User = Depends(auth.get_current_user)):
    _owned_local_task(task_id, user)
    from runtime.tasks import retry_local_task as retry

    try:
        retry(task_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return local_task_detail(task_id, user)


@router.post("/api/system/credentials/test", tags=["系统配置"])
async def test_model_connection(payload: ConnectionTest):
    """An explicit user action: make one small request using the saved profile."""
    _require_local_mode()
    from openai import AsyncOpenAI
    from runtime.credentials import credential_store

    provider = payload.provider
    if provider not in {"openai", "qwen", "moonshot", "deepseek", "dashscope"}:
        raise _settings_error(400, "TEST_NOT_SUPPORTED", "该服务不支持此连接测试")
    if not credential_store.has_key(provider):
        raise _settings_error(400, "CREDENTIALS_NOT_SAVED", "请先保存模型配置")
    try:
        async with AsyncOpenAI(api_key=credential_store.api_key(provider),
                               base_url=credential_store.base_url(provider),
                               timeout=15, max_retries=0) as client:
            if provider == "dashscope":
                dimensions = credential_store.resolve(provider, "dimensions")
                response = await client.embeddings.create(
                    model=credential_store.model(provider), input="connection test",
                    **({"dimensions": dimensions} if dimensions else {}),
                )
                return {"success": True, "data": {"dimensions": len(response.data[0].embedding)}}
            await client.chat.completions.create(
                model=credential_store.model(provider),
                messages=[{"role": "user", "content": "Reply OK."}], max_tokens=8,
            )
            return {"success": True, "data": {"message": "连接成功"}}
    except Exception:
        # SDK error strings can echo secrets or provider response bodies.
        raise _settings_error(502, "CONNECTION_FAILED", "连接失败，请检查端点、模型名、认证方式和服务状态")


@router.get("/api/models", tags=["系统配置"])
async def get_available_models(
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取可用模型列表（已移除 VIP 限制）"""
    if settings.is_local_mode:
        from llm_service.llm_config import get_available_qa_models
        from runtime.credentials import credential_store

        models = get_available_qa_models()
        preferred = credential_store.default_chat_provider
        default = preferred if preferred in {"openai", "qwen", "moonshot", "deepseek"} else None
        if default not in {model["key"] for model in models}:
            default = None
        return {"success": True, "data": {
            "default": default or (models[0]["key"] if models else "qwen"),
            "options": [{"value": m["key"], "label": f"{m['label']} ({m['model']})",
                         "model": m["model"], "disabled": False, "is_reasoning": False}
                        for m in models],
        }}
    return {
        "success": True,
        "data": {
            # Kimi 相关功能已迁到 OpenAI 兼容端点（见 llm_config.FEATURE_MODEL_MAP），默认与之一致；
            # 列表中也不再提供 Kimi 选项，需要时在此恢复
            "default": "qwen",
            "options": [
                {
                    "label": f"通义千问 ({settings.openai_model})",
                    "value": "qwen",
                    "provider": "qwen",
                    "model": settings.openai_model,
                    "disabled": False,
                    "is_reasoning": False
                },
                {
                    "label": f"通义千问 ({settings.openai_max_preview_model}）长推理",
                    "value": "qwen3-max-preview",
                    "provider": "qwen",
                    "model": settings.openai_max_preview_model,
                    "disabled": False,  # Reasoning Mode Enabled
                    "is_reasoning": True
                },
                {
                    "label": "深度求索（DeepSeek-V3.2）",
                    "value": "deepseek",
                    "provider": "deepseek",
                    "model": settings.deepseek_model,
                    "disabled": False,
                    "is_reasoning": False
                },
                {
                    "label": "深度求索（DeepSeek-R1）长推理",
                    "value": "deepseek-reasoner",
                    "provider": "deepseek",
                    "model": settings.deepseek_reasoner_model,
                    "disabled": False,  # Reasoning Mode Enabled
                    "is_reasoning": True
                },
                {
                    "label": "深度求索（DeepSeek-promax）更长推理",
                    "value": "deepseek-speciale",
                    "provider": "deepseek",
                    "model": settings.deepseek_speciale_model,
                    "disabled": False,  # Reasoning Mode Enabled
                    "is_reasoning": True
                }
            ]
        }
    }
