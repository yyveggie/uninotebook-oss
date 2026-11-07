"""
HyDE (Hypothetical Document Embeddings) 模块

原理：用 LLM 生成假设性答案，用答案的向量进行检索
优势：缩小 query 和 document 之间的语义鸿沟，提升检索准确率 20-40%
"""

from typing import Optional
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from utils.logger import get_logger
logger = get_logger(__name__)


class HyDEGenerator:
    """HyDE 假设性文档生成器"""
    
    def __init__(self, llm: Optional[ChatOpenAI] = None):
        """
        初始化 HyDE 生成器
        
        Args:
            llm: LangChain ChatOpenAI 实例（可选，默认从 llm_config 获取）
        """
        if llm:
            self.llm = llm
        else:
            # 🆕 使用统一配置
            from llm_service import get_llm_for_feature
            self.llm = get_llm_for_feature("hyde", temperature=0.3, max_tokens=500)
    
    async def generate_hypothetical_answer(
        self, 
        query: str, 
        doc_context: Optional[str] = None
    ) -> str:
        """
        生成假设性答案
        
        Args:
            query: 用户查询
            doc_context: 可选的文档上下文（如摘要）
            
        Returns:
            假设性答案文本
        """
        context_hint = ""
        if doc_context:
            context_hint = f"\n\n文档背景：{doc_context[:500]}"
        
        system_prompt = (
            "你是一个知识丰富的助手。请直接回答用户的问题，即使你不完全确定答案。"
            "生成一个可能的、合理的答案，就像这个答案来自一份专业文档一样。"
            "要求：\n"
            "1. 直接给出答案，不要说'我不确定'或'可能是'\n"
            "2. 使用专业、学术的语言风格\n"
            "3. 答案长度控制在 50-200 字\n"
            "4. 包含关键术语和概念"
            "5. 根据输入选择删除中英文"
            f"{context_hint}"
        )
        
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=query)
        ]
        
        try:
            response = await self.llm.ainvoke(messages)
            hypothetical_answer = response.content.strip()
            
            # 如果生成的答案太短，返回原始 query
            if len(hypothetical_answer) < 20:
                return query
            
            return hypothetical_answer
            
        except Exception as e:
            logger.warning(f"⚠️ HyDE 生成失败，使用原始查询: {e}")
            return query
    
    async def generate_hyde_queries(
        self, 
        query: str, 
        doc_context: Optional[str] = None,
        num_hypotheses: int = 1
    ) -> list[str]:
        """
        生成多个假设性查询（用于多路召回）
        
        Args:
            query: 原始查询
            doc_context: 文档上下文
            num_hypotheses: 生成的假设数量
            
        Returns:
            假设性查询列表（包含原始查询）
        """
        queries = [query]  # 始终包含原始查询
        
        if num_hypotheses >= 1:
            hyde_answer = await self.generate_hypothetical_answer(query, doc_context)
            if hyde_answer != query:
                queries.append(hyde_answer)
        
        return queries


# 全局单例
_hyde_generator: Optional[HyDEGenerator] = None


def get_hyde_generator(llm: Optional[ChatOpenAI] = None) -> HyDEGenerator:
    """获取 HyDE 生成器单例"""
    global _hyde_generator
    if _hyde_generator is None:
        _hyde_generator = HyDEGenerator(llm)
    return _hyde_generator
