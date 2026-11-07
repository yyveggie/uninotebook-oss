"""
内容生成任务

包含：
- 闪卡生成
- 测验生成
- 思维导图生成
- 学习路径生成
"""
import os
import sys
import logging
from typing import Optional, Dict, Any, Callable

# 确保 backend 目录在 Python 路径中
_backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from celery_app import celery_app
from tasks.base import AsyncTask
from tasks.utils import get_db_session, invalidate_caches

logger = logging.getLogger(__name__)


# ============== 通用生成器基类 ==============

# ============== Celery 专用服务工厂 ==============
# 绕过单例模式，确保所有异步客户端在当前事件循环中创建

def _create_celery_document_service(db):
    """
    为 Celery 任务创建 DocumentService 实例
    
    关键点：
    1. 绕过 DocumentService 单例模式
    2. 禁用 LLM 客户端缓存
    3. 确保所有异步资源在当前事件循环中初始化
    """
    from config import settings
    from llm_service import get_llm_for_feature
    from document_service.core import DocumentProcessor
    from document_service.generator import ContentGenerator
    
    # 创建新的 LLM 客户端（禁用缓存）
    lc_client = get_llm_for_feature("doc_general", temperature=0.3, use_cache=False)
    report_client = get_llm_for_feature("report", temperature=0.3, use_cache=False)
    doc_parsing_client = get_llm_for_feature("doc_parsing", temperature=0.3, use_cache=False)
    
    # 创建子模块（不使用 DocumentService 单例）
    processor = DocumentProcessor(lc_client=doc_parsing_client, db=db)
    generator = ContentGenerator(lc_client=lc_client, db=db)
    report_generator = ContentGenerator(lc_client=report_client, db=db)
    
    # 返回一个简单的包装对象
    class CeleryDocumentService:
        def __init__(self):
            self.db = db
            self.processor = processor
            self.generator = generator
        
        async def get_document_by_id(self, document_id: int):
            return await self.processor.get_document_by_id(document_id)
        
        async def generate_flashcards_from_document(
            self,
            document,
            user_id=None,
            num_cards=None,
            difficulty=None,
            requirements=None,
            **kwargs,
        ):
            return await self.generator.generate_flashcards(
                document=document,
                num_cards=num_cards if num_cards is not None else 10,
                user_id=user_id,
                difficulty=difficulty,
                requirements=requirements,
            )
        
        async def generate_quiz_from_document(
            self,
            document,
            user_id=None,
            num_questions=None,
            difficulty=None,
            requirements=None,
            **kwargs,
        ):
            return await self.generator.generate_quiz(
                document=document,
                num_questions=num_questions if num_questions is not None else 10,
                user_id=user_id,
                difficulty=difficulty,
                requirements=requirements,
            )
        
        async def generate_mindmap_from_document(self, document, user_id=None):
            return await self.generator.generate_mindmap(document, user_id=user_id)
        
        async def generate_highlights_from_document(
            self,
            document,
            user_id=None,
            num_points=None,
            difficulty=None,
            requirements=None,
            **kwargs,
        ):
            return await self.generator.generate_highlights(
                document=document,
                user_id=user_id,
                num_points=num_points,
                difficulty=difficulty,
                requirements=requirements,
            )
        
        async def generate_learning_path(self, document, user_id=None):
            return await self.generator.generate_learning_path(document, user_id=user_id)

        async def generate_report_from_document(
            self,
            document,
            user_id=None,
            report_type=None,
            custom_style=None,
            focus_points=None,
            requirements=None,
            progress_callback=None,
            **kwargs,
        ):
            return await report_generator.generate_report(
                document=document,
                user_id=user_id,
                report_type=report_type,
                custom_style=custom_style,
                focus_points=focus_points,
                requirements=requirements,
                progress_callback=progress_callback,
            )
    
    # 新增：对话上下文生成方法（跳过检索，直接用对话文本）
    class _Service(CeleryDocumentService):
        async def generate_flashcards_from_context(self, context_text, title="对话", **kwargs):
            return await self.generator.generate_flashcards_from_context(context_text, title=title)
        
        async def generate_quiz_from_context(self, context_text, title="对话", **kwargs):
            return await self.generator.generate_quiz_from_context(context_text, title=title)
        
        async def generate_highlights_from_context(self, context_text, title="对话", **kwargs):
            return await self.generator.generate_highlights_from_context(context_text, title=title)
    
    return _Service()


class GenerationTaskMixin:
    """
    生成任务的通用逻辑混入类
    
    所有生成任务共享：
    - 获取文档
    - 调用服务生成内容
    - 保存到历史表
    - 失效缓存
    - 🆕 失败时自动退款
    """
    
    async def run_generation(
        self,
        document_id: int,
        user_id: int,
        generator_method: str,
        model_class,
        cache_name: str,
        title_field: str = "title",
        default_title: str = "生成内容",
        data_field: str = "data",
        count_field: Optional[str] = None,
        count_extractor: Optional[callable] = None,
        generator_kwargs: Optional[Dict[str, Any]] = None,
        data_transformer: Optional[Callable[[Any], Any]] = None,
        record_kwargs_builder: Optional[Callable[[Any], Dict[str, Any]]] = None,
        response_data_builder: Optional[Callable[[Any, Any], Any]] = None,
        credit_type: Optional[str] = None,  # 🆕 积分类型，用于退款
        task_id: Optional[str] = None,  # Celery 任务 ID（重试间保持不变），用作退款幂等键
        should_refund: Optional[Callable[[], bool]] = None,  # 失败时是否退款（仅最后一次尝试才退）
    ) -> Dict[str, Any]:
        """
        通用生成任务执行逻辑
        
        Args:
            document_id: 文档 ID
            user_id: 用户 ID
            generator_method: DocumentService 中的生成方法名
            model_class: 历史记录模型类
            cache_name: 缓存名称
            title_field: 模型中标题字段名
            default_title: 默认标题
            data_field: 模型中数据字段名
            count_field: 模型中计数字段名（可选）
            count_extractor: 从生成结果提取计数的函数（可选）
            generator_kwargs: 透传给生成器方法的可选参数
            data_transformer: 结果数据到 data_field 的转换函数（可选）
            record_kwargs_builder: 追加/覆盖落库字段构建函数（可选）
            response_data_builder: 自定义返回数据构建函数（可选）
            credit_type: 积分类型（如 flashcard, quiz），失败时用于退款
            task_id: 任务 ID，退款按任务幂等
            should_refund: 失败时调用，返回 True 才退款（自动重试期间不退款，避免一笔扣费退多次）
        """
        try:
            async with get_db_session() as db:
                from sqlalchemy import select, func, update
                import db_models
                
                # 🆕 使用 Celery 专用的服务工厂（绕过单例模式和缓存）
                service = _create_celery_document_service(db)
                
                # 1. 获取文档
                document = await service.get_document_by_id(document_id)
                if not document:
                    raise ValueError(f"Document {document_id} not found")
                
                # 2. 调用生成方法
                logger.info(f"🎯 [Celery] 开始生成 {cache_name}: document_id={document_id}")
                generator = getattr(service, generator_method)
                extra_kwargs = generator_kwargs or {}
                result = await generator(document=document, user_id=user_id, **extra_kwargs)
                
                # 3. 计算版本号
                version_query = await db.execute(
                    select(func.coalesce(func.max(model_class.version), 0)).filter(
                        model_class.document_id == document_id,
                        model_class.user_id == user_id
                    )
                )
                next_version = (version_query.scalar() or 0) + 1
                
                # 4. 准备数据
                if hasattr(result, "dict"):
                    result_data = result.dict()
                elif isinstance(result, list):
                    result_data = {cache_name: result}
                else:
                    result_data = result

                data_value = data_transformer(result_data) if data_transformer else result_data
                
                # 5. 创建记录
                record_kwargs = {
                    "document_id": document_id,
                    "user_id": user_id,
                    title_field: default_title,
                    data_field: data_value,
                    "version": next_version,
                    "is_current": True
                }

                if record_kwargs_builder:
                    extra_record_kwargs = record_kwargs_builder(result_data) or {}
                    record_kwargs.update(extra_record_kwargs)
                
                # 添加计数字段
                if count_field and count_extractor:
                    record_kwargs[count_field] = count_extractor(result_data)
                
                record = model_class(**record_kwargs)
                db.add(record)
                await db.flush()
                
                # 6. 将其他版本标记为非当前
                await db.execute(
                    update(model_class)
                    .where(
                        model_class.document_id == document_id,
                        model_class.user_id == user_id,
                        model_class.id != record.id
                    )
                    .values(is_current=False)
                )
                await db.commit()
                await db.refresh(record)
                
                # 7. 失效缓存
                await invalidate_caches(user_id, document_id, names=[cache_name])
                
                logger.info(f"✅ [Celery] {cache_name} 生成完成: record_id={record.id}")

                response_data = response_data_builder(result_data, record) if response_data_builder else result_data
                
                return {
                    "success": True,
                    "record_id": record.id,
                    "data": response_data
                }
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ [Celery] {cache_name} 生成失败: {error_msg}")

            # 🆕 退还积分：只在最后一次尝试失败时退（自动重试期间不退），且按任务幂等
            if credit_type and (should_refund is None or should_refund()):
                await self._refund_generation_credits(user_id, document_id, credit_type, error_msg, task_id=task_id)

            raise  # 重新抛出异常

    async def _refund_generation_credits(
        self,
        user_id: int,
        document_id: int,
        credit_type: str,
        error_msg: str,
        task_id: Optional[str] = None,
    ):
        """退还生成任务积分（同一任务只退一次）"""
        from tasks.utils import refund_task_credits_once
        from credit_service.constants import CreditCost

        # 映射积分类型到金额
        credit_amounts = {
            "flashcard": CreditCost.FLASHCARD,
            "quiz": CreditCost.QUIZ,
            "mindmap": CreditCost.MINDMAP,
            "highlights": CreditCost.HIGHLIGHTS,
            "report": CreditCost.REPORT,
            "interview": CreditCost.INTERVIEW,
        }
        
        amount = credit_amounts.get(credit_type)
        if not amount:
            logger.warning(f"⚠️ 未知的积分类型: {credit_type}，跳过退款")
            return

        await refund_task_credits_once(
            user_id=user_id,
            amount=amount,
            credit_type=credit_type,
            reference_id=f"task:{task_id}" if task_id else str(document_id),
            reason=f"{credit_type}生成失败: {error_msg[:100]}",
            dedupe=bool(task_id),
        )


# ============== 对话上下文生成的通用逻辑 ==============

async def _build_chat_context(document_id: int, user_id: int, session_id: str) -> str:
    """从数据库加载对话历史并构建文本上下文"""
    from sqlalchemy import select
    import db_models
    
    async with get_db_session() as db:
        result = await db.execute(
            select(db_models.DocumentChatHistory)
            .filter(
                db_models.DocumentChatHistory.document_id == document_id,
                db_models.DocumentChatHistory.user_id == user_id,
                db_models.DocumentChatHistory.session_id == session_id,
            )
            .order_by(db_models.DocumentChatHistory.created_at.asc())
        )
        records = result.scalars().all()
    
    if not records:
        raise ValueError("当前会话没有对话记录")
    
    parts = []
    for r in records:
        if r.answer in ["会话已创建，等待您的第一个问题...", "会话已创建。", ""]:
            continue
        if r.question:
            parts.append(f"用户: {r.question}")
        if r.answer:
            parts.append(f"AI: {r.answer}")
    
    context = "\n\n".join(parts)
    logger.info(f"📦 [Chat Context] Built from {len(records)} records, {len(context)} chars")
    return context


async def _run_chat_generation(
    document_id: int,
    user_id: int,
    session_id: str,
    gen_type: str,  # "flashcards" | "quiz" | "highlights"
):
    """对话生成通用逻辑"""
    from sqlalchemy import select, func, update
    import db_models
    
    # 类型配置
    config = {
        "flashcards": {
            "method": "generate_flashcards_from_context",
            "model_class": db_models.DocumentFlashcards,
            "title": "对话闪卡",
            "data_field": "flashcards_data",
            "count_field": "card_count",
            "count_fn": lambda d: len(d.get("flashcards", [])) if isinstance(d, dict) else 0,
        },
        "quiz": {
            "method": "generate_quiz_from_context",
            "model_class": db_models.DocumentQuiz,
            "title": "对话测验",
            "data_field": "quiz_data",
            "count_field": "question_count",
            "count_fn": lambda d: len(d.get("quiz", [])) if isinstance(d, dict) else len(d) if isinstance(d, list) else 0,
        },
        "highlights": {
            "method": "generate_highlights_from_context",
            "model_class": db_models.DocumentHighlights,
            "title": "对话提炼",
            "data_field": "highlights_data",
            "count_field": "point_count",
            "count_fn": lambda d: len(d.get("key_points", [])) if isinstance(d, dict) else 0,
        },
    }
    
    cfg = config[gen_type]
    model_class = cfg["model_class"]
    
    # 1. 构建对话上下文
    context_text = await _build_chat_context(document_id, user_id, session_id)
    
    # 2. 调用生成器
    async with get_db_session() as db:
        service = _create_celery_document_service(db)
        generator = getattr(service, cfg["method"])
        result = await generator(context_text=context_text, title=cfg["title"])
        
        # 3. 包装数据
        if isinstance(result, list):
            result_data = {gen_type: result}
        else:
            result_data = result
        
        # 4. 计算版本号
        version_query = await db.execute(
            select(func.coalesce(func.max(model_class.version), 0)).filter(
                model_class.document_id == document_id,
                model_class.user_id == user_id
            )
        )
        next_version = (version_query.scalar() or 0) + 1
        
        # 5. 创建记录
        record_kwargs = {
            "document_id": document_id,
            "user_id": user_id,
            "title": cfg["title"],
            cfg["data_field"]: result_data,
            "version": next_version,
            "is_current": True,
        }
        if cfg["count_field"]:
            record_kwargs[cfg["count_field"]] = cfg["count_fn"](result_data)
        
        record = model_class(**record_kwargs)
        db.add(record)
        await db.flush()
        
        # 6. 标记旧版本
        await db.execute(
            update(model_class)
            .where(model_class.document_id == document_id, model_class.user_id == user_id, model_class.id != record.id)
            .values(is_current=False)
        )
        await db.commit()
        await db.refresh(record)
        
        # 7. 失效缓存
        await invalidate_caches(user_id, document_id, names=[gen_type])
        
        logger.info(f"✅ [Celery] 对话{gen_type}生成完成: record_id={record.id}")
        return {"success": True, "record_id": record.id, "data": result_data}


# ============== 具体任务定义 ==============

@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.flashcards")
def generate_flashcards(
    self,
    document_id: int,
    user_id: int,
    card_count: Optional[int] = None,
    difficulty: Optional[str] = None,
    requirements: Optional[str] = None,
):
    """生成闪卡任务"""
    
    async def _run():
        import db_models
        mixin = GenerationTaskMixin()
        return await mixin.run_generation(
            document_id=document_id,
            user_id=user_id,
            generator_method="generate_flashcards_from_document",
            model_class=db_models.DocumentFlashcards,
            cache_name="flashcards",
            title_field="title",
            default_title="闪卡",
            data_field="flashcards_data",
            count_field="card_count",
            count_extractor=lambda d: len(d.get("flashcards", [])) if isinstance(d, dict) else 0,
            generator_kwargs={
                "num_cards": card_count,
                "difficulty": difficulty,
                "requirements": requirements,
            },
            credit_type="flashcard",  # 🆕 失败时退款
            task_id=self.request.id,
            should_refund=self.is_final_attempt,  # 仅最后一次尝试失败才退款
        )
    
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.quiz")
def generate_quiz(
    self,
    document_id: int,
    user_id: int,
    question_count: Optional[int] = None,
    difficulty: Optional[str] = None,
    requirements: Optional[str] = None,
):
    """生成测验任务"""
    
    async def _run():
        import db_models
        mixin = GenerationTaskMixin()
        return await mixin.run_generation(
            document_id=document_id,
            user_id=user_id,
            generator_method="generate_quiz_from_document",
            model_class=db_models.DocumentQuiz,
            cache_name="quiz",
            title_field="title",
            default_title="测验",
            data_field="quiz_data",
            count_field="question_count",
            count_extractor=lambda d: len(d.get("quiz", [])) if isinstance(d, dict) else len(d) if isinstance(d, list) else 0,
            generator_kwargs={
                "num_questions": question_count,
                "difficulty": difficulty,
                "requirements": requirements,
            },
            credit_type="quiz",  # 🆕 失败时退款
            task_id=self.request.id,
            should_refund=self.is_final_attempt,  # 仅最后一次尝试失败才退款
        )
    
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.mindmap")
def generate_mindmap(self, document_id: int, user_id: int):
    """生成思维导图任务"""
    
    def count_nodes(node: Dict[str, Any]) -> int:
        total = 1
        for child in node.get("children", []) or []:
            total += count_nodes(child)
        return total
    
    async def _run():
        import db_models
        mixin = GenerationTaskMixin()
        return await mixin.run_generation(
            document_id=document_id,
            user_id=user_id,
            generator_method="generate_mindmap_from_document",
            model_class=db_models.DocumentMindmap,
            cache_name="mindmap",
            title_field="title",
            default_title="思维导图",
            data_field="mindmap_data",
            count_field="node_count",
            count_extractor=lambda d: count_nodes(d.get("root", {})) if isinstance(d, dict) else 0,
            credit_type="mindmap",  # 🆕 失败时退款
            task_id=self.request.id,
            should_refund=self.is_final_attempt,  # 仅最后一次尝试失败才退款
        )
    
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.highlights")
def generate_highlights(
    self,
    document_id: int,
    user_id: int,
    point_count: Optional[int] = None,
    difficulty: Optional[str] = None,
    requirements: Optional[str] = None,
):
    """生成重点提炼任务"""
    
    async def _run():
        import db_models
        mixin = GenerationTaskMixin()
        return await mixin.run_generation(
            document_id=document_id,
            user_id=user_id,
            generator_method="generate_highlights_from_document",
            model_class=db_models.DocumentHighlights,
            cache_name="highlights",
            title_field="title",
            default_title="重点提炼",
            data_field="highlights_data",
            count_field="point_count",
            count_extractor=lambda d: len(d.get("key_points", [])) if isinstance(d, dict) else 0,
            generator_kwargs={
                "num_points": point_count,
                "difficulty": difficulty,
                "requirements": requirements,
            },
            credit_type="highlights",  # 🆕 失败时退款
            task_id=self.request.id,
            should_refund=self.is_final_attempt,  # 仅最后一次尝试失败才退款
        )
    
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.report")
def generate_report(
    self,
    document_id: int,
    user_id: int,
    report_type: Optional[str] = None,
    custom_style: Optional[str] = None,
    focus_points: Optional[str] = None,
    requirements: Optional[str] = None,
):
    """生成长报告任务"""

    async def _run():
        import db_models
        mixin = GenerationTaskMixin()

        self.update_state(state='PROGRESS', meta={
            "progress": 10,
            "stage": "loading_document",
        })
        self.update_state(state='PROGRESS', meta={
            "progress": 25,
            "stage": "planning_outline",
        })

        def _on_section_progress(current_section: int, total_sections: int):
            progress = min(90, 30 + int((current_section / max(total_sections, 1)) * 55))
            self.update_state(state='PROGRESS', meta={
                "progress": progress,
                "stage": "writing_sections",
                "current_section": current_section,
                "total_sections": total_sections,
            })

        return await mixin.run_generation(
            document_id=document_id,
            user_id=user_id,
            generator_method="generate_report_from_document",
            model_class=db_models.DocumentReport,
            cache_name="report",
            title_field="title",
            default_title="长报告",
            data_field="report_data",
            count_field="word_count",
            count_extractor=lambda d: int(d.get("word_count") or 0) if isinstance(d, dict) else 0,
            generator_kwargs={
                "report_type": report_type,
                "custom_style": custom_style,
                "focus_points": focus_points,
                "requirements": requirements,
                "progress_callback": _on_section_progress,
            },
            data_transformer=lambda d: d.get("report_data") if isinstance(d, dict) else None,
            record_kwargs_builder=lambda d: {
                "title": (d.get("title") if isinstance(d, dict) else None) or "长报告",
                "report_markdown": (d.get("report_markdown") if isinstance(d, dict) else None) or "",
            },
            response_data_builder=lambda _d, record: {
                "report_markdown": record.report_markdown,
                "title": record.title,
                "word_count": record.word_count,
            },
            credit_type="report",
            task_id=self.request.id,
            should_refund=self.is_final_attempt,  # 仅最后一次尝试失败才退款
        )

    return self.run_async(_run())


# ============== 对话上下文生成任务 ==============

# 对话生成与文档生成共用扣费类型（路由层 deduct_for_flashcard / quiz / highlights）
_CHAT_GENERATION_CREDIT_TYPES = {"flashcards": "flashcard", "quiz": "quiz", "highlights": "highlights"}


async def _run_chat_generation_with_refund(task, document_id: int, user_id: int, session_id: str, gen_type: str):
    """对话生成：失败时（仅最后一次尝试）退还路由层已扣的积分，按任务幂等"""
    try:
        return await _run_chat_generation(document_id, user_id, session_id, gen_type)
    except Exception as e:
        logger.error(f"❌ [Celery] 对话{gen_type}生成失败: {e}")
        if task.is_final_attempt():
            await GenerationTaskMixin()._refund_generation_credits(
                user_id, document_id, _CHAT_GENERATION_CREDIT_TYPES[gen_type], str(e), task_id=task.request.id
            )
        raise


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.chat_flashcards")
def generate_chat_flashcards(self, document_id: int, user_id: int, session_id: str):
    """对话闪卡生成任务"""
    async def _run():
        return await _run_chat_generation_with_refund(self, document_id, user_id, session_id, "flashcards")
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.chat_quiz")
def generate_chat_quiz(self, document_id: int, user_id: int, session_id: str):
    """对话测验生成任务"""
    async def _run():
        return await _run_chat_generation_with_refund(self, document_id, user_id, session_id, "quiz")
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.chat_highlights")
def generate_chat_highlights(self, document_id: int, user_id: int, session_id: str):
    """对话提炼生成任务"""
    async def _run():
        return await _run_chat_generation_with_refund(self, document_id, user_id, session_id, "highlights")
    return self.run_async(_run())


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.learning_path")
def generate_learning_path(self, document_id: int, user_id: int):
    """生成学习路径任务"""

    def count_nodes(node: Dict[str, Any]) -> int:
        total = 1
        for child in node.get("children", []) or []:
            total += count_nodes(child)
        return total
    
    async def _run():
        import db_models
        mixin = GenerationTaskMixin()
        return await mixin.run_generation(
            document_id=document_id,
            user_id=user_id,
            generator_method="generate_learning_path",
            model_class=db_models.DocumentLearningPath,
            cache_name="flowchart",
            title_field="title",
            default_title="学习路径",
            data_field="learning_path_data",
            count_field="node_count",
            count_extractor=lambda d: count_nodes(d.get("root", {})) if isinstance(d, dict) else 0,
            record_kwargs_builder=lambda d: {
                "title": (d.get("title") if isinstance(d, dict) else None) or "学习路径",
            },
            response_data_builder=lambda d, _record: d,
        )
    
    return self.run_async(_run())


# ============== 面试任务 ==============

@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.interview_start")
def start_interview_task(self, document_id: int, user_id: int):
    """初始化面试任务（异步）"""
    
    async def _run():
        try:
            async with get_db_session() as db:
                from document_service.generators import interview
                from document_service.generators.interview import initialize_interview_manager
                from document_service.vector_store import vector_store_manager
                from config import settings
                from llm_service import get_llm_for_feature
                
                logger.info(f"🎤 [Celery] 开始初始化面试: document_id={document_id}")
                
                if interview.interview_manager is None:
                    logger.info("🔧 [Celery] 初始化面试管理器...")
                    from runtime.credentials import credential_store
                    openai_key = credential_store.api_key("openai") or settings.openai_api_key
                    openai_url = credential_store.base_url("openai") or settings.openai_base_url or "https://api.openai.com/v1"
                    initialize_interview_manager(
                        api_key=openai_key,
                        base_url=openai_url,
                        model=settings.openai_model
                    )
                
                # 🆕 使用 Celery 专用的服务工厂
                service = _create_celery_document_service(db)
                
                # 获取文档
                document = await service.get_document_by_id(document_id)
                if not document:
                    raise ValueError(f"Document {document_id} not found")
                
                # 初始化面试
                session_id, first_question_data = await interview.interview_manager.initialize_interview(
                    document,
                    vector_store_manager,
                    db=db
                )
                
                # 失效缓存
                await invalidate_caches(user_id, document_id, names=["interview"])
                
                logger.info(f"✅ [Celery] 面试初始化完成: session_id={session_id}")
                
                return {
                    "success": True,
                    "session_id": session_id,
                    "first_question": first_question_data
                }
                
        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ [Celery] 面试初始化失败: {error_msg}")
            
            # 🆕 退还积分：只在最后一次尝试失败时退（自动重试期间不退），且按任务幂等
            if self.is_final_attempt():
                await _refund_interview_credits(user_id, document_id, error_msg, task_id=self.request.id)
            
            raise  # 重新抛出异常
    
    return self.run_async(_run())


async def _refund_interview_credits(user_id: int, document_id: int, error_msg: str, task_id: Optional[str] = None):
    """面试任务失败时退还积分（同一任务只退一次）"""
    from tasks.utils import refund_task_credits_once
    from credit_service.constants import CreditCost
    
    await refund_task_credits_once(
        user_id=user_id,
        amount=CreditCost.INTERVIEW,
        credit_type="interview",
        reference_id=f"task:{task_id}" if task_id else str(document_id),
        reason=f"面试初始化失败: {error_msg[:100]}",
        dedupe=bool(task_id),
    )


# ============== 知识网络任务（灵感生成 / 整理建议 / 知识间隙）通用 ==============

class _KnowledgeTaskError(Exception):
    """可以原样展示给用户的失败原因（例如节点不足）"""


class _NoGapAnalysisNeeded(Exception):
    """知识间隙：图谱里没有可分析的社区对，不必调用模型"""

    def __init__(self, analysis):
        super().__init__(analysis.message)
        self.analysis = analysis


def _public_task_error(exc: BaseException, fallback: str) -> str:
    """给用户看的失败原因：线上不带服务商原始报错，本地版保留以便排查"""
    from config import settings
    from runtime.features import looks_like_missing_credentials, missing_credential_hint

    if isinstance(exc, _KnowledgeTaskError):
        return str(exc)
    if looks_like_missing_credentials(exc):
        return missing_credential_hint()
    if settings.is_local_mode:
        return f"{fallback}（{type(exc).__name__}: {str(exc)[:200]}）"
    return fallback


async def _refund_knowledge_task(task, user_id: int, charged_credits, credit_type: str, default_amount: int, reason: str) -> bool:
    """知识网络任务失败时退还路由层扣的积分（同一任务只退一次）。

    charged_credits 由路由层传入实际扣费额；旧版本提交的任务没有这个参数，按定价退。
    """
    from tasks.utils import refund_task_credits_once

    amount = default_amount if charged_credits is None else charged_credits
    if not amount or amount <= 0:
        return False
    task_id = getattr(task.request, "id", None)
    return await refund_task_credits_once(
        user_id=user_id,
        amount=amount,
        credit_type=credit_type,
        reference_id=f"task:{task_id}" if task_id else f"user:{user_id}",
        reason=reason[:100],
        dedupe=bool(task_id),
    )


async def _run_knowledge_task(task, coro, *, user_id: int, charged_credits, credit_type: str, default_amount: int, fallback: str, extra: dict):
    """执行任务主体；失败时退款，返回 success=False 和给用户看的原因（不抛出：自动重试会让用户等很久，且可能重复退款）"""
    try:
        return await coro
    except _NoGapAnalysisNeeded:
        raise
    except Exception as exc:
        if not isinstance(exc, _KnowledgeTaskError):
            logger.exception(f"❌ [Celery] {credit_type} 任务失败: user_id={user_id}")
        reason = _public_task_error(exc, fallback)
    refunded = await _refund_knowledge_task(task, user_id, charged_credits, credit_type, default_amount, reason)
    return {"success": False, "message": reason + ("（本次积分已退还）" if refunded else ""), **extra}


def _as_text_list(value, limit: int, max_chars: int) -> list:
    if isinstance(value, str):
        import re
        value = [part for part in re.split(r"[,，、;；\n]", value)]
    if not isinstance(value, list):
        return []
    items = [str(item).strip()[:max_chars] for item in value if str(item).strip()]
    return items[:limit]


# ============== 灵感生成任务 ==============

_EUREKA_TYPE_PROMPTS = {
    "product": "请生成一个**创新产品或服务构想**。",
    "theory": "请生成一个**可验证的理论假设**。",
    "perspective": "请生成一个**独特的观点或视角**。",
    "paper": "请生成一个**学术论文研究方向**。",
    "metaphor": "请生成一个**深刻的隐喻或类比**。",
    "auto": "请根据这些概念的特性，自动选择最合适的创新形式。",
}


async def _generate_eureka(user_id: int, target_size: int, node_ids: Optional[list], innovation_type: str) -> Dict[str, Any]:
    import random
    from collections import defaultdict
    from langchain_core.messages import SystemMessage, HumanMessage
    from sqlalchemy import select
    from document_service.utils import parse_json_safely
    from knowledge_graph.models import KnowledgeTerm, DocumentKnowledgeTerm, EurekaMoment
    from llm_service import get_llm_for_feature

    target_size = max(2, min(6, int(target_size or 3)))
    dt = DocumentKnowledgeTerm

    async with get_db_session() as db:
        logger.info(f"💡 [Celery] 开始生成灵感: user_id={user_id}")

        used_combos = set()
        for (src_nodes,) in (await db.execute(
            select(EurekaMoment.source_nodes).where(EurekaMoment.user_id == user_id)
        )).all():
            if isinstance(src_nodes, list):
                combo = frozenset(n.get("id") for n in src_nodes if isinstance(n, dict) and n.get("id"))
                if combo:
                    used_combos.add(combo)

        # 只使用仍被文档引用的节点：已删除文档留下的概念不应再出现在灵感里
        term_docs = defaultdict(set)
        for term_id, doc_id in (await db.execute(
            select(dt.term_id, dt.document_id).where(dt.user_id == user_id)
        )).all():
            term_docs[term_id].add(doc_id)
        linked_terms = select(dt.term_id).where(dt.user_id == user_id)

        # 1. 模式一：用户指定了节点
        if node_ids:
            wanted = [term_id for term_id in node_ids if isinstance(term_id, int) and term_id in term_docs][:6]
            selected_terms = list((await db.execute(
                select(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id, KnowledgeTerm.id.in_(wanted))
            )).scalars().all()) if wanted else []
            if len(selected_terms) < 2:
                raise _KnowledgeTaskError("选中的节点已不存在，请刷新后重试")

        # 2. 模式二：随机寻找互不连接的节点
        else:
            all_terms = list((await db.execute(
                select(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id, KnowledgeTerm.id.in_(linked_terms))
            )).scalars().all())
            if len(all_terms) < 2:
                raise _KnowledgeTaskError("知识网络里的概念太少，请先上传并解析更多文档")

            # 每个概念与多少个其他概念共现：越孤立越容易被选中
            doc_terms = defaultdict(set)
            for term_id, doc_ids in term_docs.items():
                for doc_id in doc_ids:
                    doc_terms[doc_id].add(term_id)
            neighbours = defaultdict(set)
            for members in doc_terms.values():
                for term_id in members:
                    neighbours[term_id] |= members
            connectivity = {term_id: len(members) - 1 for term_id, members in neighbours.items()}

            weights = [1.0 / (1 + connectivity.get(t.id, 0)) for t in all_terms]
            pool_size = min(len(all_terms), 30)
            candidate_pool = list(set(random.choices(all_terms, weights=weights, k=pool_size * 2)))[:pool_size]
            random.shuffle(candidate_pool)

            selected_terms = []
            for term in candidate_pool:
                if len(selected_terms) >= target_size:
                    break
                current_docs = term_docs.get(term.id, set())
                if all(current_docs.isdisjoint(term_docs.get(s.id, set())) for s in selected_terms):
                    selected_terms.append(term)

            if len(selected_terms) < 2:
                remaining = [t for t in candidate_pool if t not in selected_terms]
                selected_terms.extend(remaining[:target_size - len(selected_terms)])
            selected_terms = selected_terms[:target_size]

            combo_key = frozenset(t.id for t in selected_terms)
            if combo_key in used_combos and len(candidate_pool) > target_size:
                for alt in (t for t in candidate_pool if t not in selected_terms):
                    if frozenset([alt.id] + [t.id for t in selected_terms[1:]]) not in used_combos:
                        selected_terms[0] = alt
                        break

    # 3. 调用 AI 生成创意（不占用数据库连接）
    selected_labels = [t.display_term or t.term for t in selected_terms]
    selected_nodes_info = [
        {"id": t.id, "label": t.display_term or t.term, "description": t.description}
        for t in selected_terms
    ]
    type_instruction = _EUREKA_TYPE_PROMPTS.get(innovation_type, _EUREKA_TYPE_PROMPTS["auto"])
    system_prompt = f"""你是一位极具洞察力的跨学科思想家。你的核心信念是"创新源于连接"。

【你的任务】
用户提供了几个知识图谱中的概念节点。你需要：
1. 深入理解每个概念
2. 寻找真正的连接点
3. 生成高质量的创新想法：{type_instruction}

请严格按照以下 JSON 格式返回结果：
{{
  "idea": "一句话标题，精炼概括创新点（15-30字）",
  "reasoning": "详细阐述：各概念的核心特征；它们之间的连接逻辑；由此产生的创新想法及其价值。（200-400字）"
}}

请只返回 JSON，不要包含 Markdown 代码块或其他文本。"""

    # 🆕 使用统一配置（禁用缓存以避免事件循环冲突）
    llm = get_llm_for_feature("eureka", use_cache=False)
    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"请为以下概念建立创新连接：{', '.join(selected_labels)}"),
    ])
    data = parse_json_safely(response.content)
    idea = str((data or {}).get("idea") or "").strip() if isinstance(data, dict) else ""
    reasoning = str((data or {}).get("reasoning") or "").strip() if isinstance(data, dict) else ""
    if not idea or not reasoning:
        raise ValueError("模型返回的灵感缺少标题或内容")

    logger.info(f"✅ [Celery] 灵感生成完成: user_id={user_id}")
    return {
        "success": True,
        "data": {"idea": idea, "reasoning": reasoning, "nodes": selected_nodes_info},
    }


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.eureka")
def generate_eureka_task(self, user_id: int, target_size: int = 3, node_ids: list = None, innovation_type: str = "auto", charged_credits: Optional[int] = None):
    """生成灵感任务（异步）。失败时退还积分，并返回 success=False 与失败原因。"""
    from credit_service import CreditCost

    return self.run_async(_run_knowledge_task(
        self,
        _generate_eureka(user_id, target_size, node_ids, innovation_type),
        user_id=user_id,
        charged_credits=charged_credits,
        credit_type="ai_eureka",
        default_amount=CreditCost.AI_EUREKA,
        fallback="AI 灵感生成失败，请稍后重试",
        extra={},
    ))


# ============== 节点合并建议任务 ==============

_MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "integer"},
                    "target_id": {"type": "integer"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"}
                },
                "required": ["source_id", "target_id", "reason", "confidence"]
            }
        }
    },
    "required": ["suggestions"]
}


async def _embed_term_labels(labels: list) -> list:
    from document_service.vector_store import vector_store_manager
    return await vector_store_manager._generate_embeddings_batch(labels)


async def _generate_merge_suggestions(user_id: int) -> Dict[str, Any]:
    import json
    from langchain_core.messages import SystemMessage, HumanMessage
    from sqlalchemy import select
    from document_service.utils import parse_json_safely
    from knowledge_graph.models import KnowledgeTerm
    from knowledge_graph.merge_candidates import plan_batches, sanitize_suggestions
    from knowledge_graph.merging import auto_merge_terms
    from knowledge_graph.utils import format_display_term, invalidate_kg_cache
    from llm_service import get_llm_for_feature

    async with get_db_session() as db:
        logger.info(f"🧬 [Celery] 开始分析合并建议: user_id={user_id}")
        # 0. 先按归一化规则自动合并明显重复的节点（不需要模型）
        try:
            stats = await auto_merge_terms(db, user_id)
            if stats["merged_nodes"] or stats["renamed_nodes"]:
                await invalidate_kg_cache(user_id)
        except Exception as e:
            await db.rollback()
            logger.warning(f"⚠️ [Celery] 自动合并预处理失败: {e}")

        terms = list((await db.execute(
            select(KnowledgeTerm).where(KnowledgeTerm.user_id == user_id)
        )).scalars().all())

    labels = {t.id: format_display_term(t.display_term or t.term) or (t.display_term or t.term) for t in terms}
    if len(labels) < 2:
        return {"success": True, "suggestions": []}

    # 1. 分批：概念不多时整表一次分析；很多时按向量相似度把可能重复的概念放进同一批
    batches = await plan_batches(list(labels.items()), embed=_embed_term_labels)

    llm = get_llm_for_feature("merge_analysis", temperature=0.1, max_tokens=4000, use_cache=False)
    system_prompt = (
        "你是一个知识图谱维护专家。你的任务是分析用户提供的术语节点列表，找出语义重复或极其相似、应该合并的节点对。\n"
        "请找出以下几类情况：\n"
        "1. 中英文对照差异（如 'AI / 人工智能' 和 '人工智能'）\n"
        "2. 单复数差异（如 'Algorithm' 和 'Algorithms'）\n"
        "3. 简繁体差异或别名（如 'KNN' 和 'K-Nearest Neighbors'）\n"
        "4. 明显的同义词\n\n"
        "⚠️ 重要规则：\n"
        "1. **只返回确定需要合并的项**。如果两个节点概念不同、或者是包含关系但无需合并，**绝对不要**包含在结果中。\n"
        "2. 不要返回“不建议合并”的分析结果，直接忽略即可。\n"
        "3. 每个节点最多作为 source 出现一次；不要同时给出 A→B 和 B→A。\n\n"
        "请严格按照以下 JSON 格式返回结果：\n"
        f"{json.dumps(_MERGE_SCHEMA, indent=2, ensure_ascii=False)}\n\n"
        "请只返回 JSON，不要包含 Markdown 代码块标记或其他文本。如果没有发现需要合并的项，返回 {\"suggestions\": []}。"
    )

    raw_suggestions, last_error = [], None
    for batch in batches:
        term_list_str = "\n".join(f"ID: {term_id}, Term: {label}" for term_id, label in batch)
        try:
            response = await llm.ainvoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"以下是节点列表（共 {len(batch)} 个）：\n{term_list_str}"),
            ])
            data = parse_json_safely(response.content.strip())
            if isinstance(data, dict) and isinstance(data.get("suggestions"), list):
                raw_suggestions.extend(data["suggestions"])
        except Exception as exc:
            last_error = exc
            logger.warning(f"⚠️ [Celery] 合并分析的一批失败: {type(exc).__name__}: {exc}")
    if batches and last_error is not None and not raw_suggestions:
        # 所有批次都失败：按失败处理（退款）
        raise last_error

    suggestions = sanitize_suggestions(raw_suggestions, labels)
    logger.info(f"✅ [Celery] 合并分析完成：{len(batches)} 批，找到 {len(suggestions)} 条建议")
    return {"success": True, "suggestions": suggestions}


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.merge_suggestions")
def generate_merge_suggestions_task(self, user_id: int, charged_credits: Optional[int] = None):
    """生成节点合并建议任务（异步）。失败时退还积分。"""
    from credit_service import CreditCost

    return self.run_async(_run_knowledge_task(
        self,
        _generate_merge_suggestions(user_id),
        user_id=user_id,
        charged_credits=charged_credits,
        credit_type="ai_organize",
        default_amount=CreditCost.AI_ORGANIZE,
        fallback="AI 整理分析失败，请稍后重试",
        extra={"suggestions": []},
    ))


# ============== 知识间隙分析任务 ==============

_GAPS_SYSTEM_PROMPT = """你是一个知识图谱分析专家。用户的知识图谱已按概念共现关系自动划分为若干知识社区。

请分析这些社区之间的"知识间隙"，即：
1. 每个社区的主题是什么？
2. 哪些社区之间联系薄弱、但存在潜在的跨学科连接？（优先考虑联系强度低的社区对）
3. 用户可以通过学习哪些"桥梁概念"来弥合间隙？

请用 JSON 格式返回，包含一个 gaps 数组，每个元素：
{
  "community_a": 社区编号(int，必须是给出的社区 id),
  "community_b": 社区编号(int，必须是给出的社区 id),
  "theme_a": "社区A的主题概括",
  "theme_b": "社区B的主题概括",
  "bridge_concepts": ["建议学习的桥梁概念1", "桥梁概念2"],
  "explanation": "一段话解释这两个社区之间的潜在联系以及为什么值得弥合",
  "suggested_topics": ["建议搜索/阅读的文档主题1", "主题2"]
}

最多返回 5 个最有价值的间隙。只返回 JSON，不要其他内容。"""


def _clean_gaps(result_data, community_count: int) -> list:
    """模型输出可能缺字段、编号越界或把列表写成字符串：清洗后前端才能稳定渲染"""
    raw = result_data.get("gaps") if isinstance(result_data, dict) else result_data
    cleaned = []
    for gap in raw if isinstance(raw, list) else []:
        if not isinstance(gap, dict):
            continue
        try:
            a, b = int(gap.get("community_a")), int(gap.get("community_b"))
        except (TypeError, ValueError):
            continue
        if a == b or not (0 <= a < community_count and 0 <= b < community_count):
            continue
        cleaned.append({
            "community_a": a,
            "community_b": b,
            "theme_a": str(gap.get("theme_a") or "").strip()[:100],
            "theme_b": str(gap.get("theme_b") or "").strip()[:100],
            "bridge_concepts": _as_text_list(gap.get("bridge_concepts"), 6, 60),
            "explanation": str(gap.get("explanation") or "").strip()[:1000],
            "suggested_topics": _as_text_list(gap.get("suggested_topics"), 6, 80),
        })
    return cleaned[:5]


async def _analyze_knowledge_gaps(user_id: int, communities: Optional[list], pairs: Optional[list]) -> Dict[str, Any]:
    import json
    from langchain_core.messages import SystemMessage, HumanMessage
    from document_service.utils import parse_json_safely
    from llm_service import get_llm_for_feature

    if communities is None:
        # 旧版本提交的任务只带 user_id：在这里做社区划分
        from knowledge_graph.communities import analyze_term_communities
        async with get_db_session() as db:
            analysis = await analyze_term_communities(db, user_id)
        if not analysis.needs_ai:
            raise _NoGapAnalysisNeeded(analysis)
        communities, pairs = analysis.communities, analysis.pairs

    logger.info(f"🔍 [Celery] 开始分析知识间隙: user_id={user_id}, communities={len(communities)}")
    llm = get_llm_for_feature("merge_analysis", temperature=0.3, max_tokens=3000, use_cache=False)
    response = await llm.ainvoke([
        SystemMessage(content=_GAPS_SYSTEM_PROMPT),
        HumanMessage(content=(
            f"以下是用户知识图谱中的 {len(communities)} 个知识社区：\n\n"
            f"{json.dumps(communities, ensure_ascii=False, indent=2)}\n\n"
            "社区之间的联系强度（links 为跨社区共现次数，strength 越小联系越弱）：\n\n"
            f"{json.dumps(pairs or [], ensure_ascii=False, indent=2)}"
        )),
    ])
    gaps = _clean_gaps(parse_json_safely(response.content), len(communities))
    result = {"success": True, "gaps": gaps, "communities": communities}
    if not gaps:
        result["message"] = "AI 没有在这些知识社区之间找到值得弥合的间隙"
    return result


@celery_app.task(bind=True, base=AsyncTask, name="tasks.generation.knowledge_gaps")
def analyze_knowledge_gaps_task(self, user_id: int, communities: Optional[list] = None, pairs: Optional[list] = None, charged_credits: Optional[int] = None):
    """分析知识图谱中的知识间隙。失败时退还积分。"""
    from credit_service import CreditCost

    async def _run():
        try:
            return await _run_knowledge_task(
                self,
                _analyze_knowledge_gaps(user_id, communities, pairs),
                user_id=user_id,
                charged_credits=charged_credits,
                credit_type="ai_organize",
                default_amount=CreditCost.AI_ORGANIZE,
                fallback="AI 间隙分析失败，请稍后重试",
                extra={"gaps": [], "communities": communities or []},
            )
        except _NoGapAnalysisNeeded as skipped:
            # 没有可分析的间隙、也没调用模型：退还积分，按正常结果返回
            await _refund_knowledge_task(self, user_id, charged_credits, "ai_organize", CreditCost.AI_ORGANIZE, skipped.analysis.message)
            return {"success": True, "gaps": [], "communities": skipped.analysis.communities, "message": skipped.analysis.message}

    return self.run_async(_run())
