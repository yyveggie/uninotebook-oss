"""
笔记管理路由

包含: 笔记CRUD、灵感笔记(Eureka Notes)CRUD
"""
from fastapi import APIRouter, HTTPException, Depends, Body
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from database import get_db
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode
from cache_service import (
    get_user_notes_cache,
    invalidate_user_notes_cache,
)
from utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


# ==================== 笔记管理 ====================


@router.post("/api/notes", tags=["笔记管理"])
async def create_note(
    title: str = Body(...),
    content: str = Body(...),
    document_id: int = Body(None),
    type: str = Body("general"),
    source_note_id: int = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    创建笔记
    """
    try:
        logger.info(
            "📝 收到创建笔记请求: title=%s, type=%s, document_id=%s, source_note_id=%s, content_length=%s",
            title,
            type,
            document_id,
            source_note_id,
            len(content),
        )
        
        # 如果是从共享笔记复制，检查是否已经复制过
        if source_note_id:
            result = await db.execute(
                select(db_models.Note).filter(
                    db_models.Note.user_id == current_user.id,
                    db_models.Note.source_note_id == source_note_id
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                logger.warning("⚠️ 用户已复制过此笔记: %s", existing.id)
                raise_http_error(status_code=400, code=ErrorCode.NOTES_400_MESSAGE, message="您已经复制过这条笔记了")
        
        note = db_models.Note(
            user_id=current_user.id,
            document_id=document_id,
            title=title,
            content=content,
            type=type,
            source_note_id=source_note_id
        )
        db.add(note)
        await db.commit()
        await db.refresh(note)

        logger.info("✅ 笔记已保存到数据库: id=%s, title=%s", note.id, note.title)
        
        response_data = {
            "success": True,
            "data": {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "type": note.type,
                "document_id": note.document_id,
                "created_at": note.created_at.isoformat()
            }
        }

        logger.info("📤 返回给前端的数据: title=%s", response_data["data"]["title"])
        
        await invalidate_user_notes_cache(current_user.id)

        return response_data
    except HTTPException:
        # 重新抛出 HTTPException（如已复制过等业务错误）
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 创建笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="创建失败", details=str(e))


@router.get("/api/notes", tags=["笔记管理"])
async def get_notes(
    document_id: int = None,
    no_document: bool = False,  # 查询没有关联文档的笔记
    type: str = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取笔记列表"""
    try:
        # 先查缓存（仅当不是 no_document 查询时）
        if not no_document:
            cached = await get_user_notes_cache(
                current_user.id,
                document_id,
                type,
                limit,
                offset,
            )
            if cached:
                return cached

        # 构建查询
        query_stmt = select(db_models.Note).filter(db_models.Note.user_id == current_user.id)

        # 添加过滤条件
        if no_document:
            # 查询没有关联文档的笔记
            query_stmt = query_stmt.filter(db_models.Note.document_id.is_(None))
        elif document_id:
            query_stmt = query_stmt.filter(db_models.Note.document_id == document_id)
        if type:
            query_stmt = query_stmt.filter(db_models.Note.type == type)

        # 排序并分页
        query_stmt = query_stmt.order_by(db_models.Note.created_at.desc()).offset(offset).limit(limit)

        result = await db.execute(query_stmt)
        notes = result.scalars().all()

        response_payload = {
            "success": True,
            "data": [
                {
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                    "type": note.type,
                    "document_id": note.document_id,
                    "is_shared": getattr(note, "is_shared", False),
                    "shared_at": note.shared_at.isoformat() if getattr(note, "shared_at", None) else None,
                    "created_at": note.created_at.isoformat(),
                    "updated_at": note.updated_at.isoformat() if note.updated_at else None,
                }
                for note in notes
            ],
        }

        # ⚠️ 关键：真正返回给前端
        return response_payload
    except Exception as e:
        logger.exception("❌ 获取笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="获取失败", details=str(e))


@router.get("/api/notes/{note_id}", tags=["笔记管理"])
async def get_note_by_id(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    获取单个笔记详情
    """
    try:
        # 查询笔记
        result = await db.execute(
            select(db_models.Note).filter(
            db_models.Note.id == note_id,
            db_models.Note.user_id == current_user.id
        
            )
        )
        note = result.scalar_one_or_none()
        
        if not note:
            raise_http_error(status_code=404, code=ErrorCode.NOTES_404_MESSAGE, message="笔记不存在")
        
        return {
            "success": True,
            "data": {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "type": note.type,
                "document_id": note.document_id,
                "is_shared": note.is_shared if hasattr(note, 'is_shared') else False,
                "shared_at": note.shared_at.isoformat() if hasattr(note, 'shared_at') and note.shared_at else None,
                "created_at": note.created_at.isoformat() if note.created_at else None,
                "updated_at": note.updated_at.isoformat() if note.updated_at else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("❌ 获取笔记详情失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="获取失败", details=str(e))


@router.put("/api/notes/{note_id}", tags=["笔记管理"])
async def update_note(
    note_id: int,
    title: str = Body(None),
    content: str = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    更新笔记
    """
    try:
        result = await db.execute(
            select(db_models.Note).filter(
            db_models.Note.id == note_id,
            db_models.Note.user_id == current_user.id
        
            )
        )
        note = result.scalar_one_or_none()
        
        if not note:
            raise_http_error(status_code=404, code=ErrorCode.NOTES_404_MESSAGE, message="笔记不存在")
        
        # 更新字段
        if title is not None:
            note.title = title
        if content is not None:
            note.content = content
            
        await db.commit()
        await db.refresh(note)
        
        response_payload = {
            "success": True,
            "data": {
                "id": note.id,
                "title": note.title,
                "content": note.content,
                "type": note.type,
                "document_id": note.document_id,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat() if note.updated_at else None
            }
        }
        await invalidate_user_notes_cache(current_user.id)

        return response_payload
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 更新笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="更新失败", details=str(e))


@router.delete("/api/notes/{note_id}", tags=["笔记管理"])
async def delete_note(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """
    删除笔记（优化版）
    """
    try:
        # 直接使用 DELETE 语句，更高效
        from sqlalchemy import delete
        
        result = await db.execute(
            delete(db_models.Note).filter(
            db_models.Note.id == note_id,
            db_models.Note.user_id == current_user.id
            )
        )
        
        if result.rowcount == 0:
            raise_http_error(status_code=404, code=ErrorCode.NOTES_404_MESSAGE, message="笔记不存在或无权删除")
            
        await db.commit()
        logger.info("✅ 笔记删除成功: id=%s, affected_rows=%s", note_id, result.rowcount)
        
        await invalidate_user_notes_cache(current_user.id)

        return {"success": True, "message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 删除笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="删除失败", details=str(e))


# ==================== 灵感笔记 (Eureka Notes) ====================


@router.get("/api/eureka/{eureka_id}/notes", tags=["灵感笔记"])
async def get_eureka_notes(
    eureka_id: int,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """获取指定灵感的笔记列表"""
    try:
        # 构建查询
        query_stmt = select(db_models.EurekaNote).filter(
            db_models.EurekaNote.eureka_id == eureka_id,
            db_models.EurekaNote.user_id == current_user.id
        )

        # 排序并分页
        query_stmt = query_stmt.order_by(db_models.EurekaNote.created_at.desc()).offset(offset).limit(limit)

        result = await db.execute(query_stmt)
        notes = result.scalars().all()

        return {
            "success": True,
            "data": [
                {
                    "id": note.id,
                    "eureka_id": note.eureka_id,
                    "title": note.title,
                    "content": note.content,
                    "created_at": note.created_at.isoformat(),
                    "updated_at": note.updated_at.isoformat() if note.updated_at else None,
                }
                for note in notes
            ],
        }
    except Exception as e:
        logger.exception("❌ 获取灵感笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="获取失败", details=str(e))


@router.post("/api/eureka/{eureka_id}/notes", tags=["灵感笔记"])
async def create_eureka_note(
    eureka_id: int,
    title: str = Body(..., embed=True),
    content: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """创建灵感笔记"""
    try:
        new_note = db_models.EurekaNote(
            eureka_id=eureka_id,
            user_id=current_user.id,
            title=title,
            content=content
        )
        db.add(new_note)
        await db.commit()
        await db.refresh(new_note)

        return {
            "success": True,
            "data": {
                "id": new_note.id,
                "eureka_id": new_note.eureka_id,
                "title": new_note.title,
                "content": new_note.content,
                "created_at": new_note.created_at.isoformat(),
                "updated_at": new_note.updated_at.isoformat() if new_note.updated_at else None,
            }
        }
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 创建灵感笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="创建失败", details=str(e))


@router.put("/api/eureka/notes/{note_id}", tags=["灵感笔记"])
async def update_eureka_note(
    note_id: int,
    title: str = Body(None),
    content: str = Body(None),
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """更新灵感笔记"""
    try:
        result = await db.execute(
            select(db_models.EurekaNote).filter(
                db_models.EurekaNote.id == note_id,
                db_models.EurekaNote.user_id == current_user.id
            )
        )
        note = result.scalar_one_or_none()
        
        if not note:
            raise_http_error(status_code=404, code=ErrorCode.NOTES_404_MESSAGE, message="笔记不存在")
        
        if title is not None:
            note.title = title
        if content is not None:
            note.content = content
            
        await db.commit()
        await db.refresh(note)
        
        return {
            "success": True,
            "data": {
                "id": note.id,
                "eureka_id": note.eureka_id,
                "title": note.title,
                "content": note.content,
                "created_at": note.created_at.isoformat(),
                "updated_at": note.updated_at.isoformat() if note.updated_at else None
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 更新灵感笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="更新失败", details=str(e))


@router.delete("/api/eureka/notes/{note_id}", tags=["灵感笔记"])
async def delete_eureka_note(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user)
):
    """删除灵感笔记"""
    try:
        result = await db.execute(
            select(db_models.EurekaNote).filter(
                db_models.EurekaNote.id == note_id,
                db_models.EurekaNote.user_id == current_user.id
            )
        )
        note = result.scalar_one_or_none()
        
        if not note:
            raise_http_error(status_code=404, code=ErrorCode.NOTES_404_MESSAGE, message="笔记不存在")
        
        await db.delete(note)
        await db.commit()
        
        return {"success": True, "detail": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        logger.exception("❌ 删除灵感笔记失败: %s", e)
        raise_http_error(status_code=500, code=ErrorCode.NOTES_500_EXCEPTION, message="删除失败", details=str(e))
