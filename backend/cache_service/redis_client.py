"""
Redis异步缓存工具 - 工业级实现
支持连接池、健康检查、降级策略

🔧 修复：Celery prefork worker 中的事件循环兼容性
- 不再使用模块级 asyncio.Lock（会绑定到错误的事件循环）
- 使用 per-loop 客户端管理
"""

from __future__ import annotations

import asyncio
from typing import Optional, Dict
from redis.asyncio import Redis, ConnectionPool
from redis.exceptions import RedisError, ConnectionError, TimeoutError

from config import settings
from utils.logger import get_logger
logger = get_logger(__name__)

# 🔧 修复：使用 per-loop 客户端字典，而不是全局单例
# 这样每个事件循环都有自己的 Redis 客户端，避免 Lock 绑定错误
_redis_clients: Dict[int, Redis] = {}
_redis_pools: Dict[int, ConnectionPool] = {}
_is_healthy = True  # 健康状态标记


def _get_current_loop_id() -> int:
    """获取当前事件循环的唯一ID"""
    try:
        loop = asyncio.get_running_loop()
        return id(loop)
    except RuntimeError:
        return 0


async def _init_client() -> Optional[Redis]:
    """初始化异步Redis客户端（带连接池）"""
    # 创建连接池（工业级配置）
    pool = ConnectionPool.from_url(
        settings.redis_url,
        max_connections=50,        # 最大连接数
        decode_responses=False,    # 保持字节类型
        socket_timeout=5.0,        # 5秒超时
        socket_connect_timeout=3.0, # 3秒连接超时
        retry_on_timeout=True,     # 超时重试
        health_check_interval=30   # 30秒健康检查
    )
    
    client = Redis(connection_pool=pool)
    
    # 测试连接
    await asyncio.wait_for(client.ping(), timeout=3.0)
    
    # 存储到 per-loop 字典
    loop_id = _get_current_loop_id()
    _redis_pools[loop_id] = pool
    
    return client


async def get_redis_client() -> Optional[Redis]:
    """获取异步Redis客户端（per-loop 单例，避免 Lock 绑定问题）"""
    if not settings.redis_enabled:
        return None

    global _is_healthy
    
    loop_id = _get_current_loop_id()
    if loop_id == 0:
        return None  # 没有运行中的事件循环
    
    # 检查当前事件循环是否已有客户端
    if loop_id not in _redis_clients:
        try:
            client = await _init_client()
            _redis_clients[loop_id] = client
            _is_healthy = True
            logger.info(f"🔌 Redis 异步缓存已启用（连接池: 50, loop_id: {loop_id}）")
        except Exception as exc:
            logger.warning(f"⚠️ 连接 Redis 失败，将降级到无缓存模式: {exc}")
            _is_healthy = False
            return None
    
    return _redis_clients.get(loop_id)


async def is_cache_available() -> bool:
    """
    缓存当前是否可用。

    用于区分「操作失败」与「缓存根本不可用」——两者语义完全不同：
    例如 SETNX 返回 False，既可能是锁被别人持有（应拒绝重复请求），
    也可能是 Redis 不可用（此时应放行，而不是把所有请求都判为重复）。
    """
    if not settings.redis_enabled:
        return False
    return await get_redis_client() is not None


async def cache_get_bytes(key: str) -> Optional[bytes]:
    """异步获取缓存（带超时和降级）"""
    global _is_healthy
    
    if not _is_healthy:
        return None
    
    client = await get_redis_client()
    if client is None:
        return None
    
    try:
        # 设置3秒超时，避免慢查询
        return await asyncio.wait_for(client.get(key), timeout=3.0)
    except (RedisError, asyncio.TimeoutError) as exc:
        # 连续失败时标记为不健康
        if isinstance(exc, ConnectionError):
            _is_healthy = False
            logger.error(f"❌ Redis 连接失败，降级到无缓存模式")
        else:
            logger.warning(f"⚠️ Redis GET 失败: {exc}")
        return None
    except RuntimeError as exc:
        # 🔧 新增：捕获事件循环绑定错误，降级处理
        if "bound to a different event loop" in str(exc):
            loop_id = _get_current_loop_id()
            _redis_clients.pop(loop_id, None)
            _redis_pools.pop(loop_id, None)
            logger.warning(f"⚠️ Redis 客户端事件循环不匹配，已重置 (loop_id: {loop_id})")
        return None


async def cache_set_bytes(key: str, value: bytes, ttl: Optional[int] = None) -> None:
    """异步设置缓存（带超时和降级）"""
    global _is_healthy
    
    if not _is_healthy:
        return
    
    client = await get_redis_client()
    if client is None:
        return
    
    try:
        # 设置3秒超时
        await asyncio.wait_for(
            client.set(key, value, ex=ttl or settings.cache_ttl_seconds),
            timeout=3.0
        )
    except (RedisError, asyncio.TimeoutError) as exc:
        if isinstance(exc, ConnectionError):
            _is_healthy = False
            logger.error(f"❌ Redis 连接失败，降级到无缓存模式")
        else:
            logger.warning(f"⚠️ Redis SET 失败: {exc}")
    except RuntimeError as exc:
        if "bound to a different event loop" in str(exc):
            loop_id = _get_current_loop_id()
            _redis_clients.pop(loop_id, None)
            _redis_pools.pop(loop_id, None)
            logger.warning(f"⚠️ Redis 客户端事件循环不匹配，已重置 (loop_id: {loop_id})")


async def cache_set_nx_bytes(key: str, value: bytes, ttl: Optional[int] = None) -> bool:
    """仅当 key 不存在时写入（SET NX），返回是否写入成功。"""
    global _is_healthy

    if not _is_healthy:
        return False

    client = await get_redis_client()
    if client is None:
        return False

    try:
        result = await asyncio.wait_for(
            client.set(key, value, ex=ttl or settings.cache_ttl_seconds, nx=True),
            timeout=3.0
        )
        return bool(result)
    except (RedisError, asyncio.TimeoutError) as exc:
        if isinstance(exc, ConnectionError):
            _is_healthy = False
            logger.error("❌ Redis 连接失败，降级到无缓存模式")
        else:
            logger.warning(f"⚠️ Redis SETNX 失败: {exc}")
        return False
    except RuntimeError as exc:
        if "bound to a different event loop" in str(exc):
            loop_id = _get_current_loop_id()
            _redis_clients.pop(loop_id, None)
            _redis_pools.pop(loop_id, None)
            logger.warning(f"⚠️ Redis 客户端事件循环不匹配，已重置 (loop_id: {loop_id})")
        return False


async def cache_delete(key: str) -> None:
    """删除缓存键"""
    global _is_healthy

    if not _is_healthy:
        return

    client = await get_redis_client()
    if client is None:
        return

    try:
        await asyncio.wait_for(client.delete(key), timeout=3.0)
    except (RedisError, asyncio.TimeoutError) as exc:
        if isinstance(exc, ConnectionError):
            _is_healthy = False
            logger.error("❌ Redis 连接失败，降级到无缓存模式")
        else:
            logger.warning(f"⚠️ Redis DEL 失败: {exc}")
    except RuntimeError as exc:
        if "bound to a different event loop" in str(exc):
            loop_id = _get_current_loop_id()
            _redis_clients.pop(loop_id, None)
            _redis_pools.pop(loop_id, None)


async def health_check() -> bool:
    """健康检查（用于监控端点）"""
    try:
        client = await get_redis_client()
        if client is None:
            return False
        
        await asyncio.wait_for(client.ping(), timeout=2.0)
        
        global _is_healthy
        _is_healthy = True
        return True
    except Exception:
        return False


async def close_redis():
    """关闭Redis连接（应用关闭时调用）"""
    global _redis_clients, _redis_pools
    
    for loop_id, client in list(_redis_clients.items()):
        try:
            await client.close()
        except Exception:
            pass
    _redis_clients.clear()
    
    for loop_id, pool in list(_redis_pools.items()):
        try:
            await pool.disconnect()
        except Exception:
            pass
    _redis_pools.clear()
    
    logger.info("👋 Redis 连接已关闭")
