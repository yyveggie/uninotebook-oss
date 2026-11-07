"""
文档问答模块 - Unified ReAct Architecture

流程: Agent 自主决定是否检索、是否需要计划、是否需要追问。
单一 ReAct 循环，无 Plan/Simple 双模式。

Memory 设计（两层架构）：
- 运行摘要 (Running Summary)：覆盖全部历史轮次的 LLM 摘要，每次问答后后台更新
- 最近原文 (Recent Raw)：最近 2 轮原始对话，注入 Agent 消息列表
- Redis 缓存加速，PostgreSQL 持久化兜底
"""

import asyncio
import json
import time
from typing import Dict, Any, Optional, List

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_core.tools import tool
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db_models import Document, DocumentChatHistory
from database import AsyncSessionLocal
from document_service.vector_store import vector_store_manager
from config import settings

from document_service.memory import ConversationMemoryManager, MAX_RECENT_TURNS
from document_service.memory_compression import SummaryUpdater
from llm_service import get_qa_llm, get_llm_for_feature, QA_MODEL_OPTIONS
from utils.logger import get_logger
logger = get_logger(__name__)


# ==================== 问答管理器 ====================


class QAManager:
    """文档问答管理器 - Unified ReAct Agent"""

    def __init__(self, db: Optional[AsyncSession] = None, model: str = None):
        """
        初始化问答管理器

        Args:
            db: 异步数据库会话
            model: LLM模型名称（默认使用配置中的模型）
        """
        self.db = db
        self.model = model or settings.openai_model

        # Memory 管理器
        self.memory_manager = ConversationMemoryManager(db) if db else None

        # 默认 LLM（按模式区分）
        self.llm_fast = get_llm_for_feature("qa_fast", streaming=True, max_tokens=16000, request_timeout=300)
        self.llm_agent = get_llm_for_feature("qa_agent", streaming=True, max_tokens=16000, request_timeout=300)

        # 专用路由 LLM
        self.router_llm = get_llm_for_feature("router", max_tokens=4096, request_timeout=300)

    def _create_llm(self, model_key: str = "default"):
        """根据模型Key创建LLM客户端"""
        key_mapping = {
            "default": "qwen",
            "qwen3-max-preview": "qwen-preview",
        }
        mapped_key = key_mapping.get(model_key, model_key)
        return get_qa_llm(mapped_key, max_tokens=16000)

    @staticmethod
    def clean_text_newlines(text: str) -> str:
        """清理文本中的多余换行"""
        if not text:
            return text
        lines = [line.strip() for line in text.split("\n")]
        return " ".join(lines)

    @staticmethod
    def _clean_source_title(metadata: dict, fallback_filename: str) -> str:
        """清理引用标题。PDF 的 running header 通常很长且重复，直接用文件名。"""
        raw_title = metadata.get("title", "")
        if not raw_title:
            return fallback_filename
        # 超过 80 字符的标题通常是 PDF running header / 完整引用信息
        if len(raw_title) > 80:
            return fallback_filename
        return raw_title

    # ==================== 非流式接口 ====================

    async def ask_document(
        self,
        document: Document,
        question: str,
        conversation_history: Optional[List[Dict]] = None,
        user_id: Optional[int] = None,
        session_id: Optional[str] = None,
        max_iterations: Optional[int] = None,
        db_question: Optional[str] = None,
        model: Optional[str] = None,
        is_agent_mode: bool = False,
        selected_source_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """统一的文档问答接口（非流式 wrapper）"""
        generator = self.ask_document_stream(
            document=document,
            question=question,
            conversation_history=conversation_history,
            user_id=user_id,
            session_id=session_id,
            max_iterations=max_iterations,
            db_question=db_question,
            model=None,
            is_agent_mode=is_agent_mode,
            selected_source_ids=selected_source_ids,
        )

        final_answer = ""
        sources = []
        stats = {"used_rag": False, "iterations": 0, "duration": 0.0}

        async for event_str in generator:
            if not event_str.startswith("data: "):
                continue
            try:
                payload = json.loads(event_str[6:].strip())
                event_type = payload.get("event")
                data = payload.get("data")

                if event_type == "token":
                    final_answer += data
                elif event_type == "sources":
                    sources = data
                elif event_type == "done":
                    stats["used_rag"] = data.get("used_rag", False)
                    stats["iterations"] = data.get("iterations", 0)
                    stats["duration"] = data.get("duration", 0.0)
                elif event_type == "error":
                    raise Exception(data)
            except Exception as e:
                if "error" in str(type(e).__name__).lower():
                    raise
                logger.warning(f"⚠️ 解析流式响应失败: {e}")
                continue

        return {
            "answer": final_answer,
            "used_rag": stats["used_rag"],
            "iterations": stats["iterations"],
            "duration": stats["duration"],
            "sources": sources,
        }

    # ==================== 检索 ====================

    async def _retrieve_advanced_chunks(
        self,
        document: Document,
        semantic_queries: List[str],
        keywords: List[str],
        max_chunks: int = 15,
        selected_collection_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """执行高级分层混合检索"""
        vector_id = document.vector_store_id or ""

        is_plain_vector = (
            not vector_id
            or vector_id.startswith("text_direct_")
            or vector_id.startswith("doc_temp_")
            or vector_id.startswith("doc_pending_")
        )

        if is_plain_vector:
            if not document.original_text and vector_id.startswith("doc_pending_"):
                logger.info("   ⏳ 文档正在解析中，跳过检索")
                return [{"text": "文档正在解析中，请等待解析完成后再提问。", "score": 1.0, "final_score": 1.0}]

            logger.warning("   ⚠️ 文档无向量集合，使用全文作为上下文")
            text_context = (document.original_text or "")[:6000]
            if not text_context:
                text_context = "文档正在解析中，请稍后再试..."

            return [{"text": text_context, "score": 1.0, "final_score": 1.0}]

        collection_ids = [
            collection_id
            for collection_id in (selected_collection_ids or [document.vector_store_id])
            if isinstance(collection_id, str) and collection_id.strip()
        ]
        if not collection_ids:
            collection_ids = [document.vector_store_id]

        tasks = [
            vector_store_manager.advanced_hybrid_search(
                collection_id=collection_id,
                semantic_queries=semantic_queries,
                keywords=keywords,
                top_k=max(10, max_chunks),
            )
            for collection_id in collection_ids
        ]
        results_by_collection = await asyncio.gather(*tasks, return_exceptions=True)

        merged: List[Dict[str, Any]] = []
        seen_ids = set()
        for collection_id, result in zip(collection_ids, results_by_collection):
            if isinstance(result, Exception):
                logger.warning(f"⚠️ collection 检索失败: {collection_id} -> {result}")
                continue
            for item in result:
                item_id = item.get("id")
                dedup_key = f"{collection_id}:{item_id}" if item_id else f"{collection_id}:{item.get('text', '')[:120]}"
                if dedup_key in seen_ids:
                    continue
                seen_ids.add(dedup_key)
                metadata = item.get("metadata") or {}
                metadata["collection_id"] = collection_id
                item["metadata"] = metadata
                merged.append(item)

        merged.sort(key=lambda x: float(x.get("final_score", 0.0)), reverse=True)
        return merged[:max_chunks]

    async def _resolve_sources_unified(
        self,
        document: Document,
        selected_source_ids: Optional[List[str]],
        user_id: Optional[int],
    ) -> Dict[str, Any]:
        """
        统一解析来源信息（单次 SQL 查询替代之前的 3 次）。

        Returns:
            {
                "collection_ids": [...],
                "source_names": [...],
                "collection_name_map": {collection_id: name, ...},
            }
        """
        selected = [sid for sid in (selected_source_ids or []) if isinstance(sid, str) and sid.strip()]
        include_main = not selected or any(sid.startswith("document:") for sid in selected)

        collection_ids: List[str] = []
        source_names: List[str] = []
        collection_name_map: Dict[str, str] = {}

        main_vid = str(document.vector_store_id) if document.vector_store_id else ""
        main_is_valid = main_vid and not main_vid.startswith("text_direct_")

        if include_main and main_is_valid:
            collection_ids.append(main_vid)
            collection_name_map[main_vid] = document.filename or "主文档"
        if include_main:
            source_names.append(document.filename or "主文档")

        parsed_source_ids = []
        for sid in selected:
            if sid.startswith("source:"):
                raw_id = sid.split(":", 1)[1]
                if raw_id.isdigit():
                    parsed_source_ids.append(int(raw_id))

        if parsed_source_ids and self.db and user_id:
            try:
                from db_models import DocumentSource
                result = await self.db.execute(
                    select(DocumentSource).where(
                        DocumentSource.document_id == document.id,
                        DocumentSource.user_id == user_id,
                        DocumentSource.id.in_(parsed_source_ids),
                    )
                )
                rows = result.scalars().all()
                for row in rows:
                    name = row.source_name or f"来源#{row.id}"
                    source_names.append(name)
                    if row.vector_store_id:
                        vid = str(row.vector_store_id)
                        collection_ids.append(vid)
                        collection_name_map[vid] = name
            except Exception as e:
                logger.warning(f"⚠️ 读取来源信息失败: {e}")

        if not collection_ids and main_is_valid:
            collection_ids.append(main_vid)

        if main_is_valid and main_vid not in collection_name_map:
            collection_name_map[main_vid] = document.filename or "主文档"

        seen = set()
        unique_ids = []
        for cid in collection_ids:
            if cid not in seen:
                seen.add(cid)
                unique_ids.append(cid)

        return {
            "collection_ids": unique_ids,
            "source_names": source_names,
            "collection_name_map": collection_name_map,
        }

    async def _resolve_selected_collection_ids(
        self,
        document: Document,
        selected_source_ids: Optional[List[str]],
        user_id: Optional[int],
    ) -> List[str]:
        r = await self._resolve_sources_unified(document, selected_source_ids, user_id)
        return r["collection_ids"]

    async def _get_selected_source_names(
        self,
        document: Document,
        selected_source_ids: Optional[List[str]],
        user_id: Optional[int],
    ) -> List[str]:
        r = await self._resolve_sources_unified(document, selected_source_ids, user_id)
        return r["source_names"]

    async def _build_collection_name_map(
        self,
        document: Document,
        selected_source_ids: Optional[List[str]],
        user_id: Optional[int],
    ) -> Dict[str, str]:
        """返回 {collection_id: 来源名称} 映射，用于在检索结果中标注 chunk 来源。"""
        mapping: Dict[str, str] = {}
        selected = [sid for sid in (selected_source_ids or []) if isinstance(sid, str) and sid.strip()]

        include_main = not selected or any(sid.startswith("document:") for sid in selected)
        if include_main and document.vector_store_id:
            mapping[str(document.vector_store_id)] = document.filename or "主文档"

        source_ids = []
        for sid in selected:
            if sid.startswith("source:"):
                raw_id = sid.split(":", 1)[1]
                if raw_id.isdigit():
                    source_ids.append(int(raw_id))

        if source_ids and self.db and user_id:
            try:
                from db_models import DocumentSource
                result = await self.db.execute(
                    select(DocumentSource).where(
                        DocumentSource.document_id == document.id,
                        DocumentSource.user_id == user_id,
                        DocumentSource.id.in_(source_ids),
                    )
                )
                rows = result.scalars().all()
                for row in rows:
                    if row.vector_store_id:
                        mapping[str(row.vector_store_id)] = row.source_name or f"来源#{row.id}"
            except Exception:
                pass

        # 保底：主文档始终在映射里
        if document.vector_store_id and str(document.vector_store_id) not in mapping:
            mapping[str(document.vector_store_id)] = document.filename or "主文档"

        return mapping

    async def _get_qa_file_multiplier(
        self,
        document: Document,
        user_id: Optional[int],
    ) -> int:
        """计算 QA 文件倍增系数：主文档为 1，每多 1 个 file 来源，系数翻倍。"""
        if not self.db or not user_id:
            return 1
        try:
            from db_models import DocumentSource
            result = await self.db.execute(
                select(func.count(DocumentSource.id)).where(
                    DocumentSource.document_id == document.id,
                    DocumentSource.user_id == user_id,
                    DocumentSource.source_type == "file",
                )
            )
            file_source_count = int(result.scalar() or 0)
            return 2 ** max(file_source_count, 0)
        except Exception:
            return 1

    # ==================== 数据库辅助 ====================

    @staticmethod
    async def _create_chat_history_placeholder(
        document_id: int, user_id: int, session_id: str, question: str
    ) -> Optional[int]:
        """创建聊天历史占位符"""
        try:
            async with AsyncSessionLocal() as db:
                chat_history = DocumentChatHistory(
                    document_id=document_id,
                    user_id=user_id,
                    session_id=session_id,
                    question=question,
                    answer="",
                    relevant_chunks=None,
                )
                db.add(chat_history)
                await db.commit()
                await db.refresh(chat_history)

                from cache_service import invalidate_chat_history_cache
                # 新消息：只失效 UI 列表缓存，不清除 memory/summary 缓存
                await invalidate_chat_history_cache(user_id, document_id, session_id, clear_memory=False)

                return chat_history.id
        except Exception as e:
            logger.warning(f"⚠️ 创建聊天历史占位符失败: {e}")
            return None

    @staticmethod
    async def _update_chat_history_background(
        message_id: int,
        answer: str,
        relevant_payload: Optional[List[Dict]],
        iterations: int,
        elapsed_time: float,
    ):
        """后台更新聊天历史"""
        if not message_id:
            return
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(DocumentChatHistory).filter(
                        DocumentChatHistory.id == message_id
                    )
                )
                record = result.scalar_one_or_none()

                if record:
                    record.answer = answer
                    if isinstance(relevant_payload, dict):
                        record.relevant_chunks = relevant_payload.get("sources")
                        record.flowchart = relevant_payload.get("flowchart")
                        record.chart_data = relevant_payload.get("chart")
                        record.status_history = relevant_payload.get("status_history")
                    else:
                        record.relevant_chunks = relevant_payload
                    record.search_count = iterations
                    record.elapsed_time = elapsed_time
                    await db.commit()
                    logger.info(f"✅ 后台更新聊天记录 {message_id} 完成")
                else:
                    logger.warning(f"⚠️ 更新聊天历史失败: 找不到记录 {message_id}")
        except Exception as e:
            logger.warning(f"⚠️ 更新聊天历史失败（后台）: {e}")

    async def _update_conversation_summary(
        self,
        user_id: int,
        document_id: int,
        session_id: str,
        question: str,
        answer: str,
        existing_summary: str,
    ):
        """后台更新运行摘要（渐进式）"""
        try:
            updater = SummaryUpdater(self.router_llm)
            new_summary = await updater.update(
                existing_summary=existing_summary,
                new_question=question,
                new_answer=answer,
            )
            if new_summary and self.memory_manager:
                await self.memory_manager.save_summary(
                    user_id=user_id,
                    document_id=document_id,
                    session_id=session_id,
                    summary=new_summary,
                )
        except Exception as e:
            logger.warning(f"⚠️ [Summary] 后台更新摘要失败: {e}")

    async def ask_document_stream(
        self,
        document: Document,
        question: str,
        conversation_history: Optional[List[Dict]],
        user_id: Optional[int],
        session_id: Optional[str],
        max_iterations: Optional[int] = None,
        db_question: Optional[str] = None,
        model: Optional[str] = None,
        is_agent_mode: bool = False,
        selected_source_ids: Optional[List[str]] = None,
    ):
        logger.info(f"\n🚀 [QA] ask_document_stream called: is_agent_mode={is_agent_mode}, model={model}\n")
        """
        流式文档问答接口 (Unified ReAct Agent)
        Yields: SSE 格式的字符串
        """
        from document_service.agent_graph import RAGAgent
        from document_service.stream_handler import StreamHandler
        from document_service.tool_factory import RetrievalContext, SearchToolFactory

        try:
            start_time = time.time()

            # 并行加载来源信息 + Memory（无数据依赖，可同时执行）
            need_memory = (
                not conversation_history
                and self.memory_manager
                and user_id
                and session_id
            )

            source_task = self._resolve_sources_unified(
                document=document,
                selected_source_ids=selected_source_ids,
                user_id=user_id,
            )
            memory_task = (
                self.memory_manager.load_memory(
                    user_id, document.id, session_id, MAX_RECENT_TURNS
                )
                if need_memory
                else asyncio.sleep(0)
            )

            sources_result, memory_result = await asyncio.gather(
                source_task, memory_task
            )

            selected_collection_ids = sources_result["collection_ids"]
            selected_source_names = sources_result["source_names"]
            collection_name_map = sources_result["collection_name_map"]

            conversation_summary = ""
            if need_memory and hasattr(memory_result, "to_langchain_format"):
                conversation_history = memory_result.to_langchain_format()
                conversation_summary = memory_result.summary or ""
                logger.info(
                    f"   📦 [Memory] Loaded: summary={len(conversation_summary)}chars, "
                    f"recent={len(conversation_history)}rounds"
                )

            # 2. Background: create chat history placeholder（在 memory 加载完成后再创建）
            chat_history_task = asyncio.create_task(
                self._create_chat_history_placeholder(
                    document.id, user_id, session_id, db_question or question
                )
            )

            # 3. Select LLM
            if is_agent_mode:
                llm = self.llm_agent
            else:
                llm = self.llm_fast

            if model:
                logger.info(f"ℹ️ [QA] 用户传入模型参数已忽略: {model}")
            logger.info(f"✅ [QA] Selected Model: {'agent-default' if is_agent_mode else 'fast-default'}, Max Iterations: {max_iterations}")
            evaluator_llm = self._create_llm("qwen")

            # 4. Create tools
            retrieval_ctx = RetrievalContext()

            # ★ 快速模式预检索：直接用用户问题做检索，省去 Agent 的"决定搜索"那轮 LLM 调用
            pre_retrieved_context = ""
            if not is_agent_mode:
                try:
                    import jieba
                    kw_tokens = [w for w in jieba.cut(question, cut_all=False) if len(w) >= 2]
                    pre_keywords = list(dict.fromkeys(kw_tokens))[:4]

                    pre_chunks = await self._retrieve_advanced_chunks(
                        document=document,
                        semantic_queries=[question],
                        keywords=pre_keywords,
                        max_chunks=8,
                        selected_collection_ids=selected_collection_ids,
                    )
                    if pre_chunks:
                        retrieval_ctx.add_chunks(pre_chunks)
                        retrieval_ctx.search_call_count += 1
                        retrieval_ctx.record_query([question])
                        pre_retrieved_context = "\n\n".join(
                            f"[段落 {i+1}] (Score: {c.get('final_score', 0):.2f})\n{c.get('text', '')[:500]}"
                            for i, c in enumerate(pre_chunks[:5])
                        )
                        logger.info(f"✅ [QA] 快速模式预检索: {len(pre_chunks)} 段落, {len(pre_retrieved_context)} chars")
                except Exception as e:
                    logger.warning(f"⚠️ [QA] 预检索失败（回退到 Agent 自主检索）: {e}")

            search_document = SearchToolFactory.create_tool(
                retrieval_ctx=retrieval_ctx,
                qa_manager=self,
                document=document,
                deduplicate_queries_func=RetrievalContext.deduplicate_queries,
                max_searches=5,
                selected_collection_ids=selected_collection_ids,
                collection_name_map=collection_name_map,
            )

            @tool
            async def generate_learning_path(title: str, mermaid_code: str):
                """
                Render a Mermaid flowchart/learning path. YOU must generate the mermaid_code yourself.

                ⚠️ Mermaid syntax rules (CRITICAL):
                1. **Direction**: Use `graph TB` (Top to Bottom) or `graph LR` (Left to Right).
                2. **Node IDs**: MUST be simple English alphanumeric (e.g., `A`, `B`, `step1`, `node2`). DO NOT use Chinese or special chars for IDs.
                3. **Labels**: ALL node text MUST be wrapped in double quotes `["Content"]`.
                   - Correct: `A["Input Data"]`
                   - Wrong: `A[Input Data]`
                4. **Forbidden in Labels**:
                   - NO Markdown (no `**bold**`, no `*italic*`)
                   - NO double quotes `"` inside the label.
                   - Avoid special chars like `()`, `[]`, `{}`, `<`, `>`, `|` if possible.
                5. **Example**:
                    graph TB
                        start["Begin Process"] --> check{"Is Valid?"}
                        check -- "Yes" --> proc["Process Data"]
                        check -- "No" --> err["Report Error"]
                        proc --> finish["End"]

                Args:
                    title: Flowchart title (Chinese)
                    mermaid_code: Complete Mermaid diagram code you generated
                """
                logger.info(f"🎨 [Tool] generate_learning_path: title={title}")
                return json.dumps(
                    {"title": title, "mermaid_code": mermaid_code}, ensure_ascii=False
                )

            @tool
            async def generate_data_plot(
                title: str,
                chart_type: str,
                data: List[Dict[str, Any]],
                xKey: str,
                dataKeys: List[str],
                unit: str = "",
            ):
                """
                Render a data chart. YOU must extract data from retrieved content and pass it here.
                ⚠️ You MUST call search_document first to get the data before calling this tool.

                Args:
                    title: Chart title (Chinese)
                    chart_type: One of "bar", "line", "pie", "area"
                    data: Data points list, e.g. [{"year": "2020", "value": 100}]
                    xKey: X-axis field name
                    dataKeys: Y-axis value field name list
                    unit: Data unit (optional)
                """
                logger.info(f"📊 [Tool] generate_data_plot: title={title}, type={chart_type}")
                return json.dumps(
                    {
                        "title": title,
                        "type": chart_type,
                        "data": data,
                        "xKey": xKey,
                        "dataKeys": dataKeys,
                        "unit": unit,
                    },
                    ensure_ascii=False,
                )

            @tool
            async def create_plan(steps: List[Dict[str, str]]):
                """
                Create and publish an execution plan for complex user requests.
                Use this when the task requires multiple logical steps before final answering.

                Args:
                    steps: Ordered step list.
                        Each step should contain:
                        - title: short step name (Chinese)
                        - description: what to do in this step (Chinese)
                """
                normalized_steps = []
                for item in steps[:8]:
                    if not isinstance(item, dict):
                        continue
                    title = str(item.get("title", "")).strip()
                    description = str(item.get("description", "")).strip()
                    if title:
                        normalized_steps.append(
                            {"title": title, "description": description}
                        )

                logger.info(f"📋 [Tool] create_plan: {len(normalized_steps)} steps")
                return json.dumps({"steps": normalized_steps}, ensure_ascii=False)



            @tool
            async def ask_clarification(
                question: str, options: Optional[List[str]] = None
            ):
                """
                Ask the user a clarification question when their query is genuinely ambiguous.
                Use ONLY when input is truly unclear or multiple valid interpretations exist.
                Do NOT use for normal questions that can be answered with document search.

                Args:
                    question: The clarification question (Chinese)
                    options: Optional list of 2-4 choices for the user (Chinese)
                """
                logger.info(f"💬 [Tool] ask_clarification: {question}")
                return json.dumps(
                    {
                        "question": question,
                        "options": options or [],
                        "_stop": True,  # Signal: agent should stop after this
                    },
                    ensure_ascii=False,
                )

            tools = [
                search_document,
                create_plan,
                generate_learning_path,
                generate_data_plot,
                ask_clarification,
            ]

            # 5. Resolve placeholder ID
            try:
                chat_history_id = await chat_history_task
            except Exception as e:
                logger.warning(f"⚠️ Placeholder task failed: {e}")
                chat_history_id = None
            message_id = chat_history_id

            # 6. Build messages
            if len(selected_source_names) > 1:
                sources_list = "\n".join(f"  {i+1}. 《{n}》" for i, n in enumerate(selected_source_names))
                doc_info = (
                    f"当前已加载 {len(selected_source_names)} 篇来源文档，你可以检索所有来源：\n"
                    f"{sources_list}\n"
                    f"主文档摘要：{document.summary or '暂无'}"
                )
            else:
                doc_info = (
                    f"目标文档：《{document.filename}》\n"
                    f"文档摘要：{document.summary or '暂无'}"
                )

            summary_section = ""
            if conversation_summary:
                summary_section = (
                    f"\n📚 **对话记忆（历史摘要）**：\n{conversation_summary}\n"
                )

            system_prompt = (
                f"你是专业的且友好的文档分析助手，负责根据文档内容回答用户问题。\n"
                f"{doc_info}\n"
                f"{summary_section}\n"
                f"🛠️ **你不能用自己的知识回答，只能来源文档。你拥有以下工具可供调用**：\n"
                f"`search_document` — 检索文档细节。如果当前信息不足以回答，必须调用此工具。\n"
                f"`create_plan` — 对复杂任务先给出步骤计划。\n"
                f"`generate_learning_path` — 生成流程图/思维导图（**必须**调用此工具才能展示）。\n"
                f"`generate_data_plot` — 生成数据图表（**必须**调用此工具才能展示）。\n"
                f"`ask_clarification` — 当问题模糊时追问用户。\n\n"
                f"LaTeX 公式：行内用 $...$，独立用 $$...$$。\n"
            )

            # 🆕 动态Prompt约束 (Restored to fix missing output issue)
            if is_agent_mode:
                system_prompt += (
                    f"核心指令 - **深度思考**：\n"
                    f"- 你处于智能体模式，请充分利用工具进行多步推理。\n"
                    f"- 遇到复杂问题，可以多次搜索并综合信息，但不要重复搜索相同的query和keywords。\n"
                    f"- 即使第一轮搜索结果不完美，也可以尝试变更关键词再次搜索。\n"
                    f"- 该模式下可以直接进行反思和补充搜索，不必急于结束。\n"
                    f"- 请详细认真回答每一个问题。\n\n"
                )
            else:
                if pre_retrieved_context:
                    system_prompt += (
                        f"核心指令 - **快速回答**：\n"
                        f"- 系统已为你预检索了以下文档段落，请直接基于这些信息回答。\n"
                        f"- 如果以下信息不足以回答，可以调用 `search_document` 补充检索。\n"
                        f"- 虽然是快速回答，但也要详细认真，完整说完每句话。\n\n"
                        f"📚 **已检索到的文档段落**：\n{pre_retrieved_context}\n\n"
                    )
                else:
                    system_prompt += (
                        f"核心指令 - **快速回答**：\n"
                        f"- 工具调用完成后，立即根据结果生成最终回答。\n"
                        f"- 虽然是快速回答，但也要详细认真，完整说完每句话。\n"
                    )

            messages = [SystemMessage(content=system_prompt)]

            # Inject recent conversation history (adaptive: summary 存在时减少原文注入量)
            if conversation_history:
                # 有摘要时只注入最近 2 轮原文（摘要已覆盖更早的内容）
                # 无摘要时注入最近 3 轮
                inject_turns = 2 if conversation_summary else 3
                recent = conversation_history[-inject_turns:]
                # 回答截断阈值也随之调整：有摘要时更激进
                answer_max_chars = 500 if conversation_summary else 800
                logger.info(
                    f"   ✅ [Context] 注入最近 {len(recent)} 轮原始对话 + "
                    f"summary({len(conversation_summary)}chars), 回答截断={answer_max_chars}"
                )
                for msg in recent:
                    q = msg.get("question")
                    a = msg.get("answer")
                    if q and a:
                        messages.append(HumanMessage(content=str(q)))
                        a_str = str(a)
                        if len(a_str) > answer_max_chars:
                            half = answer_max_chars // 2
                            a_str = (
                                a_str[:half]
                                + f"\n\n[... 省略 {len(a_str) - answer_max_chars} 字符 ...]\n\n"
                                + a_str[-half:]
                            )
                        messages.append(AIMessage(content=a_str))

            messages.append(HumanMessage(content=question))

            # 7. Create Agent + StreamHandler
            agent = RAGAgent(
                llm=llm,
                tools=tools,
                max_iterations=max_iterations,
                evaluator_llm=evaluator_llm,
                enable_evaluator=is_agent_mode,
            )
            handler = StreamHandler(retrieval_ctx, document.filename)

            # 9. Stream events through handler
            initial_state = {
                "messages": messages,
                "iteration_count": 0,
                "max_iterations": agent.max_iterations,  # Use resolved value, not raw param
            }
            config = {"recursion_limit": 50}

            event_stream = agent.graph.astream_events(
                initial_state, version="v2", config=config
            )
            async for sse_msg in handler.process_events(event_stream):
                yield sse_msg

            # 10. Post-processing
            end_time = time.time()
            elapsed_time = round(end_time - start_time, 2)
            full_answer = handler.full_answer
            retrieval_tracks = retrieval_ctx.get_tracks()

            # 🆕 截断检测：如果模型被截断 (finish_reason=length)，追加提示
            if getattr(handler, '_truncated', False) and full_answer:
                truncation_notice = "\n\n---\n⚠️ *回答可能未完整，建议重新提问或使用智能体模式获取更详细的回答。*"
                full_answer += truncation_notice
                handler.full_answer = full_answer
                yield handler.sse("token", truncation_notice)
                logger.warning(f"⚠️ [QA] 已向用户提示回答可能被截断")
            
            # 🆕 回答完整性日志
            if full_answer:
                answer_len = len(full_answer)
                # 检查是否以不完整的方式结束（中文句子通常以 。！？）结尾）
                stripped = full_answer.rstrip()
                ends_properly = stripped and stripped[-1] in '。！？!?.…\n》）)'
                if not ends_properly and answer_len > 50:
                    logger.info(
                        f"⚠️ [QA] 回答可能不完整: {answer_len}字, "
                        f"末尾='{stripped[-20:] if stripped else '(empty)'}'"
                    )

            # Send sources — 忠实显示检索层保留的全部 chunks
            if retrieval_tracks:
                formatted_sources = [
                    {
                        "title": self._clean_source_title(
                            c.get("metadata", {}), document.filename
                        ),
                        "text": self.clean_text_newlines(c.get("text", "")),
                        "score": round(
                            c.get("final_score", c.get("score", 0)), 2
                        ),
                        "id": str(c.get("id", i)),
                    }
                    for i, c in enumerate(retrieval_tracks)
                    if isinstance(c, dict)
                ]
                yield handler.sse("sources", formatted_sources)

            # Credit deduction
            credits_deducted = 0
            if user_id and full_answer:
                try:
                    from credit_service import CreditService, CreditCost
                    file_multiplier = await self._get_qa_file_multiplier(document, user_id)

                    async with AsyncSessionLocal() as credit_db:
                        credit_service = CreditService(credit_db)
                        response_length = len(full_answer)
                        tool_call_count = len(retrieval_tracks)
                        base_credits_cost = CreditCost.calculate_qa_cost(
                            response_length, 
                            tool_call_count,
                            is_agent_mode=is_agent_mode
                        )
                        credits_cost = base_credits_cost * file_multiplier
                        try:
                            await credit_service.deduct_credits(
                                user_id=user_id,
                                amount=credits_cost,
                                transaction_type="qa",
                                description=(
                                    f"问答对话 (回复{response_length}字, 检索{tool_call_count}次, "
                                    f"文件倍数x{file_multiplier}, 基础{base_credits_cost}分)"
                                ),
                                reference_id=str(document.id),
                            )
                            await credit_db.commit()
                            credits_deducted = credits_cost
                        except Exception as credit_err:
                            logger.warning(f"⚠️ [QA Credit] 扣费失败: {credit_err}")
                except Exception as e:
                    logger.warning(f"⚠️ [QA Credit] 初始化失败: {e}")

            # Done event
            yield handler.sse(
                "done",
                {
                    "duration": elapsed_time,
                    "used_rag": len(retrieval_tracks) > 0,
                    "iterations": retrieval_ctx.search_call_count,
                    "search_count": retrieval_ctx.search_call_count,
                    "message_id": message_id,
                    "credits_deducted": credits_deducted,
                    "type": "clarification" if handler.is_clarification else "answer",
                },
            )

            # Background: save history & update summary
            if user_id and session_id and full_answer:
                relevant_payload = {
                    "sources": retrieval_tracks,
                    "flowchart": handler.flowchart_data,
                    "chart": handler.chart_data,
                    "status_history": handler.execution_logs,
                }

                if message_id:
                    asyncio.create_task(
                        self._update_chat_history_background(
                            message_id=message_id,
                            answer=full_answer,
                            relevant_payload=relevant_payload,
                            iterations=retrieval_ctx.search_call_count,
                            elapsed_time=elapsed_time,
                        )
                    )

                if self.memory_manager:
                    # 更新 Redis 消息缓存（确保下轮对话能看到本轮内容）
                    asyncio.create_task(
                        self.memory_manager.add_message(
                            user_id=user_id,
                            document_id=document.id,
                            session_id=session_id,
                            question=db_question or question,
                            answer=full_answer,
                        )
                    )
                    # 后台更新运行摘要
                    asyncio.create_task(
                        self._update_conversation_summary(
                            user_id=user_id,
                            document_id=document.id,
                            session_id=session_id,
                            question=db_question or question,
                            answer=full_answer,
                            existing_summary=conversation_summary,
                        )
                    )

        except Exception as e:
            import traceback
            traceback.print_exc()
            from runtime.features import public_model_error
            yield StreamHandler.sse("error", public_model_error(e))