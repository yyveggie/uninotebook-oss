"""
缓存服务统一入口

子模块：
- redis_client: Redis 异步连接、读写、健康检查
- config: TTL 配置常量
- l2: 两级缓存 (L1 内存 + L2 Redis)
- manager: 高级缓存 API（文档/聊天/笔记等）
"""

# ---- redis_client (原 cache.py) ----
from .redis_client import (
    get_redis_client,
    cache_get_bytes,
    cache_set_bytes,
    cache_delete,
    health_check,
    close_redis,
    is_cache_available,
)

# ---- config (原 cache_config.py) ----
from .config import CacheTTL, CACHE_TTL

# ---- l2 (原 cache_l2.py) ----
from .l2 import (
    TwoLevelCache,
    embedding_cache,
    doc_parse_cache,
    hotspot_cache,
    get_embedding_cached,
    set_embedding_cached,
    get_doc_parse_cached,
    set_doc_parse_cached,
    get_all_cache_stats,
)

# ---- manager (原 cache_manager.py) ----
from .manager import (
    get_json,
    set_json,
    delete,
    get_user_document_history,
    set_user_document_history,
    invalidate_user_document_history,
    get_document_history_cache,
    set_document_history_cache,
    invalidate_document_history,
    get_document_data_cache,
    set_document_data_cache,
    invalidate_document_data,
    get_document_content_cache,
    set_document_content_cache,
    invalidate_document_content,
    get_user_notes_cache,
    set_user_notes_cache,
    invalidate_user_notes_cache,
    get_document_list_cache,
    set_document_list_cache,
    invalidate_document_list_cache,
    get_chat_history_cache,
    set_chat_history_cache,
    invalidate_chat_history_cache,
    warmup_user_cache,
)
