import logging
import json
from typing import Optional, Dict, Any, List, Callable, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from langchain_openai import ChatOpenAI
from db_models import Document
from ..schema import SearchQueriesResponse
from ..utils import parse_json_safely

logger = logging.getLogger(__name__)

class BaseGenerator:
    """基础生成器类，提供通用工具方法"""
    
    def __init__(self, lc_client: ChatOpenAI, db: Optional[AsyncSession] = None):
        """
        初始化生成器
        
        Args:
            lc_client: LangChain ChatOpenAI 客户端
            db: 异步数据库会话（可选）
        """
        self.lc_client = lc_client
        self.db = db
        self._logger = logger.getChild(self.__class__.__name__)

    def _build_context(self, document: Optional[Document] = None, extra: Optional[Dict] = None) -> Dict:
        context = {}
        if document is not None:
            context.update({
                "document_id": getattr(document, "document_id", getattr(document, "id", None)),
                "document_title": getattr(document, "filename", None)
            })
        if extra:
            context.update(extra)
        return context

    def _log_error(self, feature: str, message: str, *, error: Optional[Exception] = None, context: Optional[Dict] = None) -> None:
        payload = {
            "feature": feature,
            "message": message,
            "context": context or {}
        }
        if error:
            self._logger.error("【%s】%s | 上下文: %s", feature, message, payload["context"], exc_info=error)
        else:
            self._logger.error("【%s】%s | 上下文: %s", feature, message, payload["context"])

    def _log_invalid_ai_output(
        self,
        feature: str,
        prompt: str,
        response: Optional[object],
        document: Optional[Document] = None,
        extra: Optional[Dict] = None,
    ) -> None:
        snippet = (prompt[:800] + "…") if len(prompt) > 800 else prompt
        context = self._build_context(document, {
            "prompt_snippet": snippet,
            "prompt_length": len(prompt),
            "ai_response_repr": repr(response)
        })
        if extra:
            context.update(extra)
        self._log_error(feature, "AI返回的结构化结果无效", context=context)

    def _detect_language(self, text: str) -> str:
        """非常简易的语言检测：中文/英文/未知"""
        if not text:
            return "unknown"

        sample = text[:4000]
        zh_count = sum(1 for ch in sample if '\u4e00' <= ch <= '\u9fff')
        en_count = sum(1 for ch in sample.lower() if 'a' <= ch <= 'z')

        if zh_count > 0 and zh_count >= en_count * 1.2:
            return "zh"
        if en_count > 0 and en_count >= zh_count:
            return "en"
        return "unknown"

    def _build_language_instruction(self, target_language: Optional[str], detected_language: str) -> Dict[str, str]:
        language = (target_language or detected_language or "unknown").lower()

        if language.startswith("zh"):
            return {
                "instruction": "请使用自然流畅的中文表达，注意语感与节奏。",
                "resolved": "zh"
            }
        if language.startswith("en"):
            return {
                "instruction": "Please deliver every part in natural, idiomatic English with genuine cadence.",
                "resolved": "en"
            }
        return {
            "instruction": "保持与原文相同的语言与语气，如果原文混合多种语言，请以主要语言书写。",
            "resolved": detected_language if detected_language != "unknown" else "original"
        }

    async def _generate_search_plan(
        self,
        *,
        document: Document,
        feature_name: str,
        planning_prompt: str,
        fallback_semantic_query: str,
        fallback_keywords: Optional[List[str]] = None,
    ) -> SearchQueriesResponse:
        fallback_keywords = fallback_keywords or [document.filename]
        try:
            schema_json = json.dumps(SearchQueriesResponse.model_json_schema(), indent=2, ensure_ascii=False)
            prompt = planning_prompt.format(
                filename=document.filename,
                base_content=(document.summary if document.summary else document.original_text[:2000]),
                schema_json=schema_json,
            )
            response = await self.lc_client.ainvoke([{"role": "user", "content": prompt}])
            data = parse_json_safely(response.content)
            validated_data = SearchQueriesResponse.model_validate(data)
            logger.info(f"🔍 {feature_name}检索方案:")
            logger.info(f"   - 语义查询: {validated_data.semantic_queries}")
            logger.info(f"   - 关键词: {validated_data.keywords}")
            return validated_data
        except Exception as e:
            logger.warning(f"⚠️ 生成{feature_name}检索查询失败，使用默认策略: {e}")
            return SearchQueriesResponse(
                queries=[],
                semantic_queries=[fallback_semantic_query],
                keywords=fallback_keywords,
            )

    async def _retrieve_rag_context(
        self,
        *,
        document: Document,
        search_plan: SearchQueriesResponse,
        top_k: int,
        max_context_chars: int,
        max_chunk_chars: int,
        short_doc_fulltext_threshold: Optional[int] = None,
        short_doc_label: str = "文档完整内容",
    ) -> Tuple[str, str, int]:
        doc_length = len(document.original_text or "")
        if short_doc_fulltext_threshold and doc_length < short_doc_fulltext_threshold:
            logger.info(f"📄 文档较短 (<{short_doc_fulltext_threshold}字)，直接使用全文作为上下文")
            return document.original_text, short_doc_label, 0

        from document_service.vector_store import vector_store_manager

        relevant_chunks = await vector_store_manager.advanced_hybrid_search(
            collection_id=document.vector_store_id,
            semantic_queries=search_plan.semantic_queries,
            keywords=search_plan.keywords,
            top_k=top_k,
        )

        context_text = ""
        current_chars = 0
        used_chunk_count = 0

        for index, chunk in enumerate(relevant_chunks):
            chunk_text = chunk.get("text", "")
            if len(chunk_text) > max_chunk_chars:
                chunk_text = chunk_text[:max_chunk_chars] + "..."

            entry = f"[片段 {index + 1}]\n{chunk_text}\n\n"
            if current_chars + len(entry) > max_context_chars:
                break
            context_text += entry
            current_chars += len(entry)
            used_chunk_count += 1

        logger.info(f"📊 最终构建上下文: {used_chunk_count} 个片段, {current_chars} 字符")
        return context_text, f"文档核心内容（基于{used_chunk_count}个关键检索片段）", used_chunk_count

    async def _invoke_structured_generation(
        self,
        *,
        feature_name: str,
        messages: List[Dict[str, str]],
        parser: Callable[[str], Any],
    ) -> Any:
        logger.info(f"🤖 调用AI生成{feature_name}...")
        response = await self.lc_client.ainvoke(messages)
        return parser(response.content)
