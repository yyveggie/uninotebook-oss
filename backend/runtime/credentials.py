"""
运行时凭据存储

解决的问题：`llm_service/llm_config.py` 原本在**模块导入时**就从 `settings` 求值出
`MODEL_CONFIGS`，导致 API Key 只能来自环境变量、无法在运行时修改。本地部署需要
用户在设置页里自己填 Key，因此凭据必须变成可运行时覆盖、可持久化的。

两种模式：

- **local**：凭据落盘到 `~/.uninotebook/credentials.json`（权限 0600），
  用户通过设置页写入，重启后仍然有效。
- **其他模式**：本存储始终为空，`resolve()` 直接回落 `settings`（环境变量），
  不做任何磁盘 IO。

安全约定：Key 值**永不写入日志**，对外一律经 `mask_secret()` 打码。
"""
from __future__ import annotations

import json
import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from config import settings
from utils.logger import get_logger

logger = get_logger(__name__)


# ==================== Provider 定义 ====================
# provider 名 → 该 provider 各字段在 Settings 上的属性名
# 这是运行时覆盖与环境变量之间唯一的映射表。

PROVIDER_SETTINGS_MAP: Dict[str, Dict[str, str]] = {
    "openai": {
        "api_key": "openai_api_key",
        "base_url": "openai_base_url",
        "model": "openai_model",
    },
    "qwen": {
        "api_key": "qwen_api_key",
        "base_url": "qwen_base_url",
        "model": "qwen_model",
    },
    "moonshot": {
        "api_key": "moonshot_api_key",
        "base_url": "moonshot_base_url",
        "model": "moonshot_model",
    },
    "deepseek": {
        "api_key": "deepseek_api_key",
        "base_url": "deepseek_base_url",
        "model": "deepseek_model",
    },
    "dashscope": {
        "api_key": "dashscope_api_key",
        "base_url": "dashscope_base_url",
        "model": "dashscope_embedding_model",
        "dimensions": "embedding_dimensions",
    },
    "image_gen": {
        "api_key": "image_gen_api_key",
        "base_url": "image_gen_base_url",
        "model": "image_gen_model",
    },
    "dashscope_native": {
        "api_key": "dashscope_native_api_key",
    },
}

# 可用于对话与内容生成的 provider；未指定默认时按此顺序回落
CHAT_PROVIDERS = ("openai", "qwen", "moonshot", "deepseek")

# 允许运行时覆盖的字段
OVERRIDABLE_FIELDS = ("api_key", "base_url", "model", "dimensions", "auth_required")

_CREDENTIALS_FILENAME = "credentials.json"


def mask_secret(value: Optional[str]) -> str:
    """将密钥打码为可安全展示/记录的形式"""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-4:]}"


class CredentialStore:
    """线程安全的运行时凭据存储"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._overrides: Dict[str, Dict[str, Any]] = {}
        self._chat_provider: Optional[str] = None
        self._loaded = False

    # ---------- 持久化 ----------

    @property
    def _path(self) -> Path:
        return settings.data_dir / _CREDENTIALS_FILENAME

    def _ensure_loaded(self) -> None:
        """本地模式下惰性从磁盘加载一次"""
        if self._loaded or not settings.is_local_mode:
            return
        with self._lock:
            if self._loaded:
                return
            self._loaded = True
            path = self._path
            if not path.exists():
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.warning("⚠️ 凭据文件读取失败，将忽略：%s", exc)
                return

            providers = raw.get("providers") if isinstance(raw, dict) else None
            if not isinstance(providers, dict):
                return

            self._chat_provider = raw.get("default_chat_provider")

            for provider, fields in providers.items():
                if provider not in PROVIDER_SETTINGS_MAP or not isinstance(fields, dict):
                    continue
                clean = {
                    field: fields[field]
                    for field in OVERRIDABLE_FIELDS
                    if field in fields and fields[field] is not None and fields[field] != ""
                }
                if clean:
                    self._overrides[provider] = clean

            logger.info("🔑 已加载本地凭据，覆盖 %d 个 provider", len(self._overrides))

    def _persist(self) -> None:
        """把当前覆盖写回磁盘（仅本地模式）。调用方必须已持有锁。"""
        if not settings.is_local_mode:
            return
        path = self._path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"providers": self._overrides,
                                  "default_chat_provider": self._chat_provider},
                                 ensure_ascii=False, indent=2)
            # 先以 0600 创建再写入，避免密钥短暂以默认权限落盘
            tmp = path.with_suffix(".tmp")
            tmp.touch(mode=0o600, exist_ok=True)
            tmp.chmod(0o600)
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
            path.chmod(0o600)
        except OSError as exc:
            logger.error("❌ 凭据写入失败：%s", exc)
            raise

    # ---------- 读取 ----------

    def resolve(self, provider: str, field: str) -> Any:
        """
        取得某 provider 某字段的生效值：运行时覆盖优先，否则回落环境变量。

        非本地模式下永远走回落分支，即只读环境变量。
        """
        mapping = PROVIDER_SETTINGS_MAP.get(provider)
        if mapping is None:
            raise KeyError(f"未知 provider: {provider}")

        if settings.is_local_mode:
            self._ensure_loaded()
            with self._lock:
                fields = self._overrides.get(provider, {})
                if field in fields:
                    return fields[field]

        settings_attr = mapping.get(field)
        if not settings_attr:
            return True if field == "auth_required" else None
        value = getattr(settings, settings_attr, None)
        if not value and provider == "dashscope_native" and field == "api_key":
            # Never send an arbitrary OpenAI-compatible embedding key to a
            # vendor's native speech/research service.
            from urllib.parse import urlsplit

            endpoint = self.base_url("dashscope") or ""
            if settings.is_saas_mode or (
                urlsplit(endpoint).hostname in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"}
                and self.resolve("dashscope", "auth_required") is not False
            ):
                return self.resolve("dashscope", "api_key") or None
            return None
        return value

    def api_key(self, provider: str) -> Optional[str]:
        if self.resolve(provider, "auth_required") is False:
            # OpenAI-compatible clients require a nonempty value even when a local
            # server does not authenticate. Never fall back to a cloud endpoint.
            return "local-no-auth" if self.base_url(provider) else None
        return self.resolve(provider, "api_key")

    def base_url(self, provider: str) -> Optional[str]:
        return self.resolve(provider, "base_url")

    def has_key(self, provider: str) -> bool:
        return bool(self.api_key(provider))

    def model(self, provider: str) -> Optional[str]:
        return self.resolve(provider, "model")

    @property
    def default_chat_provider(self) -> Optional[str]:
        self._ensure_loaded()
        return self._chat_provider or settings.default_chat_provider

    def chat_provider(self, suggested: str = "openai", prefer_default: bool = True) -> str:
        first = (self.default_chat_provider, suggested) if prefer_default else (suggested, self.default_chat_provider)
        candidates = (*first, *CHAT_PROVIDERS)
        for provider in candidates:
            if provider in CHAT_PROVIDERS and self.has_key(provider):
                return provider
        raise ValueError("API key must be set：请在设置页配置至少一个对话模型，或启用本地端点的免认证选项")

    # ---------- 写入 ----------

    def set_credentials(
        self,
        provider: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
        auth_required: Optional[bool] = None,
        use_for_chat: Optional[bool] = None,
    ) -> None:
        """
        设置某 provider 的运行时凭据。

        传入空字符串表示**清除**该字段的覆盖（回落到环境变量）；
        传入 None 表示保持该字段不变。
        """
        if provider not in PROVIDER_SETTINGS_MAP:
            raise KeyError(f"未知 provider: {provider}")

        updates = {"api_key": api_key, "base_url": base_url, "model": model,
                   "dimensions": dimensions, "auth_required": auth_required}
        if use_for_chat and provider not in CHAT_PROVIDERS:
            raise ValueError("只有对话模型可以设置为默认")

        with self._lock:
            self._ensure_loaded()
            current = dict(self._overrides.get(provider, {}))
            for field, value in updates.items():
                if value is None:
                    continue
                if isinstance(value, str):
                    value = value.strip()
                if value != "" and not (field == "dimensions" and value == 0):
                    current[field] = value
                else:
                    current.pop(field, None)

            if current:
                self._overrides[provider] = current
            else:
                self._overrides.pop(provider, None)

            if use_for_chat:
                self._chat_provider = provider
            elif use_for_chat is False and self._chat_provider == provider:
                self._chat_provider = None

            self._persist()

        logger.info("🔑 已更新 provider 凭据：%s", provider)
        invalidate_llm_clients()

    def clear(self, provider: Optional[str] = None) -> None:
        """清除指定 provider（或全部）的运行时覆盖"""
        with self._lock:
            self._ensure_loaded()
            if provider is None:
                self._overrides.clear()
                self._chat_provider = None
            else:
                self._overrides.pop(provider, None)
            self._persist()
        invalidate_llm_clients()

    # ---------- 自省 ----------

    def describe(self) -> Dict[str, Dict[str, Any]]:
        """
        供设置页展示的打码快照。绝不返回明文 Key。
        """
        result: Dict[str, Dict[str, Any]] = {}
        for provider in PROVIDER_SETTINGS_MAP:
            key = self.api_key(provider)
            with self._lock:
                overridden = provider in self._overrides
            result[provider] = {
                "configured": bool(key),
                "masked_api_key": mask_secret(key),
                "base_url": self.base_url(provider) or "",
                "model": self.model(provider) or "",
                "dimensions": self.resolve(provider, "dimensions"),
                "auth_required": self.resolve(provider, "auth_required") is not False,
                "default_for_chat": self.default_chat_provider == provider and bool(key),
                # True 表示该值来自用户运行时填写，而非环境变量
                "user_provided": overridden,
            }
        return result

    def fingerprint(self) -> str:
        """
        当前全部生效凭据的短指纹。

        用于 LLM 客户端缓存键——用户改 Key 后指纹变化，
        旧的缓存客户端自然失效（原实现漏掉了这一点）。
        """
        parts = []
        for provider in sorted(PROVIDER_SETTINGS_MAP):
            parts.append(json.dumps([provider, self.api_key(provider), self.base_url(provider),
                                    self.model(provider), self.resolve(provider, "dimensions")]))
        parts.append(str(self.default_chat_provider))
        digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        return digest[:16]


# 全局单例
credential_store = CredentialStore()


def invalidate_llm_clients() -> None:
    """
    凭据变化后清空 LLM 客户端缓存。

    放在此处而非 llm_service，是为了避免 credentials ↔ llm_config 的循环导入；
    llm_service 尚未加载时这里静默跳过。
    """
    try:
        from llm_service import llm_config
    except ImportError:
        return
    try:
        llm_config.clear_llm_cache()
    except AttributeError:
        pass
