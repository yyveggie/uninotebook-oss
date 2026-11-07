from __future__ import annotations

import json
from typing import Any, Optional

from .redis_client import cache_get_bytes, cache_set_bytes, cache_delete, get_redis_client
from .config import CACHE_TTL
from utils.logger import get_logger
logger = get_logger(__name__)


def _history_key(user_id: int, limit: int) -> str:
    return f"user:{user_id}:history:documents:limit:{limit}"


def _history_index_key(user_id: int) -> str:
    return f"user:{user_id}:history:documents:index"


def _doc_history_key(user_id: int, document_id: int, name: str) -> str:
    return f"user:{user_id}:doc:{document_id}:history:{name}"


def _doc_history_index_key(user_id: int, document_id: int) -> str:
    return f"user:{user_id}:doc:{document_id}:history:index"


async def get_json(key: str) -> Optional[Any]:
    cached = await cache_get_bytes(key)
    if not cached:
        return None

    try:
        return json.loads(cached.decode("utf-8"))
    except Exception:
        return None


async def set_json(key: str, value: Any, ttl: Optional[int] = None) -> None:
    payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
    await cache_set_bytes(key, payload, ttl=ttl)


async def delete(key: str) -> None:
    await cache_delete(key)


async def get_user_document_history(user_id: int, limit: int) -> Optional[Any]:
    key = _history_key(user_id, limit)
    return await get_json(key)


async def set_user_document_history(user_id: int, limit: int, value: Any, ttl: Optional[int] = None) -> None:
    key = _history_key(user_id, limit)
    await set_json(key, value, ttl or CACHE_TTL.USER_DOCUMENT_HISTORY)

    client = await get_redis_client()
    if client:
        try:
            await client.sadd(_history_index_key(user_id), key)
        except Exception:
            pass


async def invalidate_user_document_history(user_id: int) -> None:
    client = await get_redis_client()
    if client:
        try:
            index_key = _history_index_key(user_id)
            keys = await client.smembers(index_key)
            if keys:
                await client.delete(*keys)
            await client.delete(index_key)
        except Exception:
            pass


async def get_document_history_cache(user_id: int, document_id: int, name: str) -> Optional[Any]:
    key = _doc_history_key(user_id, document_id, name)
    return await get_json(key)


async def set_document_history_cache(
    user_id: int,
    document_id: int,
    name: str,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    key = _doc_history_key(user_id, document_id, name)
    await set_json(key, value, ttl or CACHE_TTL.DEFAULT)

    client = await get_redis_client()
    if client:
        try:
            await client.sadd(_doc_history_index_key(user_id, document_id), key)
        except Exception:
            pass


async def invalidate_document_history(user_id: int, document_id: int, names: Optional[list[str]] = None) -> None:
    client = await get_redis_client()
    if client and not names:
        try:
            index_key = _doc_history_index_key(user_id, document_id)
            keys = await client.smembers(index_key)
            if keys:
                await client.delete(*keys)
            await client.delete(index_key)
        except Exception:
            pass

    if names:
        for name in names:
            key = _doc_history_key(user_id, document_id, name)
            await delete(key)
        if client:
            try:
                await client.srem(
                    _doc_history_index_key(user_id, document_id),
                    *[_doc_history_key(user_id, document_id, name) for name in names],
                )
            except Exception:
                pass


def _doc_data_key(user_id: int, document_id: int, name: str) -> str:
    return f"user:{user_id}:doc:{document_id}:data:{name}"


def _doc_data_index_key(user_id: int, document_id: int) -> str:
    return f"user:{user_id}:doc:{document_id}:data:index"


async def get_document_data_cache(user_id: int, document_id: int, name: str) -> Optional[Any]:
    key = _doc_data_key(user_id, document_id, name)
    return await get_json(key)


async def set_document_data_cache(
    user_id: int,
    document_id: int,
    name: str,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    key = _doc_data_key(user_id, document_id, name)
    await set_json(key, value, ttl or CACHE_TTL.DEFAULT)

    client = await get_redis_client()
    if client:
        try:
            await client.sadd(_doc_data_index_key(user_id, document_id), key)
        except Exception:
            pass


async def invalidate_document_data(user_id: int, document_id: int, names: Optional[list[str]] = None) -> None:
    client = await get_redis_client()
    if client and not names:
        try:
            index_key = _doc_data_index_key(user_id, document_id)
            keys = await client.smembers(index_key)
            if keys:
                await client.delete(*keys)
            await client.delete(index_key)
        except Exception:
            pass

    if names:
        for name in names:
            key = _doc_data_key(user_id, document_id, name)
            await delete(key)
        if client:
            try:
                await client.srem(
                    _doc_data_index_key(user_id, document_id),
                    *[_doc_data_key(user_id, document_id, name) for name in names],
                )
            except Exception:
                pass


def _doc_content_key(user_id: int, document_id: int) -> str:
    return f"user:{user_id}:doc:{document_id}:content"


async def get_document_content_cache(user_id: int, document_id: int) -> Optional[Any]:
    key = _doc_content_key(user_id, document_id)
    return await get_json(key)


async def set_document_content_cache(
    user_id: int,
    document_id: int,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    key = _doc_content_key(user_id, document_id)
    await set_json(key, value, ttl or CACHE_TTL.DOCUMENT_CONTENT)


async def invalidate_document_content(user_id: int, document_id: int) -> None:
    key = _doc_content_key(user_id, document_id)
    await delete(key)


def _user_notes_key(
    user_id: int,
    document_id: Optional[int],
    note_type: Optional[str],
    limit: int,
    offset: int,
) -> str:
    doc_part = document_id if document_id is not None else "all"
    type_part = note_type if note_type is not None else "all"
    return f"user:{user_id}:notes:doc:{doc_part}:type:{type_part}:limit:{limit}:offset:{offset}"


def _user_notes_index_key(user_id: int) -> str:
    return f"user:{user_id}:notes:index"


async def get_user_notes_cache(
    user_id: int,
    document_id: Optional[int],
    note_type: Optional[str],
    limit: int,
    offset: int,
) -> Optional[Any]:
    key = _user_notes_key(user_id, document_id, note_type, limit, offset)
    return await get_json(key)


async def set_user_notes_cache(
    user_id: int,
    document_id: Optional[int],
    note_type: Optional[str],
    limit: int,
    offset: int,
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    key = _user_notes_key(user_id, document_id, note_type, limit, offset)
    await set_json(key, value, ttl or CACHE_TTL.USER_NOTES)

    client = await get_redis_client()
    if client:
        try:
            await client.sadd(_user_notes_index_key(user_id), key)
        except Exception:
            pass


async def invalidate_user_notes_cache(user_id: int) -> None:
    client = await get_redis_client()
    if not client:
        return

    try:
        index_key = _user_notes_index_key(user_id)
        keys = await client.smembers(index_key)
        if keys:
            await client.delete(*keys)
        await client.delete(index_key)
    except Exception:
        pass


# ==================== 文档列表缓存 ====================

def _document_list_key(user_id: int) -> str:
    return f"user:{user_id}:documents:list"


async def get_document_list_cache(user_id: int) -> Optional[Any]:
    """获取用户文档列表缓存"""
    key = _document_list_key(user_id)
    return await get_json(key)


async def set_document_list_cache(user_id: int, value: Any, ttl: Optional[int] = None) -> None:
    """设置用户文档列表缓存"""
    key = _document_list_key(user_id)
    await set_json(key, value, ttl or CACHE_TTL.DOCUMENT_LIST)


async def invalidate_document_list_cache(user_id: int) -> None:
    """失效用户文档列表缓存"""
    key = _document_list_key(user_id)
    await delete(key)


# ==================== 聊天历史缓存 ====================

def _chat_history_key(user_id: int, document_id: int, session_id: Optional[str]) -> str:
    session_part = session_id if session_id else "all"
    return f"user:{user_id}:doc:{document_id}:chat:{session_part}"


def _chat_history_index_key(user_id: int, document_id: int) -> str:
    return f"user:{user_id}:doc:{document_id}:chat:index"


async def get_chat_history_cache(
    user_id: int, 
    document_id: int, 
    session_id: Optional[str] = None
) -> Optional[Any]:
    """获取聊天历史缓存"""
    key = _chat_history_key(user_id, document_id, session_id)
    return await get_json(key)


async def set_chat_history_cache(
    user_id: int,
    document_id: int,
    session_id: Optional[str],
    value: Any,
    ttl: Optional[int] = None,
) -> None:
    """设置聊天历史缓存"""
    key = _chat_history_key(user_id, document_id, session_id)
    await set_json(key, value, ttl or CACHE_TTL.CHAT_HISTORY)

    client = await get_redis_client()
    if client:
        try:
            await client.sadd(_chat_history_index_key(user_id, document_id), key)
        except Exception:
            pass


async def invalidate_chat_history_cache(
    user_id: int, 
    document_id: int, 
    session_id: Optional[str] = None,
    clear_memory: bool = True,
) -> None:
    """失效聊天历史缓存
    
    Args:
        clear_memory: 是否同时清除 Memory 模块的缓存（摘要+消息）。
                      编辑/删除 操作传 True（默认），新增消息 传 False。
    """
    client = await get_redis_client()
    
    # 🧠 仅在编辑/删除场景清除 Memory 缓存（新消息不应销毁已有摘要）
    if clear_memory and client and session_id:
        try:
            summary_key = f"chat_summary:{user_id}:{document_id}:{session_id}"
            memory_key = f"chat_memory:{user_id}:{document_id}:{session_id}"
            await client.delete(summary_key)
            await client.delete(memory_key)
            logger.info(f"🧹 [Cache] Invalidated memory caches for session {session_id}")
        except Exception:
            pass
    
    if session_id:
        # 只失效指定 session
        key = _chat_history_key(user_id, document_id, session_id)
        await delete(key)
        if client:
            try:
                await client.srem(_chat_history_index_key(user_id, document_id), key)
            except Exception:
                pass
        
        # 🆕 同时失效全局历史（所有会话视图）
        # 否则在"全部历史"视图中看到的还是旧数据
        global_key = _chat_history_key(user_id, document_id, None)
        await delete(global_key)
        if client:
            try:
                await client.srem(_chat_history_index_key(user_id, document_id), global_key)
            except Exception:
                pass
    else:
        # 失效该文档所有聊天缓存
        if client:
            try:
                index_key = _chat_history_index_key(user_id, document_id)
                keys = await client.smembers(index_key)
                if keys:
                    await client.delete(*keys)
                await client.delete(index_key)
            except Exception:
                pass


# ==================== 缓存预热配置 ====================

class WarmupConfig:
    """缓存预热配置"""
    # 预热的最大文档数
    MAX_DOCUMENTS = 10
    
    # 每个文档预热的内容类型
    CONTENT_TYPES = [
        "chat_history",    # 聊天历史
        "mindmap",         # 思维导图
        "flashcards",      # 闪卡
        "highlights",      # 重点提炼
    ]
    
    # 优先级权重（基于活动类型）
    # 数值越高越优先预热
    PRIORITY_WEIGHTS = {
        "recent_access": 3,     # 最近访问
        "frequent_chat": 2,     # 聊天频繁
        "has_notes": 1,         # 有笔记
    }


WARMUP_CONFIG = WarmupConfig()


# ==================== 缓存预热 ====================

async def warmup_user_cache(user_id: int, _db=None) -> None:
    """
    用户登录后预热常用缓存（使用独立 session，可安全在后台任务中调用）
    
    动态预热策略：
    1. 基于 last_accessed_at 选择最近活跃的文档
    2. 预热多种内容类型（聊天、思维导图、闪卡等）
    3. 并行预热以提高效率
    
    Args:
        user_id: 用户ID
        _db: 忽略，为兼容性保留
    """
    import asyncio
    from sqlalchemy import select, func, desc
    from database import AsyncSessionLocal
    import db_models
    
    try:
        async with AsyncSessionLocal() as db:
            # 1. 预热文档列表
            result = await db.execute(
                select(db_models.Document).filter(
                    db_models.Document.user_id == user_id,
                    db_models.Document.processing_status == 'completed'  # 只预热已完成的
                ).order_by(db_models.Document.created_at.desc()).limit(50)
            )
            documents = result.scalars().all()
            
            doc_list = {
                "success": True,
                "data": [
                    {
                        "id": doc.id,
                        "filename": doc.filename,
                        "file_type": doc.file_type,
                        "chunk_count": doc.chunk_count,
                        "processing_status": doc.processing_status,
                        "processing_progress": doc.processing_progress,
                        "created_at": doc.created_at.isoformat() if doc.created_at else None
                    }
                    for doc in documents
                ]
            }
            await set_document_list_cache(user_id, doc_list)
            
            # 2. 选择需要深度预热的文档（基于活动优先级）
            priority_docs = await _get_priority_documents(
                db, user_id, documents, WARMUP_CONFIG.MAX_DOCUMENTS
            )
            
            # 3. 并行预热选中文档的各类内容
            warmup_tasks = []
            for doc in priority_docs:
                for content_type in WARMUP_CONFIG.CONTENT_TYPES:
                    warmup_tasks.append(
                        _warmup_document_content(db, user_id, doc.id, content_type)
                    )
            
            if warmup_tasks:
                await asyncio.gather(*warmup_tasks, return_exceptions=True)
            
            # 4. 预热用户笔记（不关联文档的笔记）
            await _warmup_user_notes(db, user_id)
            
            logger.info(f"🔥 用户 {user_id} 缓存预热完成（{len(documents)} 文档，深度预热 {len(priority_docs)} 个）")
        
    except Exception as e:
        logger.warning(f"⚠️ 缓存预热失败: {e}")


async def _get_priority_documents(db, user_id: int, documents: list, max_count: int) -> list:
    """
    基于活动优先级选择需要深度预热的文档
    
    优先级因素：
    1. 最近访问时间 (last_accessed_at)
    2. 聊天频率 (chat_history count)
    3. 是否有笔记
    """
    import db_models
    from sqlalchemy import select, func
    
    if not documents:
        return []
    
    doc_ids = [d.id for d in documents]
    
    # 获取每个文档的聊天次数
    chat_counts_result = await db.execute(
        select(
            db_models.DocumentChatHistory.document_id,
            func.count(db_models.DocumentChatHistory.id).label("count")
        ).filter(
            db_models.DocumentChatHistory.document_id.in_(doc_ids),
            db_models.DocumentChatHistory.user_id == user_id
        ).group_by(db_models.DocumentChatHistory.document_id)
    )
    chat_counts = dict(chat_counts_result.all())
    
    # 获取每个文档的笔记数
    note_counts_result = await db.execute(
        select(
            db_models.Note.document_id,
            func.count(db_models.Note.id).label("count")
        ).filter(
            db_models.Note.document_id.in_(doc_ids),
            db_models.Note.user_id == user_id
        ).group_by(db_models.Note.document_id)
    )
    note_counts = dict(note_counts_result.all())
    
    # 计算每个文档的优先级分数
    doc_scores = []
    now = None
    
    for doc in documents:
        score = 0
        
        # 最近访问加分（7天内递减）
        if hasattr(doc, 'last_accessed_at') and doc.last_accessed_at:
            from datetime import datetime, timezone
            if now is None:
                now = datetime.now(timezone.utc)
            days_ago = (now - doc.last_accessed_at).days
            if days_ago < 7:
                score += (7 - days_ago) * WARMUP_CONFIG.PRIORITY_WEIGHTS["recent_access"]
        
        # 聊天频率加分
        chat_count = chat_counts.get(doc.id, 0)
        if chat_count > 0:
            score += min(chat_count, 10) * WARMUP_CONFIG.PRIORITY_WEIGHTS["frequent_chat"]
        
        # 有笔记加分
        if note_counts.get(doc.id, 0) > 0:
            score += WARMUP_CONFIG.PRIORITY_WEIGHTS["has_notes"]
        
        doc_scores.append((doc, score))
    
    # 按分数排序，取前 N 个
    doc_scores.sort(key=lambda x: x[1], reverse=True)
    return [doc for doc, score in doc_scores[:max_count]]


async def _warmup_document_content(db, user_id: int, document_id: int, content_type: str) -> None:
    """预热单个文档的特定内容类型"""
    import db_models
    from sqlalchemy import select
    
    try:
        if content_type == "chat_history":
            result = await db.execute(
                select(db_models.DocumentChatHistory).filter(
                    db_models.DocumentChatHistory.document_id == document_id,
                    db_models.DocumentChatHistory.user_id == user_id
                ).order_by(db_models.DocumentChatHistory.created_at.asc()).limit(50)
            )
            history = result.scalars().all()
            
            if history:
                history_data = {
                    "success": True,
                    "data": [
                        {
                            "id": h.id,
                            "question": h.question,
                            "answer": h.answer,
                            "sources": h.relevant_chunks if h.relevant_chunks else [],
                            "search_count": h.search_count,
                            "elapsed_time": h.elapsed_time,
                            "status_history": h.status_history,  # 🆕 Add status_history to cache warmup
                            "flowchart": h.flowchart,
                            "chart_data": h.chart_data,
                            "plan": h.plan,  # 🆕 Add plan to cache warmup
                            "created_at": h.created_at.isoformat() if h.created_at else None
                        }
                        for h in history
                    ]
                }
                await set_chat_history_cache(user_id, document_id, None, history_data)
        
        elif content_type == "mindmap":
            result = await db.execute(
                select(db_models.DocumentMindmap).filter(
                    db_models.DocumentMindmap.document_id == document_id,
                    db_models.DocumentMindmap.user_id == user_id,
                    db_models.DocumentMindmap.is_current == True
                ).limit(1)
            )
            mindmap = result.scalar_one_or_none()
            
            if mindmap and mindmap.mindmap_data:
                await set_document_history_cache(
                    user_id, document_id, "mindmap",
                    {"success": True, "data": [{"id": mindmap.id, "mindmap_data": mindmap.mindmap_data}]}
                )
        
        elif content_type == "flashcards":
            result = await db.execute(
                select(db_models.DocumentFlashcards).filter(
                    db_models.DocumentFlashcards.document_id == document_id,
                    db_models.DocumentFlashcards.user_id == user_id,
                    db_models.DocumentFlashcards.is_current == True
                ).limit(1)
            )
            flashcards = result.scalar_one_or_none()
            
            if flashcards and flashcards.flashcards_data:
                await set_document_history_cache(
                    user_id, document_id, "flashcards",
                    {"success": True, "data": [{"id": flashcards.id, "flashcards_data": flashcards.flashcards_data}]}
                )
        
        elif content_type == "highlights":
            result = await db.execute(
                select(db_models.DocumentHighlights).filter(
                    db_models.DocumentHighlights.document_id == document_id,
                    db_models.DocumentHighlights.user_id == user_id,
                    db_models.DocumentHighlights.is_current == True
                ).limit(1)
            )
            highlights = result.scalar_one_or_none()
            
            if highlights and highlights.highlights_data:
                await set_document_history_cache(
                    user_id, document_id, "highlights",
                    {"success": True, "data": [{"id": highlights.id, "highlights_data": highlights.highlights_data}]}
                )
    
    except Exception as e:
        # 单个内容预热失败不影响整体
        pass


async def _warmup_user_notes(db, user_id: int) -> None:
    """预热用户笔记（前 50 条）"""
    import db_models
    from sqlalchemy import select
    
    try:
        result = await db.execute(
            select(db_models.Note).filter(
                db_models.Note.user_id == user_id
            ).order_by(db_models.Note.created_at.desc()).limit(50)
        )
        notes = result.scalars().all()
        
        if notes:
            notes_data = {
                "success": True,
                "data": [
                    {
                        "id": note.id,
                        "title": note.title,
                        "content": note.content,
                        "type": note.type,
                        "document_id": note.document_id,
                        "is_shared": note.is_shared,
                        "created_at": note.created_at.isoformat() if note.created_at else None,
                    }
                    for note in notes
                ],
            }
            await set_user_notes_cache(user_id, None, None, 50, 0, notes_data)
    
    except Exception:
        pass

