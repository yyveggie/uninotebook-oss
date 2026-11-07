"""
文档检索策略统一管理模块

提供不同场景下的智能检索策略：
- 全局检索：用于思维导图生成、文档摘要等
- 局部检索：用于节点扩展、特定主题深入
- 上下文检索：用于问答、对话等
"""

from typing import Dict, Any, Optional, Tuple
from enum import Enum
from utils.logger import get_logger
logger = get_logger(__name__)


class RetrievalMode(Enum):
    """检索模式枚举"""
    GLOBAL = "global"           # 全局检索（宏观，覆盖整个文档）
    LOCAL = "local"             # 局部检索（微观，聚焦特定主题）
    CONTEXTUAL = "contextual"   # 上下文检索（基于对话历史）


class RetrievalStrategy:
    """文档检索策略统一管理类"""
    
    @staticmethod
    async def retrieve_for_mindmap_generation(
        document,
        vector_store_manager,
        top_k: int = 8,
        fetch_k: int = 32,
        lambda_mult: float = 0.5,
        chunk_max_chars: int = 900
    ) -> Tuple[str, int]:
        """
        思维导图生成的全局检索策略
        
        策略：使用文档摘要作为查询，检索最重要的内容片段
        
        参数:
            document: 文档对象
            vector_store_manager: 向量存储管理器
            top_k: 返回的chunk数量（默认10个）
            fetch_k: MMR候选集大小（默认40个）
            lambda_mult: MMR多样性参数（0-1，默认0.5）
            chunk_max_chars: 每个chunk的最大字符数（默认1000）
            
        返回:
            (retrieved_content, chunk_count): 检索到的内容和chunk数量
        """
        logger.info(f"🔍 [全局检索] 思维导图生成检索")
        
        # 1. 构建全局查询
        if document.summary:
            global_query = f"{document.filename}\n\n{document.summary}"
            logger.info(f"   📋 使用文档摘要作为全局查询")
        else:
            global_query = document.original_text[:2000]
            logger.warning(f"   ⚠️ 文档无摘要，使用开头2000字符作为查询")
        
        # 2. 执行全局检索
        logger.info(f"   参数: top_k={top_k}, fetch_k={fetch_k}, lambda={lambda_mult}")
        relevant_chunks = await vector_store_manager.search_with_mmr(
            collection_id=document.vector_store_id,
            query=global_query,
            top_k=top_k,
            fetch_k=fetch_k,
            lambda_mult=lambda_mult
        )
        
        # 3. 构建检索内容
        retrieved_content = "\n\n---\n\n".join([
            f"[片段 {i+1}]\n{chunk['text'][:chunk_max_chars]}"
            for i, chunk in enumerate(relevant_chunks)
        ])
        
        total_chars = len(retrieved_content)
        logger.info(f"   ✅ 正在检索: {len(relevant_chunks)} 个片段, {total_chars} 字符")
        
        return retrieved_content, len(relevant_chunks)
    
    @staticmethod
    async def retrieve_for_node_expansion(
        document,
        vector_store_manager,
        node_path: str,
        target_node: Dict[str, Any],
        top_k: int = 4,
        fetch_k: int = 16,
        lambda_mult: float = 0.5,
        chunk_max_chars: int = 700
    ) -> Tuple[str, int]:
        """
        节点扩展的局部检索策略
        
        策略：结合文档摘要和节点上下文，检索相关内容
        
        参数:
            document: 文档对象
            vector_store_manager: 向量存储管理器
            node_path: 节点路径字符串
            target_node: 目标节点信息
            top_k: 返回的chunk数量（默认5个）
            fetch_k: MMR候选集大小（默认20个）
            lambda_mult: MMR多样性参数（0-1，默认0.5）
            chunk_max_chars: 每个chunk的最大字符数（默认800）
            
        返回:
            (retrieved_content, chunk_count): 检索到的内容和chunk数量
        """
        logger.info(f"🔍 [局部检索] 节点扩展检索")
        logger.info(f"   节点: {target_node['title']}")
        
        # 1. 构建局部查询（全局+局部）
        if document.summary:
            global_query = f"{document.filename}\n节点路径: {node_path}\n\n{document.summary}"
            logger.info(f"   📋 使用摘要+节点路径构建查询")
        else:
            global_query = f"{target_node['title']} {target_node.get('description', '')}\n{document.original_text[:1000]}"
            logger.warning(f"   ⚠️ 使用节点信息+文档开头构建查询")
        
        # 2. 执行局部检索
        logger.info(f"   参数: top_k={top_k}, fetch_k={fetch_k}, lambda={lambda_mult}")
        relevant_chunks = await vector_store_manager.search_with_mmr(
            collection_id=document.vector_store_id,
            query=global_query,
            top_k=top_k,
            fetch_k=fetch_k,
            lambda_mult=lambda_mult
        )
        
        # 3. 构建检索内容
        retrieved_content = "\n\n---\n\n".join([
            f"[片段 {i+1}]\n{chunk['text'][:chunk_max_chars]}"
            for i, chunk in enumerate(relevant_chunks)
        ])
        
        total_chars = len(retrieved_content)
        logger.info(f"   ✅ 正在检索: {len(relevant_chunks)} 个片段, {total_chars} 字符")
        
        return retrieved_content, len(relevant_chunks)
    

    
    @staticmethod
    async def retrieve_for_highlights(
        document,
        vector_store_manager,
        top_k: int = 9,
        fetch_k: int = 36,
        lambda_mult: float = 0.3,
        chunk_max_chars: int = 700
    ) -> Tuple[str, int]:
        """
        文档要点提取的检索策略
        
        策略：需要更多样化的内容（较低的lambda_mult），覆盖文档各个方面
        
        参数:
            document: 文档对象
            vector_store_manager: 向量存储管理器
            top_k: 返回的chunk数量（默认12个）
            fetch_k: MMR候选集大小（默认50个）
            lambda_mult: MMR多样性参数（0-1，默认0.3，更注重多样性）
            chunk_max_chars: 每个chunk的最大字符数（默认800）
            
        返回:
            (retrieved_content, chunk_count): 检索到的内容和chunk数量
        """
        logger.info(f"🔍 [全局检索] 文档要点提取检索")
        
        # 1. 构建查询
        if document.summary:
            query = f"{document.filename}\n\n{document.summary}"
            logger.info(f"   📋 使用文档摘要作为查询")
        else:
            query = document.original_text[:2000]
            logger.warning(f"   ⚠️ 使用文档开头作为查询")
        
        # 2. 执行检索
        logger.info(f"   参数: top_k={top_k}, fetch_k={fetch_k}, lambda={lambda_mult} (高多样性)")
        relevant_chunks = await vector_store_manager.search_with_mmr(
            collection_id=document.vector_store_id,
            query=query,
            top_k=top_k,
            fetch_k=fetch_k,
            lambda_mult=lambda_mult
        )
        
        # 3. 构建内容
        retrieved_content = "\n\n---\n\n".join([
            f"[片段 {i+1}]\n{chunk['text'][:chunk_max_chars]}"
            for i, chunk in enumerate(relevant_chunks)
        ])
        
        total_chars = len(retrieved_content)
        logger.info(f"   ✅ 正在检索: {len(relevant_chunks)} 个片段, {total_chars} 字符")
        
        return retrieved_content, len(relevant_chunks)
    
    @staticmethod
    async def retrieve_with_custom_params(
        document,
        vector_store_manager,
        query: Optional[str] = None,
        top_k: int = 4,
        fetch_k: int = 16,
        lambda_mult: float = 0.5,
        chunk_max_chars: int = 900,
        mode: RetrievalMode = RetrievalMode.GLOBAL
    ) -> Tuple[str, int]:
        """
        自定义参数的检索策略
        
        参数:
            document: 文档对象
            vector_store_manager: 向量存储管理器
            query: 自定义查询（如果为None，则使用文档摘要）
            top_k: 返回的chunk数量
            fetch_k: MMR候选集大小
            lambda_mult: MMR多样性参数（0-1）
            chunk_max_chars: 每个chunk的最大字符数
            mode: 检索模式
            
        返回:
            (retrieved_content, chunk_count): 检索到的内容和chunk数量
        """
        logger.info(f"🔍 [自定义检索] 模式={mode.value}")
        
        # 1. 构建查询
        if query is None:
            if document.summary:
                query = f"{document.filename}\n\n{document.summary}"
                logger.info(f"   📋 使用文档摘要作为默认查询")
            else:
                query = document.original_text[:2000]
                logger.warning(f"   ⚠️ 使用文档开头作为默认查询")
        else:
            logger.info(f"   📝 使用自定义查询: {query[:100]}...")
        
        # 2. 执行检索
        logger.info(f"   参数: top_k={top_k}, fetch_k={fetch_k}, lambda={lambda_mult}")
        relevant_chunks = await vector_store_manager.search_with_mmr(
            collection_id=document.vector_store_id,
            query=query,
            top_k=top_k,
            fetch_k=fetch_k,
            lambda_mult=lambda_mult
        )
        
        # 3. 构建内容
        retrieved_content = "\n\n---\n\n".join([
            f"[片段 {i+1}]\n{chunk['text'][:chunk_max_chars]}"
            for i, chunk in enumerate(relevant_chunks)
        ])
        
        total_chars = len(retrieved_content)
        logger.info(f"   ✅ 正在检索: {len(relevant_chunks)} 个片段, {total_chars} 字符")
        
        return retrieved_content, len(relevant_chunks)


# 预设参数配置（优化版 v2）
class RetrievalConfig:
    """检索参数预设配置（优化多样性）"""
    
    # 思维导图生成（覆盖广，多样性中等）
    MINDMAP_GENERATION = {
        "top_k": 10,           # 🆕 增加chunk数量（8 -> 10）
        "fetch_k": 60,         # 🆕 更大候选池（32 -> 60）
        "lambda_mult": 0.35,   # 🆕 更强多样性（0.5 -> 0.35）
        "chunk_max_chars": 1500 # 🆕 增加字符限制（900 -> 1500）
    }
    
    # 节点扩展（聚焦深，中等数量）
    NODE_EXPANSION = {
        "top_k": 5,            # 🆕 略增（4 -> 5）
        "fetch_k": 30,         # 🆕 更大候选池（16 -> 30）
        "lambda_mult": 0.4,    # 🆕 增强多样性（0.5 -> 0.4）
        "chunk_max_chars": 1200 # 🆕 增加字符限制（700 -> 1200）
    }
    

    
    # 文档要点（多样性优先）- 最重要的改进！
    HIGHLIGHTS = {
        "top_k": 12,           # 🆕 显著增加（9 -> 12）
        "fetch_k": 72,         # 🆕 超大候选池（36 -> 72）
        "lambda_mult": 0.25,   # 🆕 极强多样性（0.3 -> 0.25）
        "chunk_max_chars": 1200 # 🆕 增加字符限制（700 -> 1200）
    }
    
    # 闪卡生成（覆盖广，多样性高）
    FLASHCARDS = {
        "top_k": 10,           # 🆕 增加（7 -> 10）
        "fetch_k": 60,         # 🆕 更大候选池（28 -> 60）
        "lambda_mult": 0.3,    # 🆕 更强多样性（0.4 -> 0.3）
        "chunk_max_chars": 1200 # 🆕 增加字符限制（800 -> 1200）
    }
    
    # 测验生成（覆盖广，多样性高）
    QUIZ = {
        "top_k": 10,           # 🆕 增加（7 -> 10）
        "fetch_k": 60,         # 🆕 更大候选池（28 -> 60）
        "lambda_mult": 0.3,    # 🆕 更强多样性（0.4 -> 0.3）
        "chunk_max_chars": 1200 # 🆕 增加字符限制（800 -> 1200）
    }
    
    # QA 问答（多样性高，需要完整上下文）
    QA = {
        "top_k": 10,
        "fetch_k": 60,
        "lambda_mult": 0.3,    # 强调多样性
        "max_chunks": 10       # 最终返回的 chunk 数量
    }

