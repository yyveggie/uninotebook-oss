"""LLM 配置与客户端"""
from .llm_config import (
    MODEL_CONFIGS,
    EMBEDDING_CONFIGS,
    FEATURE_EMBEDDING_MAP,
    FEATURE_MODEL_MAP,
    QA_MODEL_OPTIONS,
    THINKING_MODEL_KEYS,
    get_model_config,
    get_llm_for_feature,
    get_qa_llm,
    get_available_qa_models,
    get_embedding_config,
    clear_llm_cache,
)
from .thinking_llm import ThinkingChatOpenAI
