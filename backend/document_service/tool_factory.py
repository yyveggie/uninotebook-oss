import asyncio
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, field_validator
from langchain_core.tools import tool, Tool
from utils.logger import get_logger
logger = get_logger(__name__)

class RetrievalContext:
    """封装检索上下文，确保每次调用独立"""
    def __init__(self):
        self.tracks: List[Dict[str, Any]] = []
        self.last_queries: List[str] = []
        self.last_keywords: List[str] = []
        self.router_queries: List[str] = []
        self.router_keywords: List[str] = []
        self.router_used: bool = False
        self._seen_signatures: set = set()
        self.search_call_count: int = 0
        # 已执行过的查询（用于去重，避免 Agent 重复检索）
        self._searched_queries: List[str] = []
    
    def set_router_analysis(self, queries: List[str], keywords: List[str]):
        """存储 Router 的预分析结果"""
        self.router_queries = queries or []
        self.router_keywords = keywords or []
    
    def consume_router_analysis(self):
        """
        消费 Router 的预分析结果（只能用一次）。
        首轮返回 (queries, keywords)，后续返回 (None, None)。
        后续检索由 Agent 的 query 自行驱动，不再强制复用 Router keywords。
        """
        if not self.router_used and (self.router_queries or self.router_keywords):
            self.router_used = True
            return self.router_queries, self.router_keywords
        return None, None
    
    def get_router_keywords_fallback(self) -> List[str]:
        """
        获取 Router 关键词作为兜底（仅在 Agent 未提供有效 keywords 时使用）。
        Router 在干净上下文中生成的关键词质量通常最高，
        作为 BM25 搜索的兜底可避免关键词路完全无输入。
        """
        return self.router_keywords if self.router_keywords else []
    
    def add_chunks(self, chunks: List[Dict[str, Any]], limit: int = 8) -> int:
        """添加检索到的 chunks（带去重），返回新增数量"""
        added = 0
        for c in chunks:
            if added >= limit:
                break
            text = c.get('text', '')
            if not text:
                continue
            sig = text[:200].strip()
            if sig not in self._seen_signatures:
                self._seen_signatures.add(sig)
                self.tracks.append(c)
                added += 1
        logger.info(f"   📦 [Tracks] +{added} new, total: {len(self.tracks)}")
        return added
    
    def set_search_details(self, queries: List[str], keywords: List[str]):
        """记录检索详情"""
        self.last_queries = queries or []
        self.last_keywords = keywords or []
    
    def get_search_details(self) -> Dict[str, Any]:
        """获取检索详情"""
        return {
            "queries": self.last_queries,
            "keywords": self.last_keywords
        }
    
    def get_context_text(self, limit: int = 8, max_chars: int = 500) -> str:
        """获取上下文文本"""
        return "\n".join([f"- {c.get('text', '')[:max_chars]}..." for c in self.tracks[-limit:]])
    
    def get_tracks(self) -> List[Dict[str, Any]]:
        """获取所有检索记录"""
        return self.tracks
    
    def is_duplicate_query(self, query: str, threshold: float = 0.6) -> bool:
        """检查查询是否与已搜索过的查询高度重复（Jaccard > 0.6）"""
        if not query or not self._searched_queries:
            return False
            
        for prev in self._searched_queries:
            if RetrievalContext.calculate_jaccard_similarity(query, prev) > threshold:
                return True
        return False
    
    def record_query(self, queries: List[str]):
        """记录已执行的查询"""
        self._searched_queries.extend(queries)

    @staticmethod
    def calculate_jaccard_similarity(s1: str, s2: str) -> float:
        """计算两个字符串的 Jaccard 相似度"""
        words1 = set(s1.lower().split())
        words2 = set(s2.lower().split())
        if not words1 or not words2:
            return 0.0
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def deduplicate_queries(queries: List[str], threshold: float = 0.6) -> List[str]:
        """
        去除列表中的语义相似查询 (Jaccard > threshold)
        """
        if not queries:
            return []
        
        unique_queries = []
        for q in queries:
            if not q or not q.strip():
                continue
            
            is_similar = False
            for existing_q in unique_queries:
                if RetrievalContext.calculate_jaccard_similarity(q, existing_q) > threshold:
                    is_similar = True
                    break
            
            if not is_similar:
                unique_queries.append(q)
        return unique_queries


class SearchToolFactory:
    """Search Tool Factory to decouple tool definition from QA service logic"""
    
    @staticmethod
    def create_search_tool(retrieval_ctx: RetrievalContext, qa_manager_instance) -> Any:
        """
        Create a search_document tool bound to the given context.
        
        Args:
            retrieval_ctx: The retrieval context instance for this request.
            qa_manager_instance: The QAManager instance (needed to call _retrieve_advanced_chunks).
            
        Returns:
            A LangChain Tool function.
        """
        
        # We need to capture document context from the closure where this is called in qa.py
        # However, since we are moving this out, we need a way to access `document` object.
        # But `document` is available in `ask_document_stream` scope.
        # The cleanest way without massive refactoring of `ask_document_stream` arguments
        # is to create this factory method such that it returns the tool function definition
        # which acts as a closure over the necessary scope if defined inline, 
        # OR we pass the `document` explicitly here.
        
        # To make it truly decoupled, we should require `document` to be passed here.
        # Let's adjust the signature to accept `document`.
        
        raise NotImplementedError("This method is intended to be used with a closure-like structure. See create_tool_with_context")

    @staticmethod
    def create_tool(
        retrieval_ctx: RetrievalContext, 
        qa_manager, 
        document,
        deduplicate_queries_func,
        max_searches: int = 15,  # Default limit
        selected_collection_ids: Optional[List[str]] = None,
        collection_name_map: Optional[Dict[str, str]] = None,
    ):
        """
        Creates the search_document tool.
        
        Args:
            retrieval_ctx: Context object (mutable state).
            qa_manager: QAManager instance (for retrieval methods).
            document: The document object being queried.
            deduplicate_queries_func: Function for query deduplication.
            max_searches: Maximum number of allowed searches.
        """
        
        class SearchDocumentInput(BaseModel):
            query: str = Field(
                ...,
                description="Short, focused search query (5-20 words, max 80 chars). Target ONE specific topic."
            )
            keywords: Optional[List[str]] = Field(
                default=None,
                description="1-4 precise keywords (each 1-4 Chinese chars or 1 English word). e.g. ['评估指标', 'AT', 'Transformer']"
            )
            
            @field_validator('query')
            @classmethod
            def truncate_query(cls, v):
                return v[:80] if len(v) > 80 else v
            
            @field_validator('keywords')
            @classmethod
            def limit_keywords(cls, v):
                if v and len(v) > 4:
                    return v[:4]
                return v
        
        @tool(args_schema=SearchDocumentInput)
        async def search_document(query: str, keywords: Optional[List[str]] = None):
            """
            Search the document for relevant information.
            The returned content is RAW REFERENCE MATERIAL - read, understand, and synthesize into a proper answer.
            """
            logger.info(f"   🔍 [Search] query={query[:80]}{'...' if len(query) > 80 else ''}, keywords={keywords}")
            
            try:
                # 🛑 全局检索次数限制检查
                current_searches = retrieval_ctx.search_call_count
                if current_searches >= max_searches:
                    logger.info(f"   🛑 [Search Limit] Reached max searches ({max_searches}). Returning existing context.")
                    return (
                        f"⚠️ SEARCH LIMIT REACHED ({max_searches}/{max_searches}). \n"
                        f"You cannot perform more searches. Please synthesize your answer based on the information you have retrieved so far."
                    )
                
                retrieval_ctx.search_call_count += 1
                
                # 查询去重
                if retrieval_ctx.is_duplicate_query(query) and len(retrieval_ctx.tracks) > 0:
                    total = len(retrieval_ctx.tracks)
                    logger.info(f"   ⚡ [Search] Duplicate query detected, skipping retrieval (have {total} passages)")
                    return (
                        f"⚠️ This query is very similar to a previous search. No new retrieval needed. "
                        f"You already have {total} passages. Please answer based on existing information."
                    )
                
                # 记录查询
                retrieval_ctx.record_query([query])
                
                # 1. 扩充和合并查询词（Router + Agent）
                # 获取 Router 预分析的查询词（仅首轮有效）
                router_qs, router_kws = retrieval_ctx.consume_router_analysis()
                
                final_queries = [query]
                if router_qs:
                    final_queries.extend(router_qs)
                
                # 去重语义相似的查询
                final_queries = deduplicate_queries_func(final_queries)
                final_queries = final_queries[:4]  # 最多 4 条查询
                
                # 2. 扩充关键词
                # 如果 Agent 没有提供关键词，尝试使用 Router 的关键词作为兜底
                # 如果 Agent 提供了，则合并（Prioritize Agent's keywords, but add Router's unique ones）
                final_keywords = set(keywords or [])
                if router_kws:
                    final_keywords.update(router_kws)
                # 始终寻找兜底（如果上面 consume 返回了 None，这里尝试获取 fallback）
                if not final_keywords:
                    fallback_kws = retrieval_ctx.get_router_keywords_fallback()
                    final_keywords.update(fallback_kws)
                
                final_keywords_list = list(final_keywords)[:4]  # 最多 4 个关键词
                
                logger.info(f"   🧠 [Search Strategy] Queries: {len(final_queries)}, Keywords: {len(final_keywords_list)}")
                
                # 记录本次检索的详细参数（供状态显示）
                retrieval_ctx.set_search_details(final_queries, final_keywords_list)
                
                # 3. 执行高级混合检索
                new_chunks = await qa_manager._retrieve_advanced_chunks(
                    document=document,
                    semantic_queries=final_queries,
                    keywords=final_keywords_list,
                    max_chunks=20,  # 保持较高的召回量
                    selected_collection_ids=selected_collection_ids,
                )
                
                if not new_chunks:
                    return "No relevant information found in the document."

                # 注入来源名称到每个 chunk 的 metadata
                if collection_name_map:
                    for chunk in new_chunks:
                        meta = chunk.get("metadata") or {}
                        cid = meta.get("collection_id")
                        if cid and "source_name" not in meta:
                            meta["source_name"] = collection_name_map.get(str(cid), "未知来源")
                            chunk["metadata"] = meta

                # 4. 添加到上下文（去重）
                added_count = retrieval_ctx.add_chunks(new_chunks)
                
                if added_count == 0:
                    return "Found some information but it duplicates what you already have."
                
                # 5. 返回检索到的文本片段（为了节省 Token，只返回新增的或最相关的）
                # 每个 passage 最多 300 字，最多 3 个（平衡信息量与上下文开销）
                MAX_PASSAGE_CHARS = 300
                result_text = "\n\n".join([
                    f"[Passage {i+1}] ({(c.get('metadata') or {}).get('source_name', '未知来源')}, Score: {c.get('final_score', 0):.2f})\n{c.get('text', '')[:MAX_PASSAGE_CHARS]}"
                    for i, c in enumerate(new_chunks[:3])
                ])
                
                return f"Retrieval Successful. Found {len(new_chunks)} relevant passages:\n\n{result_text}"
                
            except Exception as e:
                logger.warning(f"⚠️ Search tool error: {e}")
                import traceback
                traceback.print_exc()
                return f"Error during search: {str(e)}"

        return search_document
