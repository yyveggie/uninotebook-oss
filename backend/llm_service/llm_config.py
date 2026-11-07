"""
LLM 统一配置管理

通过修改 FEATURE_MODEL_MAP 来控制每个功能使用哪个 LLM 模型。
通过修改 FEATURE_EMBEDDING_MAP 来控制每个功能使用哪个嵌入模型。

支持的 LLM 模型键: moonshot, kimi-thinking, kimi-thinking-turbo, deepseek, deepseek-reasoner, qwen, qwen-preview
支持的嵌入模型键: dashscope

🆕 V2: 添加 LLM 客户端缓存，避免重复创建连接
🆕 V3: 思考模型使用 ThinkingChatOpenAI 以保留 reasoning_content
🆕 V4: 凭据改为**惰性求值** —— MODEL_CONFIGS / EMBEDDING_CONFIGS 不再在模块导入时
       冻结 API Key，而是每次访问时经 runtime.credentials 解析。这样开源本地版
       才能让用户在设置页里自填 Key 并立即生效。未启用运行时覆盖时，
       解析结果恒等于环境变量。
"""
from collections import OrderedDict
from collections.abc import Mapping
import asyncio
import json
import threading
from typing import Optional, Dict, Any, Iterator

from langchain_openai import ChatOpenAI

from config import settings
from runtime.credentials import credential_store
from .thinking_llm import ThinkingChatOpenAI
from utils.logger import get_logger

logger = get_logger(__name__)

# 思考模型列表 — 使用 ThinkingChatOpenAI 以保留 reasoning_content
THINKING_MODEL_KEYS = {"kimi-thinking", "kimi-thinking-turbo", "deepseek-reasoner", "deepseek-v4-flash", "qwen-preview"}
# Kimi K2.5 / K2.6 的固定温度（见 _normalize_temperature_for_model）
KIMI_INSTANT_TEMPERATURE = 0.6   # 关闭思考
KIMI_THINKING_TEMPERATURE = 1.0  # 开启思考（模型默认）
_KIMI_FIXED_TEMPERATURE_MODELS = ("kimi-k2.6", "kimi-k2.5")

# LLM 客户端缓存（使用 OrderedDict 实现 LRU 淘汰）
_LLM_CACHE_MAX_SIZE = 32
_llm_cache: OrderedDict = OrderedDict()
_llm_cache_lock = threading.RLock()


def _cache_scope():
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    return threading.get_ident(), loop_id


def clear_llm_cache() -> None:
    """清空 LLM 客户端缓存（凭据变更后由 runtime.credentials 调用）"""
    with _llm_cache_lock:
        _llm_cache.clear()
    logger.info("♻️ LLM 客户端缓存已清空")


# ==================== 惰性配置容器 ====================


class _LazyConfigMapping(Mapping):
    """
    只读映射，每次访问时重新求值。

    存在的意义：`MODEL_CONFIGS["qwen"]["api_key"]` 这类既有写法必须继续可用，
    但取到的 Key 必须是**当前**生效值而非导入时的快照。
    """

    __slots__ = ("_builder",)

    def __init__(self, builder):
        self._builder = builder

    def __getitem__(self, key: str) -> Dict[str, Any]:
        return self._builder()[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._builder())

    def __len__(self) -> int:
        return len(self._builder())

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return f"<LazyConfigMapping keys={list(self._builder())}>"


# ==================== 模型配置定义 ====================
# 每个模型的详细配置参数
# 注：api_key / base_url 经 credential_store 解析，支持运行时覆盖。


def _build_model_configs() -> Dict[str, Dict[str, Any]]:
    moonshot_key = credential_store.api_key("moonshot")
    moonshot_url = credential_store.base_url("moonshot")
    deepseek_key = credential_store.api_key("deepseek")
    deepseek_url = credential_store.base_url("deepseek")
    openai_key = credential_store.api_key("openai")
    openai_url = credential_store.base_url("openai")

    return {
        "moonshot": {
            "model": settings.moonshot_model,                # kimi-k2.6
            "api_key": moonshot_key,
            "base_url": moonshot_url,
            "temperature": KIMI_INSTANT_TEMPERATURE,  # 关闭思考时只允许 0.6
            "model_kwargs": {"extra_body": {"thinking": {"type": "disabled"}}},
        },
        "kimi-thinking": {
            "model": settings.moonshot_thinking_model,       # kimi-k2.6
            "api_key": moonshot_key,
            "base_url": moonshot_url,
            "temperature": KIMI_THINKING_TEMPERATURE,  # 思考模式只允许 1.0
            "model_kwargs": {"extra_body": {"thinking": {"type": "enabled"}}},
        },
        "kimi-thinking-turbo": {
            "model": settings.moonshot_thinking_turbo_model, # kimi-k2.6
            "api_key": moonshot_key,
            "base_url": moonshot_url,
            "temperature": KIMI_THINKING_TEMPERATURE,  # 思考模式只允许 1.0
            "model_kwargs": {"extra_body": {"thinking": {"type": "enabled"}}},
        },
        "deepseek": {
            "model": settings.deepseek_model,                # deepseek-chat
            "api_key": deepseek_key,
            "base_url": deepseek_url,
            "temperature": 0.5,
        },
        "deepseek-v4-flash": {
            "model": "deepseek-v4-flash",
            "api_key": deepseek_key,
            "base_url": deepseek_url,
            "temperature": 0.5,
            # reasoning_effort 也放进 extra_body：作为顶层参数时，openai SDK < 1.58 的 create()
            # 直接 TypeError，ThinkingChatOpenAI 的流式请求也不会带上它
            "model_kwargs": {
                "extra_body": {"thinking": {"type": "enabled"}, "reasoning_effort": "high"},
            },
        },
        "deepseek-reasoner": {
            "model": settings.deepseek_reasoner_model,       # deepseek-reasoner
            "api_key": deepseek_key,
            "base_url": deepseek_url,
            "temperature": 0.6,
        },
        # 本地版「OpenAI 兼容端点」在问答模型列表里的键（托管版不使用）
        "openai": {
            "model": settings.openai_model,
            "api_key": openai_key,
            "base_url": openai_url,
            "temperature": 0.5,
        },
        # 通用 Qwen：显式关闭思考。qwen3.x 混合模型（如 qwen3.8-flash）默认开启思考，
        # 不关会让闪卡、问答等所有通用功能都多花一轮推理时间与 token
        "qwen": {
            "model": settings.openai_model,                  # OPENAI_MODEL
            "api_key": openai_key,
            "base_url": openai_url,
            "temperature": 0.5,
            "model_kwargs": {"extra_body": {"enable_thinking": False}},
        },
        "qwen-flash": {
            "model": "qwen3.6-flash",
            "api_key": openai_key,
            "base_url": openai_url,
            "temperature": 0.5,
            "model_kwargs": {"extra_body": {"enable_thinking": False}},
        },
        "qwen-preview": {
            "model": settings.openai_max_preview_model,      # qwen3-max-preview
            "api_key": openai_key,
            "base_url": openai_url,
            "temperature": 0.7,
            "model_kwargs": {"extra_body": {"enable_thinking": True}},
        },
        # 🆕 qwen3-max 关闭 thinking 模式 (用于 Router 等需要稳定 JSON 输出的场景)
        "qwen-no-think": {
            "model": settings.openai_model,                  # qwen3-max
            "api_key": openai_key,
            "base_url": openai_url,
            "temperature": 0.1,  # 低温度保证稳定性
            "model_kwargs": {"extra_body": {"enable_thinking": False}},
        },
    }


MODEL_CONFIGS: Mapping = _LazyConfigMapping(_build_model_configs)


# ==================== 嵌入模型配置 ====================


def _build_embedding_configs() -> Dict[str, Dict[str, Any]]:
    return {
        "dashscope": {
            "model": credential_store.model("dashscope"),
            "api_key": credential_store.api_key("dashscope"),
            "base_url": credential_store.base_url("dashscope"),
            "dimensions": credential_store.resolve("dashscope", "dimensions"),
        },
    }


EMBEDDING_CONFIGS: Mapping = _LazyConfigMapping(_build_embedding_configs)


# ==================== 功能 → 嵌入模型映射 ====================

FEATURE_EMBEDDING_MAP: Dict[str, str] = {
    "vector_store": "dashscope",         # 主向量存储
    "paper2slides_rag": "dashscope",     # 论文转PPT的RAG
}

# ==================== 功能 → 模型映射 ====================
# 修改此字典即可控制每个功能使用哪个模型

FEATURE_MODEL_MAP: Dict[str, str] = {
    # 原先由 Kimi 承担的功能已迁到 OpenAI 兼容端点（OPENAI_*，开发环境为模型网关）：
    # 普通调用用 qwen / qwen-no-think，需要思考的用 qwen-preview（OPENAI_MAX_PREVIEW_MODEL）

    # ===== 文档处理 =====
    "doc_parsing": "qwen-no-think",      # 文档解析 (摘要/关键术语，结构化输出需关闭思考)
    "doc_general": "qwen",               # 闪卡、测验、思维导图、学习路径
    "report": "deepseek-v4-flash",       # 长报告
    "paper_pipeline": "qwen",            # 论文转 PPT / 学术海报的文本与 RAG 阶段

    # ===== 问答系统 =====
    "qa_fast": "qwen",                   # QA 快速模式默认
    "qa_agent": "qwen-preview",          # QA 智能体模式（思考模型）
    "router": "qwen-no-think",           # router（需要稳定的结构化输出）

    # ===== 写作 =====
    "editor": "qwen",                    # 创作空间 AI 编辑
    "polish": "qwen-no-think",           # 润色 / 去 AI 味（JSON 输出，思考模式不支持）

    # ===== 内容生成 =====
    "flashcards": "qwen",                # 闪卡生成
    "quiz": "qwen",                      # 测验生成
    "mindmap": "qwen",                   # 思维导图生成
    "highlights": "qwen",                # 重点提炼
    "learning_path": "qwen",             # 学习路径

    # ===== 面试系统 =====
    "interview": "qwen",                 # 面试问答

    # ===== 知识图谱 =====
    "eureka": "qwen-preview",            # 灵感生成 (需要创造力，思考模型)
    "eureka_chat": "qwen",               # 灵感对话
    "merge_analysis": "qwen",            # 节点合并分析

    # ===== 检索增强 =====
    "hyde": "qwen",                      # HyDE 假设文档生成
}


# ==================== QA 模型选项 (前端可选) ====================
# 用于问答系统的用户可选模型列表

_QA_MODEL_DISPLAY_NAMES: Dict[str, str] = {
    "qwen": "通义千问",
    "qwen-preview": "通义千问 (长推理)",
    "moonshot": "Kimi",
    "kimi-thinking": "Kimi K2.6 (思考)",
    "kimi-thinking-turbo": "Kimi K2.6 (思考)",
    "deepseek": "DeepSeek",
    "deepseek-reasoner": "DeepSeek Reasoner",
}


def _build_qa_model_options() -> Dict[str, Dict[str, Any]]:
    model_configs = _build_model_configs()
    return {
        key: {"display_name": display_name, **model_configs[key]}
        for key, display_name in _QA_MODEL_DISPLAY_NAMES.items()
    }


QA_MODEL_OPTIONS: Mapping = _LazyConfigMapping(_build_qa_model_options)


def get_model_config(model_key: str, prefer_default: bool = True) -> Dict[str, Any]:
    """
    获取指定模型的配置参数

    Args:
        model_key: 模型键名 (如 "qwen", "moonshot" 等)

    Returns:
        模型配置字典
    """
    configs = _build_model_configs()
    if model_key not in configs:
        logger.warning(f"⚠️ 未知模型键 '{model_key}'，使用默认 qwen")
        model_key = "qwen"
    config = configs[model_key].copy()
    if not settings.is_local_mode:
        return config

    requested_provider = (
        "moonshot" if model_key.startswith(("moonshot", "kimi")) else
        "deepseek" if model_key.startswith("deepseek") else
        "qwen" if model_key.startswith("qwen") else "openai"
    )
    provider = credential_store.chat_provider(requested_provider, prefer_default=prefer_default)
    default_urls = {
        "openai": "https://api.openai.com/v1",
        "moonshot": "https://api.moonshot.cn/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    }
    config.update(
        model=credential_store.model(provider),
        api_key=credential_store.api_key(provider),
        base_url=credential_store.base_url(provider) or default_urls[provider],
    )
    # A generic OpenAI-compatible endpoint must not receive vendor-specific
    # thinking parameters merely because a feature used to be mapped to Kimi.
    config.pop("model_kwargs", None)
    if provider == "qwen":
        # 通义千问卡片明确是 Qwen：与托管版一样显式关闭思考。qwen3.x 混合模型默认开启思考，
        # 会让闪卡、问答等通用功能都多一轮推理
        config["model_kwargs"] = {"extra_body": {"enable_thinking": False}}
    config["_thinking_client"] = False
    return config


def _apply_model_kwargs(llm_kwargs: Dict[str, Any], config: Dict[str, Any]) -> None:
    """
    把配置中的 model_kwargs 填入 ChatOpenAI 构造参数。

    `extra_body`（Kimi / Qwen 的思考开关）必须作为显式参数传入：较旧的 langchain-openai
    （如 0.1.x）会直接拒绝 model_kwargs 里的 extra_body，导致客户端构造失败、问答 500；
    显式参数在新旧版本中都有效。

    厂商专有参数（thinking、reasoning_effort 等）一律写在 extra_body 里：model_kwargs 的其他键
    会作为 create() 的关键字参数交给 openai SDK，SDK 版本不认识就 TypeError；extra_body 在各版本
    都原样并入请求体。
    """
    model_kwargs = dict(config.get("model_kwargs") or {})
    extra_body = model_kwargs.pop("extra_body", None)
    if extra_body:
        llm_kwargs["extra_body"] = extra_body
    if model_kwargs:
        llm_kwargs["model_kwargs"] = model_kwargs


def _normalize_temperature_for_model(config: Dict[str, Any], temperature: Optional[float]) -> float:
    actual_temp = temperature if temperature is not None else config.get("temperature", 0.5)
    model = str(config.get("model", "")).lower()
    if model.startswith(_KIMI_FIXED_TEMPERATURE_MODELS):
        # Kimi K2.5 / K2.6 只接受固定温度，且取决于思考模式：思考（默认开启）必须为 1.0，
        # 关闭思考必须为 0.6，其他取值一律 400。规则来自模型本身，经代理网关调用同样适用。
        # 本地版不发送 thinking 参数，即默认的思考模式 → 1.0
        thinking = (((config.get("model_kwargs") or {}).get("extra_body") or {}).get("thinking") or {}).get("type")
        return KIMI_INSTANT_TEMPERATURE if thinking == "disabled" else KIMI_THINKING_TEMPERATURE
    if model.startswith("kimi-k2-thinking"):
        # 旧版思考模型 ID 只接受 1.0
        return KIMI_THINKING_TEMPERATURE
    return actual_temp


def effective_temperature(config: Dict[str, Any], temperature: Optional[float] = None) -> float:
    """给直接发 HTTP 请求（不经 ChatOpenAI）的调用方用：返回该模型实际允许的温度"""
    return _normalize_temperature_for_model(config, temperature)


def get_llm_for_feature(
    feature: str,
    temperature: Optional[float] = None,
    streaming: bool = False,
    use_cache: bool = True,  # 🆕 是否使用缓存
    **extra_kwargs
) -> ChatOpenAI:
    """
    获取指定功能的 LLM 客户端（带缓存）

    Args:
        feature: 功能名称 (如 "doc_parsing", "eureka" 等)
        temperature: 可选，覆盖默认温度
        streaming: 是否启用流式输出
        use_cache: 是否使用缓存（默认 True）
        **extra_kwargs: 额外的 ChatOpenAI 参数

    Returns:
        配置好的 ChatOpenAI 实例

    Example:
        llm = get_llm_for_feature("eureka")
        llm = get_llm_for_feature("qa_fast", streaming=True)
    """
    # 获取功能对应的模型键
    model_key = FEATURE_MODEL_MAP.get(feature)
    if not model_key:
        logger.warning(f"⚠️ 未知功能 '{feature}'，使用默认 qwen")
        model_key = "qwen"

    # 思考模型必须使用 ThinkingChatOpenAI，才能保留 reasoning_content
    # 获取模型配置
    config = get_model_config(model_key)
    use_thinking_client = config.get("_thinking_client", model_key in THINKING_MODEL_KEYS)

    # 计算实际 temperature；Kimi K2.6 服务端只接受固定值 0.6
    actual_temp = _normalize_temperature_for_model(config, temperature)

    # 🆕 构建缓存键（基于影响客户端行为的关键参数）
    # 凭据指纹必须参与缓存键：否则用户在设置页换了 Key，仍会命中持有旧 Key 的客户端。
    cache_key = (
        _cache_scope(),
        credential_store.fingerprint(),
        config["model"],
        config.get("base_url", ""),
        actual_temp,
        streaming,
        use_thinking_client,
        # extra_kwargs 转为 frozenset 以便哈希
        json.dumps(extra_kwargs, sort_keys=True, default=str)
    )

    if use_cache:
        with _llm_cache_lock:
            if cache_key in _llm_cache:
                _llm_cache.move_to_end(cache_key)
                return _llm_cache[cache_key]

    # 构建 ChatOpenAI 参数
    llm_kwargs = {
        "model": config["model"],
        "api_key": config["api_key"],
        "temperature": actual_temp,
        "request_timeout": 300,
        "max_retries": 3,
    }

    if config.get("base_url"):
        llm_kwargs["base_url"] = config["base_url"]

    _apply_model_kwargs(llm_kwargs, config)

    if streaming:
        llm_kwargs["streaming"] = True

    llm_kwargs.update(extra_kwargs)

    llm_cls = ThinkingChatOpenAI if use_thinking_client else ChatOpenAI
    llm = llm_cls(**llm_kwargs)

    if use_cache:
        with _llm_cache_lock:
            _llm_cache[cache_key] = llm
            while len(_llm_cache) > _LLM_CACHE_MAX_SIZE:
                _llm_cache.popitem(last=False)

    return llm


def get_qa_llm(model_key: str, use_cache: bool = True, **extra_kwargs) -> ChatOpenAI:
    """
    获取 QA 问答系统的 LLM 客户端 (支持用户选择模型，带缓存)

    Args:
        model_key: 用户选择的模型键 (来自前端)
        use_cache: 是否使用缓存（默认 True）
        **extra_kwargs: 额外的 ChatOpenAI 参数

    Returns:
        配置好的 ChatOpenAI 实例
    """
    qa_options = _build_qa_model_options()
    model_configs = _build_model_configs()

    # 如果用户选择的模型在 QA 选项中，使用该配置
    if model_key in qa_options:
        config = qa_options[model_key]
    elif model_key in model_configs:
        config = model_configs[model_key]
    else:
        # 回退到默认 QA 模型
        default_key = FEATURE_MODEL_MAP.get("qa_fast", "qwen")
        config = model_configs.get(default_key, model_configs["qwen"])

    if settings.is_local_mode:
        config = get_model_config(model_key if model_key in model_configs else "qwen", prefer_default=False)

    # 🆕 构建缓存键（含凭据指纹，见 get_llm_for_feature 注释）
    cache_key = (
        "qa",
        _cache_scope(),
        credential_store.fingerprint(),
        config["model"],
        config.get("base_url", ""),
        _normalize_temperature_for_model(config, None),
        json.dumps(extra_kwargs, sort_keys=True, default=str)
    )

    # 🆕 检查缓存
    if use_cache:
        with _llm_cache_lock:
            if cache_key in _llm_cache:
                _llm_cache.move_to_end(cache_key)
                return _llm_cache[cache_key]

    llm_kwargs = {
        "model": config["model"],
        "api_key": config["api_key"],
        "temperature": _normalize_temperature_for_model(config, None),
        "request_timeout": 300,   # 防止长时间挂起
        "max_retries": 3,         # 自动重试瞬态错误
    }

    if config.get("base_url"):
        llm_kwargs["base_url"] = config["base_url"]

    _apply_model_kwargs(llm_kwargs, config)

    llm_kwargs.update(extra_kwargs)

    # 🆕 思考模型使用 ThinkingChatOpenAI 以保留 reasoning_content
    if config.get("_thinking_client", model_key in THINKING_MODEL_KEYS):
        llm = ThinkingChatOpenAI(**llm_kwargs)
    else:
        llm = ChatOpenAI(**llm_kwargs)

    # 🆕 缓存客户端
    if use_cache:
        with _llm_cache_lock:
            _llm_cache[cache_key] = llm
            while len(_llm_cache) > _LLM_CACHE_MAX_SIZE:
                _llm_cache.popitem(last=False)

    return llm


def get_available_qa_models() -> list:
    """
    获取可用的 QA 模型列表 (用于前端展示)

    Returns:
        模型列表，每个元素包含 key, label, model 等字段
    """
    models = []
    if settings.is_local_mode:
        for provider, key, label in (("qwen", "qwen", "通义千问"),
                                     ("openai", "openai", "OpenAI 兼容模型"),
                                     ("moonshot", "moonshot", "Kimi"),
                                     ("deepseek", "deepseek", "DeepSeek")):
            if credential_store.has_key(provider):
                models.append({"key": key, "label": label,
                               "model": credential_store.model(provider)})
        return models
    for key, config in _build_qa_model_options().items():
        # 检查 API key 是否配置
        if not config.get("api_key"):
            continue

        models.append({
            "key": key,
            "label": config.get("display_name", key),
            "model": config.get("model"),
        })

    return models


# ==================== 嵌入模型辅助函数 ====================

def get_embedding_config(feature: str) -> Dict[str, Any]:
    """
    获取指定功能的嵌入模型配置

    Args:
        feature: 功能名称 (如 "vector_store", "paper2slides_rag")

    Returns:
        嵌入模型配置字典，包含 model, api_key, base_url

    Example:
        config = get_embedding_config("vector_store")
        model_name = config["model"]  # text-embedding-v4
    """
    embedding_key = FEATURE_EMBEDDING_MAP.get(feature, "dashscope")
    configs = _build_embedding_configs()

    if embedding_key not in configs:
        logger.warning(f"⚠️ 未知嵌入模型键 '{embedding_key}'，使用默认 dashscope")
        embedding_key = "dashscope"

    return configs[embedding_key].copy()
