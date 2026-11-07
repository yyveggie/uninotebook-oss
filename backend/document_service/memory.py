"""
文档问答 Memory 管理模块

基于 PostgreSQL + Redis 的两层记忆架构：
- 运行摘要 (Running Summary)：覆盖全部历史轮次的 LLM 摘要（Redis 缓存 + 按需重建）
- 最近原文 (Recent Raw)：最近 N 轮原始对话（从 DB 加载）

设计原则：
1. 多用户隔离：使用 user_id + document_id + session_id 作为唯一标识
2. 成本控制：摘要仅在每次问答结束后更新一次（后台异步），不在 Agent 迭代中调用 LLM
3. Token 优化：Agent 看到的是 [Summary] + [最近 2 轮原文] + [当前问题]
"""

import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import DocumentChatHistory, ConversationSummary
from database import AsyncSessionLocal
from cache_service import get_redis_client
from utils.logger import get_logger
logger = get_logger(__name__)


# ==================== 配置 ====================

# Redis 缓存配置
MEMORY_CACHE_PREFIX = "chat_memory"
SUMMARY_CACHE_PREFIX = "chat_summary"
MEMORY_CACHE_TTL = 28800  # 8小时过期（每次问答后 save_summary 会刷新，不会有过期数据）
SUMMARY_CACHE_TTL = 28800  # 摘要同步 8 小时

# 历史记录限制
MAX_RECENT_TURNS = 3  # Agent 注入的最近原文轮数
MAX_HISTORY_TURNS = MAX_RECENT_TURNS  # 兼容旧导出名
MAX_HISTORY_TOKENS = 6000  # 兼容旧导出名

# 摘要配置
SUMMARY_MAX_LENGTH = 800  # 摘要最大长度（字符）


# ==================== 数据结构 ====================

@dataclass
class ChatMessage:
    """单条对话消息"""
    role: str  # 'user' or 'assistant'
    content: str
    timestamp: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChatMessage":
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]) if data.get("timestamp") else None
        )


@dataclass  
class ConversationMemory:
    """会话记忆 — 包含运行摘要 + 最近原文"""
    user_id: int
    document_id: int
    session_id: str
    messages: List[ChatMessage]
    summary: str = ""  # 运行摘要（覆盖所有非 recent 的历史）
    
    @property
    def thread_id(self) -> str:
        """生成唯一的线程ID（用于多用户隔离）"""
        return f"{self.user_id}_{self.document_id}_{self.session_id}"
    
    def to_langchain_format(self) -> List[Dict[str, str]]:
        """转换为 LangChain 消息格式"""
        result = []
        for msg in self.messages:
            if msg.role == "user":
                result.append({"question": msg.content, "answer": ""})
            elif msg.role == "assistant" and result:
                result[-1]["answer"] = msg.content
        # 过滤掉没有回答的消息
        return [m for m in result if m.get("answer")]
    
    def get_recent_messages(self, max_turns: int = MAX_RECENT_TURNS) -> List[ChatMessage]:
        """获取最近的对话（按轮数限制）"""
        if len(self.messages) <= max_turns * 2:
            return self.messages
        return self.messages[-(max_turns * 2):]
    
    def estimate_tokens(self) -> int:
        """粗略估算 Token 数（1 token ≈ 2 中文字符 或 4 英文字符）"""
        total_chars = sum(len(msg.content) for msg in self.messages)
        total_chars += len(self.summary)
        return total_chars // 2


# ==================== Memory Manager ====================

class ConversationMemoryManager:
    """
    会话记忆管理器 — 运行摘要 + 最近原文
    
    职责：
    1. 加载会话记忆：运行摘要 + 最近 N 轮原始对话
    2. 更新运行摘要：每次问答结束后，后台异步更新
    3. Redis 缓存加速，PostgreSQL 持久化兜底
    """
    
    def __init__(self, db: AsyncSession):
        self.db = db
        self._redis = None
    
    async def _get_redis(self):
        """获取 Redis 客户端（懒加载）"""
        if self._redis is None:
            self._redis = await get_redis_client()
        return self._redis
    
    def _cache_key(self, user_id: int, document_id: int, session_id: str) -> str:
        """生成消息 Redis 缓存键"""
        return f"{MEMORY_CACHE_PREFIX}:{user_id}:{document_id}:{session_id}"
    
    def _summary_key(self, user_id: int, document_id: int, session_id: str) -> str:
        """生成摘要 Redis 缓存键"""
        return f"{SUMMARY_CACHE_PREFIX}:{user_id}:{document_id}:{session_id}"
    
    # ==================== 加载 ====================
    
    async def load_memory(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        max_turns: int = MAX_RECENT_TURNS
    ) -> ConversationMemory:
        """
        加载会话记忆：运行摘要 + 最近 N 轮原始对话
        
        优先从 Redis 加载，未命中则从 PostgreSQL 加载
        """
        # 并行加载摘要和最近消息
        summary = await self._load_summary(user_id, document_id, session_id)
        messages = await self._load_recent_messages(user_id, document_id, session_id, max_turns)
        
        logger.info(f"📦 [Memory] Loaded: summary={len(summary)}chars, recent={len(messages)}msgs")
        
        return ConversationMemory(
            user_id=user_id,
            document_id=document_id,
            session_id=session_id,
            messages=messages,
            summary=summary
        )
    
    async def _load_summary(self, user_id: int, document_id: int, session_id: str) -> str:
        """加载运行摘要（Redis → PostgreSQL 兜底）"""
        # 1. 尝试 Redis
        try:
            redis = await self._get_redis()
            if redis:
                key = self._summary_key(user_id, document_id, session_id)
                cached = await redis.get(key)
                if cached:
                    return cached.decode() if isinstance(cached, bytes) else str(cached)
        except Exception as e:
            logger.warning(f"⚠️ [Memory] Redis 摘要加载失败: {e}")
        
        # 2. 兜底：从 PostgreSQL 加载
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ConversationSummary).filter(
                        ConversationSummary.user_id == user_id,
                        ConversationSummary.document_id == document_id,
                        ConversationSummary.session_id == session_id,
                    )
                )
                record = result.scalar_one_or_none()
                if record and record.summary:
                    # 回填 Redis 缓存
                    try:
                        redis = await self._get_redis()
                        if redis:
                            key = self._summary_key(user_id, document_id, session_id)
                            await redis.setex(key, SUMMARY_CACHE_TTL, record.summary)
                    except Exception:
                        pass
                    logger.info(f"   📚 [Memory] PostgreSQL 摘要兜底: {len(record.summary)} chars")
                    return record.summary
        except Exception as e:
            logger.warning(f"⚠️ [Memory] PostgreSQL 摘要加载失败: {e}")
        
        return ""
    
    async def _load_recent_messages(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        max_turns: int
    ) -> List[ChatMessage]:
        """加载最近 N 轮消息（Redis → PostgreSQL）"""
        cache_key = self._cache_key(user_id, document_id, session_id)
        
        # 1. 尝试从 Redis 加载
        try:
            redis = await self._get_redis()
            if redis:
                cached = await redis.get(cache_key)
                if cached:
                    data = json.loads(cached)
                    messages = [ChatMessage.from_dict(m) for m in data.get("messages", [])]
                    recent = messages[-(max_turns * 2):]
                    logger.info(f"   📦 [Memory] Redis 命中: {len(recent)} 条消息")
                    return recent
        except Exception as e:
            logger.warning(f"   ⚠️ [Memory] Redis 读取失败: {e}")
        
        # 2. 从 PostgreSQL 加载
        messages = await self._load_from_db(user_id, document_id, session_id, max_turns)
        logger.info(f"   📚 [Memory] PostgreSQL 加载: {len(messages)} 条消息")
        
        # 3. 写入 Redis 缓存
        await self._save_messages_to_cache(user_id, document_id, session_id, messages)
        
        return messages
    
    async def _load_from_db(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        max_turns: int
    ) -> List[ChatMessage]:
        """从 PostgreSQL 加载历史记录"""
        try:
            result = await self.db.execute(
                select(DocumentChatHistory)
                .filter(
                    DocumentChatHistory.document_id == document_id,
                    DocumentChatHistory.user_id == user_id,
                    DocumentChatHistory.session_id == session_id
                )
                .order_by(desc(DocumentChatHistory.created_at))
                .limit(max_turns)
            )
            records = result.scalars().all()
            
            # 转换为 ChatMessage 列表（按时间正序）
            messages = []
            for record in reversed(records):
                # 跳过占位符消息
                if record.answer in ["会话已创建，等待您的第一个问题...", "会话已创建。"]:
                    continue
                if record.question:
                    messages.append(ChatMessage(
                        role="user",
                        content=record.question,
                        timestamp=record.created_at
                    ))
                if record.answer:
                    messages.append(ChatMessage(
                        role="assistant",
                        content=record.answer,
                        timestamp=record.created_at
                    ))
            
            return messages
            
        except Exception as e:
            logger.warning(f"⚠️ [Memory] PostgreSQL 读取失败: {e}")
            return []
    
    # ==================== 保存 ====================
    
    async def _save_messages_to_cache(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        messages: List[ChatMessage]
    ):
        """保存消息到 Redis 缓存"""
        try:
            redis = await self._get_redis()
            if redis:
                cache_key = self._cache_key(user_id, document_id, session_id)
                data = {"messages": [m.to_dict() for m in messages]}
                await redis.setex(cache_key, MEMORY_CACHE_TTL, json.dumps(data))
        except Exception as e:
            logger.warning(f"⚠️ [Memory] Redis 写入失败: {e}")
    
    async def save_summary(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        summary: str
    ):
        """保存运行摘要到 Redis + PostgreSQL 双写"""
        # 1. 写 Redis 缓存（快速生效）
        try:
            redis = await self._get_redis()
            if redis:
                key = self._summary_key(user_id, document_id, session_id)
                await redis.setex(key, SUMMARY_CACHE_TTL, summary)
        except Exception as e:
            logger.warning(f"⚠️ [Memory] Redis 摘要写入失败: {e}")
        
        # 2. 写 PostgreSQL 持久化（UPSERT：存在则更新，不存在则插入）
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ConversationSummary).filter(
                        ConversationSummary.user_id == user_id,
                        ConversationSummary.document_id == document_id,
                        ConversationSummary.session_id == session_id,
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    record.summary = summary
                else:
                    db.add(ConversationSummary(
                        user_id=user_id,
                        document_id=document_id,
                        session_id=session_id,
                        summary=summary,
                    ))
                await db.commit()
                logger.info(f"✅ [Memory] 摘要已持久化: {len(summary)} chars")
        except Exception as e:
            logger.warning(f"⚠️ [Memory] PostgreSQL 摘要写入失败: {e}")
    
    async def add_message(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        question: str,
        answer: str
    ):
        """
        添加新的对话消息到记忆
        
        同时更新 Redis 缓存（PostgreSQL 由 QAManager 负责保存）
        """
        cache_key = self._cache_key(user_id, document_id, session_id)
        
        try:
            redis = await self._get_redis()
            if redis:
                # 加载现有缓存
                cached = await redis.get(cache_key)
                messages = []
                if cached:
                    data = json.loads(cached)
                    messages = data.get("messages", [])
                
                # 添加新消息
                now = datetime.utcnow().isoformat()
                messages.append({"role": "user", "content": question, "timestamp": now})
                messages.append({"role": "assistant", "content": answer, "timestamp": now})
                
                # 限制数量（只保留最近 N 轮）
                max_msgs = MAX_RECENT_TURNS * 2
                if len(messages) > max_msgs:
                    messages = messages[-max_msgs:]
                
                # 保存回 Redis
                await redis.setex(cache_key, MEMORY_CACHE_TTL, json.dumps({"messages": messages}))
                logger.info(f"✅ [Memory] 缓存已更新: {len(messages)} 条消息")
                
        except Exception as e:
            logger.warning(f"⚠️ [Memory] 缓存更新失败: {e}")
    
    async def clear_memory(
        self,
        user_id: int,
        document_id: int,
        session_id: str
    ):
        """清除会话记忆（Redis 缓存 + PostgreSQL 摘要）"""
        try:
            redis = await self._get_redis()
            if redis:
                cache_key = self._cache_key(user_id, document_id, session_id)
                summary_key = self._summary_key(user_id, document_id, session_id)
                await redis.delete(cache_key)
                await redis.delete(summary_key)
        except Exception as e:
            logger.warning(f"⚠️ [Memory] Redis 缓存清除失败: {e}")
        
        # 同时清除 PostgreSQL 中的摘要
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ConversationSummary).filter(
                        ConversationSummary.user_id == user_id,
                        ConversationSummary.document_id == document_id,
                        ConversationSummary.session_id == session_id,
                    )
                )
                record = result.scalar_one_or_none()
                if record:
                    await db.delete(record)
                    await db.commit()
        except Exception as e:
            logger.warning(f"⚠️ [Memory] PostgreSQL 摘要清除失败: {e}")
        
        logger.info(f"🗑️ [Memory] 缓存已清除")


# ==================== 工具函数 ====================

def truncate_history_by_tokens(
    messages: List[ChatMessage],
    max_tokens: int = MAX_HISTORY_TOKENS
) -> List[ChatMessage]:
    """
    按 Token 数量截断历史
    
    保留最近的消息，直到达到 Token 限制
    """
    if not messages:
        return []
    
    result = []
    total_tokens = 0
    
    for msg in reversed(messages):
        # 粗略估算 token 数
        msg_tokens = len(msg.content) // 2
        
        if total_tokens + msg_tokens > max_tokens:
            break
        
        result.insert(0, msg)
        total_tokens += msg_tokens
    
    return result
