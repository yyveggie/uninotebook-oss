"""
🆕 两级缓存 (L1: 内存 + L2: Redis)

工业级缓存策略，减少 Redis 网络往返：
- L1: 进程内 LRU 内存缓存（毫秒级响应）
- L2: Redis 分布式缓存（持久化，跨进程共享）

使用场景：
- Embedding 缓存（高频读取，数据量大）
- 文档解析缓存（计算成本高）
- 热点数据缓存

注意：L1 缓存是进程级的，多进程/分布式部署时各进程有独立的 L1 缓存
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from typing import Any, Optional, Callable, Awaitable
from dataclasses import dataclass, field

from .redis_client import cache_get_bytes, cache_set_bytes


@dataclass
class L1CacheEntry:
    """L1 缓存条目"""
    value: bytes
    expires_at: float  # Unix 时间戳
    created_at: float = field(default_factory=time.time)
    access_count: int = 0


class TwoLevelCache:
    """
    两级缓存实现
    
    L1: 进程内 LRU 内存缓存
    L2: Redis 分布式缓存
    
    读取流程:
    1. 检查 L1 -> 命中则返回
    2. 检查 L2 -> 命中则回填 L1 并返回
    3. 都未命中则返回 None
    
    写入流程:
    1. 写入 L1
    2. 异步写入 L2
    """
    
    def __init__(
        self,
        name: str = "default",
        l1_max_size: int = 1000,
        l1_ttl: int = 60,  # L1 默认 TTL (秒)
        l2_ttl: int = 3600,  # L2 默认 TTL (秒)
        enable_l1: bool = True,
        enable_l2: bool = True,
    ):
        """
        初始化两级缓存
        
        Args:
            name: 缓存名称（用于日志和指标）
            l1_max_size: L1 最大条目数
            l1_ttl: L1 默认 TTL (秒)
            l2_ttl: L2 默认 TTL (秒)
            enable_l1: 是否启用 L1 缓存
            enable_l2: 是否启用 L2 缓存
        """
        self.name = name
        self.l1_max_size = l1_max_size
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl
        self.enable_l1 = enable_l1
        self.enable_l2 = enable_l2
        
        # L1: 使用 OrderedDict 实现 LRU
        self._l1_cache: OrderedDict[str, L1CacheEntry] = OrderedDict()
        self._l1_lock = asyncio.Lock()
        
        # 统计
        self._stats = {
            "l1_hits": 0,
            "l1_misses": 0,
            "l2_hits": 0,
            "l2_misses": 0,
            "writes": 0,
        }
        
        # 定期清理任务
        self._cleanup_task: Optional[asyncio.Task] = None
    
    def _make_l2_key(self, key: str) -> str:
        """生成 L2 缓存键（添加前缀避免冲突）"""
        return f"l2:{self.name}:{key}"
    
    async def get(self, key: str) -> Optional[bytes]:
        """
        获取缓存值
        
        Args:
            key: 缓存键
            
        Returns:
            缓存值（字节），未命中返回 None
        """
        # 1. 检查 L1
        if self.enable_l1:
            entry = self._l1_cache.get(key)
            if entry is not None:
                now = time.time()
                if entry.expires_at > now:
                    # L1 命中
                    entry.access_count += 1
                    # 更新 LRU 顺序
                    self._l1_cache.move_to_end(key)
                    self._stats["l1_hits"] += 1
                    return entry.value
                else:
                    # L1 过期，删除
                    del self._l1_cache[key]
            
            self._stats["l1_misses"] += 1
        
        # 2. 检查 L2
        if self.enable_l2:
            l2_key = self._make_l2_key(key)
            value = await cache_get_bytes(l2_key)
            
            if value is not None:
                # L2 命中，回填 L1
                self._stats["l2_hits"] += 1
                if self.enable_l1:
                    await self._l1_set(key, value, self.l1_ttl)
                return value
            
            self._stats["l2_misses"] += 1
        
        return None
    
    async def set(
        self, 
        key: str, 
        value: bytes, 
        l1_ttl: Optional[int] = None,
        l2_ttl: Optional[int] = None,
    ) -> None:
        """
        设置缓存值
        
        Args:
            key: 缓存键
            value: 缓存值（字节）
            l1_ttl: L1 TTL (秒)，None 使用默认值
            l2_ttl: L2 TTL (秒)，None 使用默认值
        """
        l1_ttl = l1_ttl or self.l1_ttl
        l2_ttl = l2_ttl or self.l2_ttl
        
        self._stats["writes"] += 1
        
        # 1. 写入 L1
        if self.enable_l1:
            await self._l1_set(key, value, l1_ttl)
        
        # 2. 异步写入 L2（不阻塞）
        if self.enable_l2:
            l2_key = self._make_l2_key(key)
            # 使用 create_task 异步执行，不等待
            asyncio.create_task(self._safe_l2_set(l2_key, value, l2_ttl))
    
    async def _l1_set(self, key: str, value: bytes, ttl: int) -> None:
        """L1 缓存写入（带 LRU 淘汰）"""
        async with self._l1_lock:
            now = time.time()
            
            # 如果已存在，更新
            if key in self._l1_cache:
                del self._l1_cache[key]
            
            # 检查容量，LRU 淘汰
            while len(self._l1_cache) >= self.l1_max_size:
                # 淘汰最久未访问的（OrderedDict 的第一个）
                self._l1_cache.popitem(last=False)
            
            # 添加新条目
            self._l1_cache[key] = L1CacheEntry(
                value=value,
                expires_at=now + ttl,
                created_at=now,
            )
    
    async def _safe_l2_set(self, key: str, value: bytes, ttl: int) -> None:
        """安全的 L2 写入（捕获异常）"""
        try:
            await cache_set_bytes(key, value, ttl=ttl)
        except Exception as e:
            # L2 写入失败不影响主流程
            pass
    
    async def delete(self, key: str) -> None:
        """删除缓存"""
        # 删除 L1
        if key in self._l1_cache:
            del self._l1_cache[key]
        
        # 删除 L2
        if self.enable_l2:
            from .redis_client import cache_delete
            l2_key = self._make_l2_key(key)
            await cache_delete(l2_key)
    
    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[bytes]],
        l1_ttl: Optional[int] = None,
        l2_ttl: Optional[int] = None,
    ) -> Optional[bytes]:
        """
        获取缓存，未命中时调用 factory 生成并缓存
        
        Args:
            key: 缓存键
            factory: 生成值的异步函数
            l1_ttl: L1 TTL
            l2_ttl: L2 TTL
            
        Returns:
            缓存值
        """
        # 先尝试获取
        value = await self.get(key)
        if value is not None:
            return value
        
        # 未命中，调用 factory
        value = await factory()
        if value is not None:
            await self.set(key, value, l1_ttl, l2_ttl)
        
        return value
    
    def get_stats(self) -> dict:
        """获取缓存统计"""
        l1_size = len(self._l1_cache)
        total_hits = self._stats["l1_hits"] + self._stats["l2_hits"]
        total_misses = self._stats["l1_misses"]  # L1 miss 才会查 L2
        total_requests = total_hits + total_misses
        
        return {
            "name": self.name,
            "l1_size": l1_size,
            "l1_max_size": self.l1_max_size,
            "l1_hits": self._stats["l1_hits"],
            "l1_misses": self._stats["l1_misses"],
            "l2_hits": self._stats["l2_hits"],
            "l2_misses": self._stats["l2_misses"],
            "writes": self._stats["writes"],
            "hit_rate": total_hits / total_requests if total_requests > 0 else 0,
            "l1_hit_rate": self._stats["l1_hits"] / total_requests if total_requests > 0 else 0,
        }
    
    async def cleanup_expired(self) -> int:
        """清理过期的 L1 条目"""
        now = time.time()
        expired_keys = []
        
        for key, entry in self._l1_cache.items():
            if entry.expires_at <= now:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self._l1_cache[key]
        
        return len(expired_keys)
    
    def clear_l1(self) -> None:
        """清空 L1 缓存"""
        self._l1_cache.clear()


# ==================== 预配置的缓存实例 ====================

# Embedding 缓存：高频读取，数据持久
embedding_cache = TwoLevelCache(
    name="embedding",
    l1_max_size=2000,       # 内存中最多缓存 2000 个 embedding
    l1_ttl=300,             # L1: 5 分钟（热点数据）
    l2_ttl=30 * 24 * 3600,  # L2: 30 天（几乎永久）
)

# 文档解析缓存：计算成本高
doc_parse_cache = TwoLevelCache(
    name="doc_parse",
    l1_max_size=100,        # 内存中最多缓存 100 个解析结果
    l1_ttl=600,             # L1: 10 分钟
    l2_ttl=7 * 24 * 3600,   # L2: 7 天
)

# 通用热点数据缓存
hotspot_cache = TwoLevelCache(
    name="hotspot",
    l1_max_size=500,
    l1_ttl=60,              # L1: 1 分钟
    l2_ttl=3600,            # L2: 1 小时
)


# ==================== 便捷函数 ====================

async def get_embedding_cached(key: str) -> Optional[bytes]:
    """获取 Embedding 缓存"""
    return await embedding_cache.get(key)


async def set_embedding_cached(key: str, value: bytes) -> None:
    """设置 Embedding 缓存"""
    await embedding_cache.set(key, value)


async def get_doc_parse_cached(key: str) -> Optional[bytes]:
    """获取文档解析缓存"""
    return await doc_parse_cache.get(key)


async def set_doc_parse_cached(key: str, value: bytes) -> None:
    """设置文档解析缓存"""
    await doc_parse_cache.set(key, value)


def get_all_cache_stats() -> dict:
    """获取所有缓存的统计"""
    return {
        "embedding": embedding_cache.get_stats(),
        "doc_parse": doc_parse_cache.get_stats(),
        "hotspot": hotspot_cache.get_stats(),
    }
