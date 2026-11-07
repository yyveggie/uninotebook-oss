"""
能力探测

统一回答一个问题：**这个功能现在能不能用？**

有两类「用不了」：
1. 用户还没填对应的 API Key（如没填 DashScope Key → 无法向量化 → 无法问答）
2. 用户没装可选依赖（如没装 lightrag-hku → 无法用论文转 PPT）

两者走同一套降级机制：本模块探测 → `/api/system/capabilities` → 前端把入口置灰
并提示原因，而不是让用户点进去撞一个 500。

依赖与 Key 齐备时，该接口一律返回可用。
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import dataclass
from typing import Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


# 任一存在即可满足「有对话模型」
CHAT_PROVIDERS = ("qwen", "moonshot", "deepseek", "openai")


@dataclass(frozen=True)
class Capability:
    """一项功能的可用性要求"""

    key: str
    label: str
    # 需要的 provider（任一满足即可）；空表示不需要 Key
    any_provider: tuple = ()
    # 需要的 provider（必须全部满足）
    all_providers: tuple = ()
    # 需要的 Python 包（必须全部可导入）
    packages: tuple = ()
    # 装包提示，用于前端引导
    install_extra: Optional[str] = None
    commands: tuple = ()


CAPABILITIES: tuple = (
    Capability("chat", "智能问答", any_provider=CHAT_PROVIDERS),
    Capability("mindmap", "思维导图", any_provider=CHAT_PROVIDERS),
    Capability("flashcards", "闪卡", any_provider=CHAT_PROVIDERS),
    Capability("quiz", "测验", any_provider=CHAT_PROVIDERS),
    Capability("highlights", "重点提炼", any_provider=CHAT_PROVIDERS),
    Capability("report", "深度报告", any_provider=CHAT_PROVIDERS),
    Capability("learning_path", "学习路径", any_provider=CHAT_PROVIDERS),
    Capability("interview", "AI 面试", any_provider=CHAT_PROVIDERS),
    Capability("knowledge_graph", "知识图谱", any_provider=CHAT_PROVIDERS),
    # 文档入库必须能向量化
    Capability("document_ingest", "文档解析入库", all_providers=("dashscope",)),
    Capability(
        "advanced_parsing", "高级文档解析（表格/公式）",
        packages=("unstructured", "camelot"),
        install_extra="parsing",
    ),
    Capability(
        "ppt", "论文转 PPT",
        any_provider=CHAT_PROVIDERS,
        all_providers=("image_gen",),
        packages=("lightrag", "unstructured", "dashscope", "reportlab"),
        install_extra="ppt",
    ),
    Capability(
        "poster", "学术海报",
        any_provider=CHAT_PROVIDERS,
        all_providers=("image_gen",),
        packages=("dashscope", "unstructured", "reportlab"),
        install_extra="poster",
    ),
    Capability(
        "podcast", "播客生成",
        any_provider=CHAT_PROVIDERS,
        all_providers=("dashscope_native",),
        packages=("dashscope", "pydub"),
        install_extra="podcast",
        commands=("ffmpeg", "ffprobe"),
    ),
    Capability("deep_research", "联网研究", all_providers=("dashscope_native",),
               packages=("dashscope",), install_extra="research"),
)


_package_cache: Dict[str, bool] = {}
INSTALL_COMMANDS = {
    "parsing": 'pip install "unstructured[all-docs]" "camelot-py[cv]"',
    "ppt": 'pip install lightrag-hku huggingface_hub reportlab dashscope "unstructured[all-docs]"',
    "poster": 'pip install dashscope reportlab "unstructured[all-docs]"',
    "podcast": "pip install dashscope pydub",
    "research": "pip install dashscope",
}


def has_package(name: str) -> bool:
    """探测可选依赖是否已安装（只查找不导入，避免副作用与耗时）"""
    if name not in _package_cache:
        try:
            _package_cache[name] = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError):
            _package_cache[name] = False
    return _package_cache[name]


def describe_capabilities() -> Dict[str, dict]:
    """
    返回全部功能的可用性清单。

    每项含：available（能否使用）、missing_keys（缺哪些 Key）、
    missing_packages（缺哪些包）、install_extra（装包提示）。
    """
    from runtime.credentials import credential_store

    result: Dict[str, dict] = {}

    for cap in CAPABILITIES:
        missing_keys: List[str] = []
        missing_packages: List[str] = []

        if cap.any_provider and not any(credential_store.has_key(p) for p in cap.any_provider):
            # 任一即可——都没有时把候选项都列出来供前端提示
            missing_keys.extend(cap.any_provider)

        for provider in cap.all_providers:
            if not credential_store.has_key(provider):
                missing_keys.append(provider)

        for package in cap.packages:
            if not has_package(package):
                missing_packages.append(package)
        missing_commands = [command for command in cap.commands if not shutil.which(command)]

        result[cap.key] = {
            "label": cap.label,
            "available": not missing_keys and not missing_packages and not missing_commands,
            "missing_keys": missing_keys,
            "missing_packages": missing_packages,
            "missing_commands": missing_commands,
            "install_extra": cap.install_extra,
            "install_command": INSTALL_COMMANDS.get(cap.install_extra),
        }

    return result


def is_available(capability_key: str) -> bool:
    """单项能力是否可用（供后端在入口处快速判断）"""
    caps = describe_capabilities()
    entry = caps.get(capability_key)
    return bool(entry and entry["available"])


def require_capability(capability_key: str) -> None:
    """Guard optional features before accepting work, including direct API calls."""
    from fastapi import HTTPException
    from config import settings

    if not settings.is_local_mode:
        return
    entry = describe_capabilities()[capability_key]
    if not entry["available"]:
        raise HTTPException(status_code=503, detail={
            "code": "CREDENTIALS_NOT_CONFIGURED" if entry["missing_keys"] else "CAPABILITY_UNAVAILABLE",
            "message": f"{entry['label']}暂不可用，请在设置页补充配置或安装可选依赖",
            "details": entry,
        })


def require_document_parser(filename: str) -> None:
    from pathlib import Path
    from fastapi import HTTPException
    from document_service.parsers.parser_simple import SimpleParser

    if Path(filename).suffix.lower() not in SimpleParser.supported_extensions and not has_package("unstructured"):
        raise HTTPException(status_code=503, detail={
            "code": "CAPABILITY_UNAVAILABLE",
            "message": "该格式需要高级解析依赖，请安装 uninotebook[parsing] 后重试",
        })


def public_model_error(exc: BaseException) -> str:
    """模型调用失败时给用户看的文字：Key 问题给出指引；线上版不暴露服务商的原始报错"""
    from config import settings

    if looks_like_missing_credentials(exc):
        return missing_credential_hint()
    return str(exc) if settings.is_local_mode else "回答生成失败，请稍后重试。"


# ==================== 缺凭据的统一识别 ====================

# 各家 SDK 在缺 Key 时抛出的错误文本特征。
# 这些消息对用户毫无意义（"The api_key client option must be set…"），
# 需要在响应层翻译成「去设置页填 Key」这样的可执行指引。
_MISSING_KEY_SIGNATURES = (
    "api_key client option must be set",
    "api key must be set",
    "no api key provided",
    "incorrect api key",
    "invalid api key",
    "invalid api-key",
    "invalid_api_key",
    "error code: 401",
    "authentication_error",
    "未配置",
)


def looks_like_missing_credentials(exc: BaseException) -> bool:
    """判断某个异常是否源于「未配置 API Key」"""
    text = str(exc).lower()
    return any(sig.lower() in text for sig in _MISSING_KEY_SIGNATURES)


def missing_credential_hint() -> str:
    """
    根据当前实际缺失的凭据，给出一句可执行的提示。

    比起原样抛出 SDK 报错，用户需要知道的是「缺哪个、去哪填」。
    """
    from config import settings
    from runtime.credentials import credential_store

    missing = []
    if not any(credential_store.has_key(p) for p in CHAT_PROVIDERS):
        missing.append("对话模型（通义千问 / Kimi / DeepSeek / OpenAI 兼容端点，任选其一）")
    if not credential_store.has_key("dashscope"):
        missing.append("向量化（文档功能必需）")

    if not missing:
        # Key 配了但仍报鉴权错——多半是填错或额度耗尽
        if settings.is_local_mode:
            return "模型服务拒绝了这个 API Key，可能是 Key 无效或额度不足。请到「设置」页检查后重试。"
        return "服务暂时不可用，请稍后重试。"

    items = "；".join(missing)
    if settings.is_local_mode:
        return f"尚未配置：{items}。请在「设置」页填写 API Key 后重试。"
    return f"服务端未配置：{items}。请联系管理员。"
