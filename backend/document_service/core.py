"""
文档处理核心功能
包含文档上传、解析、摘要生成等基础功能
"""
import asyncio
import os
import hashlib
from typing import Optional, List, Dict, Any
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

# Unstructured 文档处理
# LangChain
from langchain_openai import ChatOpenAI

# 内部模块
from .schema import DocumentSummaryResponse, DocumentFullSummaryResponse
from .utils import calculate_file_hash, clean_text_newlines
from db_models import Document
from document_service.vector_store import vector_store_manager
from .parsers import get_document_parser
from utils.logger import get_logger
from knowledge_graph.service import (
    update_knowledge_graph_for_document,
    cleanup_orphan_terms_for_user,
)

logger = get_logger(__name__)

SHORT_TEXT_VECTOR_THRESHOLD = 4000  # 字符数，低于该值的纯文本跳过向量化

# 文档摘要的时限：单次请求超时与总时限（含重试）。超时即使用兜底摘要，不阻塞文档可用
SUMMARY_REQUEST_TIMEOUT_SECONDS = 45
SUMMARY_TIMEOUT_SECONDS = 60


def _delete_cos_objects(keys: List[str], prefixes: List[str]) -> None:
    """删除文档在 COS 上的对象（同步调用，需放在线程中执行）；COS 未配置时为空操作"""
    from utils.cos_service import COSService

    cos = COSService()
    if not getattr(cos, "client", None):
        return
    targets = list(keys)
    for prefix in prefixes:
        targets.extend(cos.list_files_with_prefix(prefix, max_keys=1000))
    for key in dict.fromkeys(targets):
        cos.delete_file(key)


def format_summary_markdown(summary_response: DocumentSummaryResponse) -> str:
    """
    将结构化摘要转换为 Markdown 格式
    
    Args:
        summary_response: 结构化摘要响应
        
    Returns:
        markdown_text: Markdown 格式的摘要
    """
    lines = []
    
    # 核心内容概述
    lines.append("## 📋 核心内容")
    lines.append("")
    lines.append(summary_response.overview)
    lines.append("")
    
    # 关键术语列表
    lines.append("## 🔑 关键术语")
    lines.append("")
    for term in summary_response.key_terms:
        # 术语名称用加粗 + 行内代码
        term_name = term.term
        # 如果包含斜杠，说明是中英文对照，分别处理
        if ' / ' in term_name:
            parts = term_name.split(' / ')
            formatted_term = f"**{parts[0]}** / `{parts[1]}`"
        else:
            formatted_term = f"`{term_name}`"
        
        lines.append(f"- {formatted_term}")
        lines.append(f"  {term.description}")
        lines.append("")
    
    return "\n".join(lines)


class DocumentProcessor:
    """文档处理器 - 负责文档解析、标题提取、摘要生成等核心功能"""
    
    def __init__(self, lc_client: ChatOpenAI, db: Optional[AsyncSession] = None, parser=None):
        """
        初始化文档处理器
        
        Args:
            lc_client: LangChain ChatOpenAI 客户端
            db: 异步数据库会话（可选）
        """
        self._lc_client = lc_client
        self.db = db
        self.parser = parser or get_document_parser()

    @property
    def lc_client(self):
        if self._lc_client is not None:
            return self._lc_client
        from llm_service import get_llm_for_feature

        return get_llm_for_feature("doc_parsing", temperature=0.3)
    
    async def extract_structured_elements_from_file(self, file_content: bytes, filename: str) -> List[Dict[str, Any]]:
        """
        从文档提取结构化元素（使用 Unstructured，支持多种格式）
        
        ⚡ 异步优化：使用线程池执行阻塞的文档解析，不阻塞事件循环
        
        支持的格式：PDF, DOCX, DOC, TXT, MD, HTML, EPUB, PPTX 等
        
        Args:
            file_content: 文件二进制内容
            filename: 文件名（用于识别文件类型）
            
        Returns:
            elements: 结构化元素列表，每个元素包含 text, category, metadata
        """
        elements = await self.parser.parse(file_content, filename)
        self._log_parse_summary(filename, elements)
        
        # 🆕 如果 Unstructured 解析失败（返回空列表），对 PDF 走纯文本提取兜底
        if not elements and filename.lower().endswith('.pdf'):
            logger.warning("⚠️ Unstructured 解析失败，尝试纯文本提取兜底...")
            elements = await self._extract_pdf_text(file_content, filename)
            self._log_parse_summary(filename, elements)

        return elements

    async def _extract_pdf_text(self, file_content: bytes, filename: str) -> List[Dict[str, Any]]:
        """
        PDF 纯文本提取兜底方案

        后端可插拔（PyMuPDF / pypdf），选择逻辑见 parsers/pdf_text.py。
        """
        from starlette.concurrency import run_in_threadpool

        from document_service.parsers.pdf_text import extract_text_elements

        return await run_in_threadpool(extract_text_elements, file_content)

    def _log_parse_summary(self, filename: str, elements: List[Dict[str, Any]]):
        try:
            category_counts: Dict[str, int] = {}
            total_chars = 0
            for elem in elements:
                category = elem.get('category', 'Unknown')
                category_counts[category] = category_counts.get(category, 0) + 1
                total_chars += len(str(elem.get('text') or ''))
            
            logger.info(f"\n{'='*60}")
            logger.info(f"📄 解析文档完成: {filename}")
            logger.info(f"   总元素数: {len(elements)}")
            logger.info(f"   总字符数: {total_chars}")
            logger.info(f"📊 元素类型分布:")
            for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
                logger.info(f"   {cat:20s}: {count:3d} 个")
            logger.info(f"{'='*60}\n")
        except Exception as exc:  # pragma: no cover - 仅日志
            logger.warning(f"⚠️ 打印解析统计失败: {exc}")

    async def _generate_full_summary_response(
        self,
        content: str,
        filename: str,
    ) -> DocumentFullSummaryResponse:
        """生成文档摘要 + 关键术语的结构化响应（不再提取标题）。"""
        logger.info("🤖 正在生成文档摘要（结构化提取）...")

        preview_content = content[:3000]
        content_length = len(content)

        # 摘要失败有兜底文案，不值得为它长时间等待：通用客户端是 300s 超时 × 3 次重试，
        # 一次卡住的请求就能拖满整个任务时限。这里用专门的短超时客户端，并设总时限。
        from llm_service import get_llm_for_feature

        llm = self._lc_client or get_llm_for_feature(
            "doc_parsing",
            temperature=0.3,
            request_timeout=SUMMARY_REQUEST_TIMEOUT_SECONDS,
            max_retries=1,
        )
        structured_llm = llm.with_structured_output(
            DocumentFullSummaryResponse,
            method="function_calling",
        )

        prompt = f"""请分析这份文档，提取【核心内容概述】和【关键术语】。

1. 核心内容概述（overview，200-300字）：
- 文档类型和主题
- 解决什么问题/讨论什么内容
- 主要方法/观点/结论
- 决不允许编造

2. 关键术语列表（key_terms，5-12个）：
- 专业概念、技术名称、方法名称
- 算法名称、模型名称
- 重要的术语（必须包含中英文）
- 核心指标/公式名称
- 创新的方法/观点/结论
- 术语命名格式请严格遵守以下规范（例子）：
  - 有缩写时：LLM / Large Language Model / 大型语言模型
  - 无缩写时：Text mining / 文本挖掘

⚠️ 重要约束：
- 【仅提取文档中实际出现的术语】，不要推测、联想或补充文档未明确提及的技术/方法
- 如果文档明确说明"未使用"或"不包含"某个技术，绝对不要将其作为关键词
- 每个术语至少包含英文和中文，中间用"/"分隔，格式参考上面的示例
- 英文缩写（如有）全部大写，英文全称使用首字母大写
- 术语说明要具体、准确，20-50字
- 概述要全面但简洁
- 避免抽象概括，使用具体表述

文档信息：
- 文件名：{filename}
- 长度：{content_length} 字符

文档内容（前3000字）：
{preview_content}
"""

        messages = [{"role": "user", "content": prompt}]
        full = await asyncio.wait_for(structured_llm.ainvoke(messages), timeout=SUMMARY_TIMEOUT_SECONDS)
        if full is None:
            # 模型用纯文本作答、没有调用结构化工具时 LangChain 返回 None
            # （部分 OpenAI 兼容端点 / 本地模型会这样）。按失败处理，由调用方走兜底摘要
            raise ValueError("模型未返回结构化摘要")
        return full

    async def _generate_document_summary_response(self, content: str, filename: str) -> DocumentSummaryResponse:
        """兼容旧接口：从 full summary 中派生摘要响应。"""
        logger.info(f"🤖 正在生成文档摘要（结构化提取）...")
        try:
            full = await self._generate_full_summary_response(content, filename)
            return DocumentSummaryResponse(
                overview=full.overview,
                key_terms=full.key_terms,
            )
        except Exception as e:
            logger.exception(f"⚠️ 摘要生成失败（结构化）: filename={filename}, err={e}")
            raise

    async def generate_document_summary(self, content: str, filename: str) -> str:
        """对外公开的摘要生成接口（保持原有签名）。

        仍然返回 Markdown 文本，但内部复用结构化响应函数，
        便于知识图谱更新逻辑在其它地方复用。
        """
        try:
            summary_response = await self._generate_document_summary_response(content, filename)
            markdown_summary = self._format_summary_as_markdown(summary_response)

            logger.info(f"✅ 摘要生成成功")
            logger.info(f"   概述长度: {len(summary_response.overview)} 字符")
            logger.info(f"   关键术语: {len(summary_response.key_terms)} 个")
            logger.info(f"   Markdown长度: {len(markdown_summary)} 字符")

            return markdown_summary

        except Exception:
            # 向后兼容：失败时返回一个通用占位摘要
            return f"这是一份关于「{filename}」的文档。你可以向我提问任何相关问题！"
    
    def _format_summary_as_markdown(self, summary_response: DocumentSummaryResponse) -> str:
        return format_summary_markdown(summary_response)
    
    async def create_document_record(
        self,
        filename: str,
        content: str,
        file_type: str,
        user_id: int,
        file_hash: Optional[str]
    ) -> Document:
        """
        创建文档记录（立即返回，不进行向量化）
        
        Args:
            filename: 文件名
            content: 文本内容
            file_type: 文件类型 ('pdf' or 'text')
            user_id: 用户ID（必填）
            file_hash: 文件哈希
            
        Returns:
            document: 文档对象（状态为 pending）
        """
        try:
            # 清理文本内容
            content = await run_in_threadpool(clean_text_newlines, content)
            
            logger.info(f"\n{'='*60}")
            logger.info(f"📄 创建文档记录: {filename}")
            logger.info(f"{'='*60}")
            hash_display = f"{file_hash[:16]}..." if file_hash else "无"
            logger.info(f"   文件哈希: {hash_display}")
            
            # 创建临时向量集合ID（真正的向量化将在后台完成）
            temp_doc_id = hashlib.md5(f"{filename}{datetime.utcnow()}".encode()).hexdigest()[:8]
            collection_id = f"doc_temp_{temp_doc_id}"
            
            # ⚙️ 新文件，将在后台处理...
            extracted_title = None
            summary = None
            processing_status = 'pending'  # 待处理
            processing_progress = 0
            existing_doc_with_same_hash = None
            
            # 创建文档记录
            document = Document(
                user_id=user_id,
                filename=filename,
                file_type=file_type,
                original_text=content,
                vector_store_id=collection_id,
                file_size=len(content.encode('utf-8')),
                file_hash=file_hash,
                extracted_title=extracted_title,
                summary=summary,
                processing_status=processing_status,
                processing_progress=processing_progress,
                chunk_count=existing_doc_with_same_hash.chunk_count if existing_doc_with_same_hash else 0
            )
            
            if self.db:
                self.db.add(document)
                await self.db.commit()
                await self.db.refresh(document)
                logger.info(f"✅ 文档记录已创建，ID: {document.id}, 状态: {processing_status}")
            
            return document
            
        except Exception as e:
            if self.db:
                await self.db.rollback()
            logger.error(f"❌ 创建文档记录失败: {e}")
            raise
    
    async def process_document_async(
        self,
        document_id: int,
        structured_elements: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        异步处理文档（向量化+生成摘要）- 在后台任务中执行
        
        🚀 优化：摘要生成和向量化并行执行，大幅缩短处理时间
        
        Args:
            document_id: 文档ID
            structured_elements: 可选的结构化元素（Unstructured 输出）
        """
        import asyncio

        document = None
        # 本次尝试新建的向量集合：失败时清理，避免任务重试每次都遗留一个孤儿集合
        created_collections: List[str] = []
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"⚡ 开始后台处理文档: ID={document_id}")
            logger.info(f"{'='*60}")

            # 1. 获取文档
            document = await self.get_document_by_id(document_id)
            if not document:
                raise Exception(f"文档不存在: {document_id}")
            # 重投递 / 重试时文档可能已有上一次建好的集合，新集合提交后需回收旧集合
            previous_collection_id = document.vector_store_id or ""

            # 2. 更新状态为 processing
            document.processing_status = 'processing'
            document.processing_progress = 10
            await self.db.commit()
            logger.info(f"✅ 状态已更新为 processing")
            
            # 3. 检查是否可以复用向量
            if document.processing_progress == 100:
                logger.info(f"✅ 文档已处理完成（复用向量），跳过")
                return
            
            # 判断是否需要向量化
            # 只有"纯文本 + 内容很短"才跳过向量化
            # 即使没有结构化元素，只要 original_text 不为空，也要做向量化（用纯文本切块）
            is_plain_text = (document.file_type or '').lower() == 'text'
            original_text_len = len(document.original_text or '')
            is_short_text = original_text_len <= SHORT_TEXT_VECTOR_THRESHOLD
            has_content = bool((document.original_text or '').strip())
            skip_vectorization = (is_plain_text and is_short_text) or (not has_content)
            
            # 调试日志
            logger.info(f"🔍 向量化判断:")
            logger.info(f"   file_type={document.file_type}, is_plain_text={is_plain_text}")
            logger.info(f"   original_text长度={original_text_len}, is_short_text={is_short_text}")
            logger.info(f"   has_content={has_content}")
            logger.info(f"   structured_elements数量={len(structured_elements) if structured_elements else 0}")
            logger.info(f"   skip_vectorization={skip_vectorization}")
            
            # 🚀 并行任务定义
            async def task_generate_summary():
                """任务1: 生成标题+摘要"""
                logger.info(f"📝 [并行] 开始生成标题和摘要...")
                try:
                    full_response = await self._generate_full_summary_response(
                        document.original_text,
                        document.filename,
                    )
                    return full_response
                except Exception as summary_err:
                    logger.exception(
                        f"⚠️ 摘要生成失败，使用兜底摘要继续处理: "
                        f"doc_id={document.id}, filename={document.filename}, err={summary_err}"
                    )
                    fallback_overview = (
                        f"这是一份关于《{os.path.splitext(document.filename)[0]}》的文档。"
                        "摘要暂时生成失败，你可以继续在对话区提问，我会基于原文内容为你解答。"
                    )
                    return DocumentFullSummaryResponse(
                        overview=fallback_overview,
                        key_terms=[],
                    )
            
            async def task_vectorize():
                """任务2: 向量化文档"""
                if skip_vectorization:
                    return None, 0
                
                logger.info(f"🔄 [并行] 开始向量化...")
                collection_id = await vector_store_manager._run_in_executor(
                    vector_store_manager.create_collection, document.id
                )
                created_collections.append(collection_id)
                chunk_count = await vector_store_manager.add_documents(
                    collection_id=collection_id,
                    text=document.original_text,
                    metadata={
                        "filename": document.filename,
                        "document_id": str(document.id),
                        "source_id": f"document:{document.id}",
                        "source_name": document.display_name or document.filename,
                        "source_type": document.file_type or "document",
                    },
                    structured_elements=structured_elements
                )
                return collection_id, chunk_count
            
            # 🚀 摘要与向量化并行；文档以「向量化完成」为可用标志，摘要稍后补写。
            # 摘要是一次远程模型调用，耗时不可控（排队、限流、超时重试），不应让上传页一直等它。
            # 摘要任务在本协程返回前必定被 await 或取消，不会像游离的 create_task 那样
            # 在 Celery 的 run_until_complete 结束时被事件循环丢弃。
            logger.info(f"🚀 启动并行处理（摘要 + 向量化）...")
            summary_future = asyncio.ensure_future(task_generate_summary())
            try:
                collection_id, chunk_count = await task_vectorize()
            except BaseException:
                summary_future.cancel()
                await asyncio.gather(summary_future, return_exceptions=True)
                raise

            def as_summary_response(full):
                return DocumentSummaryResponse(overview=full.overview, key_terms=full.key_terms)

            # 4. 标题直接用文件名（不再让 LLM 提取）
            document.extracted_title = os.path.splitext(document.filename)[0]

            # 5. 处理向量化结果
            if skip_vectorization:
                reason = "纯文本且较短" if is_plain_text and is_short_text else "内容为空"
                logger.info(f"📝 {reason}，跳过向量化步骤")
                document.vector_store_id = f"text_direct_{document.id}"
                document.chunk_count = 0
            else:
                document.vector_store_id = collection_id
                document.chunk_count = chunk_count
                logger.info(f"✅ 向量化完成: {chunk_count} 个切块")

            # 6. 标记可用。摘要若已就绪就一并写入；否则先留空，补写后前端会刷新
            summary_response = None
            if summary_future.done():
                summary_response = as_summary_response(summary_future.result())
                document.summary = self._format_summary_as_markdown(summary_response)
                logger.info(f"✅ 摘要生成完成")
            else:
                document.summary = None
            document.processing_status = 'completed'
            document.processing_progress = 100
            await self.db.commit()
            created_collections.clear()  # 已提交为文档的正式集合，不再属于“待清理”
            await self._invalidate_status_cache(document)

            # 6.1 回收被替换掉的旧集合（重投递 / 重试场景），仅限本文档自己的且无其他引用
            if (
                previous_collection_id.startswith(f"doc_{document.id}_")
                and previous_collection_id != document.vector_store_id
            ):
                await self._delete_collection_if_unreferenced(previous_collection_id)

            # 6.2 补写摘要（有总时限，见 SUMMARY_TIMEOUT_SECONDS）。文档已可用，
            # 补写失败只记日志，不能把已完成的文档改判为失败
            if summary_response is None:
                logger.info("⏳ 文档已可用，摘要仍在生成，完成后补写")
                try:
                    summary_response = as_summary_response(await summary_future)
                    document.summary = self._format_summary_as_markdown(summary_response)
                    await self.db.commit()
                    await self._invalidate_status_cache(document)
                    logger.info(f"✅ 摘要生成完成（已补写）")
                except Exception as summary_err:
                    logger.warning(f"⚠️ 摘要补写失败（文档已可用）: doc_id={document.id}, err={summary_err}")
                    await self.db.rollback()
                    summary_response = None

            # 7. 🧠 异步更新知识图谱（独立 session，失败不影响主流程）
            # 🚀 [2026-02-25] 使用独立 db session，避免与主流程 session 竞争
            # ⚠️ 注意：不能用 asyncio.create_task()，因为 Celery 的 run_until_complete
            #    在主协程完成后会停止事件循环，create_task 的后台任务会被丢弃
            if summary_response and summary_response.key_terms:
                try:
                    from tasks.utils import get_db_session
                    from db_models import Document as DocModel
                    async with get_db_session() as kg_db:
                        kg_doc = await kg_db.get(DocModel, document.id)
                        if kg_doc:
                            await update_knowledge_graph_for_document(
                                kg_db, kg_doc, summary_response.key_terms
                            )
                            logger.info(f"✅ 知识图谱已更新（文档ID={document.id}）")
                except Exception as kg_err:
                    logger.warning(f"⚠️ 更新知识图谱失败（文档ID={document.id}）: {kg_err}")
            
            logger.info(f"✅ 文档处理完成！")
            logger.info(f"   文档ID: {document.id}")
            logger.info(f"   切块数: {document.chunk_count}")
            logger.info(f"   向量集合: {document.vector_store_id}")
            logger.info(f"{'='*60}\n")
            
        except Exception as e:
            logger.exception(f"❌ 文档处理失败: doc_id={document_id}, err={e}")
            
            # 🔥 处理失败：标记为 failed（不再直接删除）
            if document:
                logger.error(f"❌ 标记文档为失败状态: ID={document.id}")
                try:
                    document.processing_status = 'failed'
                    document.processing_error = str(e)[:500]
                    await self.db.commit()
                except Exception as update_err:
                    logger.warning(f"   ⚠️ 更新失败状态时出错: {update_err}")
                    await self.db.rollback()

            # 清理本次尝试已创建的向量集合（保留文档记录，但清理垃圾数据），
            # 否则 Celery 自动重试每次都会遗留一个孤儿集合
            for collection_id in created_collections:
                try:
                    await vector_store_manager._run_in_executor(vector_store_manager.delete_collection, collection_id)
                    logger.info(f"   已清理向量集合: {collection_id}")
                except Exception:
                    pass
            
            # 🔧 重新抛出异常，让 Celery TaskStatusUpdater 正确标记为 failed
            # （之前吞掉异常会导致 TaskStatusUpdater.__aexit__ 将状态覆写为 completed）
            raise
    
    async def process_document(
        self,
        filename: str,
        content: str,
        file_type: str,
        user_id: int,
        structured_elements: Optional[List[Dict[str, Any]]] = None
    ) -> Document:
        """
        处理文档（保存+向量化+生成摘要）- 同步版本（向后兼容）
        
        ⚠️ 此方法会阻塞，推荐使用 create_document_record + process_document_async
        
        Args:
            filename: 文件名
            content: 文本内容
            file_type: 文件类型 ('pdf' or 'text')
            user_id: 用户ID（必填）
            structured_elements: 可选的结构化元素（Unstructured 输出）
            
        Returns:
            document: 文档对象
        """
        # 向后兼容的同步版本：直接创建并完成处理
        try:
            # 清理文本内容
            content = await run_in_threadpool(clean_text_newlines, content)
            
            # 计算文件哈希
            file_hash = await run_in_threadpool(calculate_file_hash, content)
            
            # 创建文档记录
            document = await self.create_document_record(
                filename=filename,
                content=content,
                file_type=file_type,
                user_id=user_id,
                file_hash=file_hash
            )
            
            # 如果不是复用向量的文档，同步处理
            if document.processing_status != 'completed':
                await self.process_document_async(
                    document_id=document.id,
                    structured_elements=structured_elements
                )
            
            # 刷新文档对象以获取最新状态
            if self.db:
                await self.db.refresh(document)
            
            return document
            
        except Exception as e:
            if self.db:
                await self.db.rollback()
            logger.error(f"❌ 文档处理失败: {e}")
            raise
    
    async def get_document_by_id(self, document_id: int, user_id: Optional[int] = None) -> Optional[Document]:
        """获取文档"""
        if not self.db:
            return None
        query = select(Document).filter(Document.id == document_id)
        if user_id is not None:
            query = query.filter(Document.user_id == user_id)
        result = await self.db.execute(query)
        return result.scalar_one_or_none()
    
    async def list_documents(self, user_id: int, limit: int = 50) -> List[Document]:
        """列出指定用户的文档（user_id 必填，不允许不带过滤地列出全部用户的文档）"""
        if user_id is None:
            raise ValueError("list_documents 必须指定 user_id")
        if not self.db:
            return []

        query_stmt = (
            select(Document)
            .filter(Document.user_id == user_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(query_stmt)
        return result.scalars().all()
    
    async def _invalidate_status_cache(self, document) -> None:
        """状态接口会缓存终态 30 秒；状态或摘要变化后立刻失效，前端下一次轮询即可看到"""
        try:
            from cache_service import invalidate_document_data

            await invalidate_document_data(document.user_id, document.id, names=["status"])
        except Exception as cache_err:
            logger.warning(f"⚠️ 失效文档状态缓存失败: {cache_err}")

    async def _delete_collection_if_unreferenced(self, collection_id: Optional[str]) -> bool:
        """删除不再被任何文档 / 来源引用的向量集合（集合可能被重复文档共享）。失败只记日志。"""
        if not collection_id or not self.db:
            return False
        from db_models import DocumentSource

        try:
            doc_ref = await self.db.execute(
                select(Document.id).filter(Document.vector_store_id == collection_id).limit(1)
            )
            if doc_ref.first() is not None:
                return False
            source_ref = await self.db.execute(
                select(DocumentSource.id).filter(DocumentSource.vector_store_id == collection_id).limit(1)
            )
            if source_ref.first() is not None:
                return False
            await vector_store_manager._run_in_executor(vector_store_manager.delete_collection, collection_id)
            return True
        except Exception as exc:
            logger.warning(f"⚠️ 清理向量集合失败: {collection_id} ({exc})")
            return False

    async def _refresh_knowledge_graph_after_delete(self, user_id: int, term_ids: set) -> None:
        """删除文档后：重算原关联节点的文档数、删掉不再被引用的节点，并清掉图谱缓存。

        不做的话，删掉的文档里的概念会继续出现在知识网络、灵感生成和 AI 整理里，
        线上还会被图谱缓存再保留 10 分钟。
        """
        try:
            from knowledge_graph.utils import invalidate_kg_cache, refresh_terms

            if term_ids:
                await refresh_terms(self.db, user_id, term_ids)
                await self.db.commit()
            await invalidate_kg_cache(user_id)
        except Exception as kg_err:
            await self.db.rollback()
            logger.warning(f"⚠️ 删除文档后同步知识网络失败: user_id={user_id}, err={kg_err}")

    async def delete_document(self, document_id: int, user_id: Optional[int] = None):
        """
        删除文档及其所有关联数据
        
        级联删除内容：
        1. 向量数据库 collection（释放磁盘空间）
        2. 聊天历史记录
        3. 学习路径记录
        4. 闪卡记录
        5. 测验记录
        6. 相关笔记
        
        Args:
            document_id: 文档ID
        """
        if not self.db:
            return
        
        document = await self.get_document_by_id(document_id, user_id=user_id)
        if not document:
            logger.warning(f"⚠️ 文档不存在或无权删除: {document_id}")
            raise ValueError("document_not_found")
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🗑️ 准备删除文档: {document.filename} (ID: {document_id})")
        logger.info(f"{'='*60}")
        logger.info(f"   - 向量数据: Collection ID = {document.vector_store_id}")
        
        try:
            # 1. 检查向量集合的引用计数（是否有其他文档共享）
            logger.info(f"\n🔍 检查向量集合引用计数...")
            result = await self.db.execute(
                select(Document).filter(
                    Document.vector_store_id == document.vector_store_id,
                    Document.id != document.id
                )
            )
            other_docs_sharing_vector = len(result.scalars().all())
            
            if other_docs_sharing_vector > 0:
                logger.warning(f"   ⚠️ 还有 {other_docs_sharing_vector} 个文档共享此向量集合，保留向量数据")
                should_delete_vector = False
            else:
                logger.info(f"   ✅ 没有其他文档共享，可以删除向量数据")
                should_delete_vector = True

            # 1.1 记录附加来源（DocumentSource 由数据库级联删除，但其向量集合 / 本地全文需要单独清理）
            from db_models import DocumentSource
            source_rows = (await self.db.execute(
                select(DocumentSource.id, DocumentSource.vector_store_id).filter(
                    DocumentSource.document_id == document.id
                )
            )).all()
            owner_id = document.user_id

            # 1.2 服务端部署：记录 COS 上的原文备份与生成产物（相关行会被级联删除，需事先取出定位信息）
            from config import settings
            cos_keys, cos_prefixes = [], []
            if not settings.is_local_mode:
                from db_models import DocumentPPT, DocumentPoster, DocumentPodcast

                if getattr(document, "original_file_cos_key", None):
                    cos_keys.append(document.original_file_cos_key)
                podcast_ids = (await self.db.execute(
                    select(DocumentPodcast.id).filter(DocumentPodcast.document_id == document.id)
                )).scalars().all()
                cos_keys.extend(f"podcasts/podcast_{podcast_id}.mp3" for podcast_id in podcast_ids)
                # PPT / 海报的 session_id 为每个任务独立的 uuid4，按前缀删除不会波及其他文档
                for model, folder in ((DocumentPPT, "ppt_output"), (DocumentPoster, "poster_output")):
                    session_ids = (await self.db.execute(
                        select(model.session_id).filter(
                            model.document_id == document.id, model.session_id.isnot(None)
                        )
                    )).scalars().all()
                    cos_prefixes.extend(f"{folder}/{sid}/" for sid in session_ids if sid)

            # 1.3 记下这篇文档关联的知识网络节点：关系会被级联删除，删除后要重算计数、清理孤立节点
            from knowledge_graph.models import DocumentKnowledgeTerm
            kg_term_ids = set((await self.db.execute(
                select(DocumentKnowledgeTerm.term_id).filter(DocumentKnowledgeTerm.document_id == document.id)
            )).scalars().all())

            # 2. 删除数据库记录（级联删除所有关联表）
            logger.info(f"\n🔄 正在删除数据库记录...")
            await self.db.delete(document)
            await self.db.commit()
            logger.info(f"   ✅ 数据库记录已删除")

            # 2.1 同步知识网络（失败只记日志，不影响删除结果）
            await self._refresh_knowledge_graph_after_delete(owner_id, kg_term_ids)

            # 3. 删除向量数据库 collection（仅当无其他引用时）
            # 🛡️ 容错处理：即使向量删除失败，也不应该回滚已删除的文档记录
            if should_delete_vector:
                try:
                    logger.info(f"\n🔄 正在删除向量数据...")
                    await vector_store_manager._run_in_executor(
                        vector_store_manager.delete_collection, document.vector_store_id
                    )
                    logger.info(f"   ✅ 向量数据已删除")
                except Exception as vec_err:
                    logger.warning(f"⚠️ 向量集合删除失败 (可能是未创建或已删除): {vec_err}")

            # 3.1 删除附加来源的向量集合（仅删除不再被引用的）
            for _source_id, source_collection_id in source_rows:
                await self._delete_collection_if_unreferenced(source_collection_id)

            # 3.2 服务端部署：删除 COS 上的原文备份与生成产物（失败只记日志，不影响删除结果）
            if cos_keys or cos_prefixes:
                try:
                    await asyncio.to_thread(_delete_cos_objects, cos_keys, cos_prefixes)
                except Exception as cos_err:
                    logger.warning(f"⚠️ 删除 COS 文件失败: {cos_err}")

            # 3.3 本地版：删除落盘的原始文件与来源全文
            if settings.is_local_mode:
                try:
                    from runtime.storage import local_storage

                    await asyncio.to_thread(local_storage.delete_document, owner_id, document_id)
                    for source_id, _collection_id in source_rows:
                        await asyncio.to_thread(local_storage.delete_source, owner_id, source_id)
                except Exception as file_err:
                    logger.warning(f"⚠️ 删除本地存储文件失败: {file_err}")

            logger.info(f"\n{'='*60}")
            logger.info(f"✅ 文档删除成功！")
            if should_delete_vector:
                logger.info(f"   已释放向量数据库空间 🗑️")
            else:
                logger.info(f"   向量数据已保留（被其他文档共享）♻️")
            logger.info(f"{'='*60}\n")
            
            # 4. 清理当前用户下不再被任何文档引用的知识图谱节点
            try:
                await cleanup_orphan_terms_for_user(self.db, document.user_id)
            except Exception as kg_err:
                # 清理失败不应影响主流程
                logger.warning(f"⚠️ 清理知识图谱孤立节点失败: {kg_err}")
            
        except Exception as e:
            logger.error(f"❌ 删除失败: {e}")
            # 只有在数据库操作阶段失败才回滚
            # 如果此时文档已经被 commit 删除，rollback 不会把文档找回来，这是符合预期的
            await self.db.rollback()
            raise

