"""
文档服务模块

模块化结构：
- schema.py: Pydantic 数据模型
- utils.py: 工具函数（哈希、文本清理）
- core.py: 核心文档处理（上传、解析、摘要生成）
- generator.py: 内容生成（闪卡、测验、思维导图、学习路径、重点提炼）
- retrieval_strategy.py: 检索策略（全局检索、局部检索、自定义检索）
- memory.py: 会话记忆管理（Redis缓存 + PostgreSQL持久化）
- service.py: 统一服务类（整合所有功能）

使用方式：
    from document_service import DocumentService
    
    service = DocumentService(db=db_session)
    document = await service.process_document(...)
"""

from .service import DocumentService, get_document_service
from .core import DocumentProcessor
from .generator import ContentGenerator
from .utils import calculate_file_hash, clean_text_newlines
from .retrieval_strategy import RetrievalStrategy, RetrievalConfig, RetrievalMode
from .memory import ConversationMemoryManager, ConversationMemory
from .generators.interview import InterviewManager, InterviewSession, initialize_interview_manager

from .schema import (
    KeyTerm,
    DocumentSummaryResponse,
    DocumentTitleResponse,
    QuizQuestion,
    QuizResponse,
    Flashcard,
    FlashcardsResponse,
    DocumentLearningNode,
    DocumentLearningPathResponse,
    MindMapNode,
    MindMapResponse,
    KnowledgePoint,
    KnowledgePointsResponse,
    InterviewQuestion,
    InterviewEvaluation,
    InterviewSummary,
)

__all__ = [
    # 主服务类
    'DocumentService',
    # 子模块
    'DocumentProcessor',
    'ContentGenerator',
    # 检索策略
    'RetrievalStrategy',
    'RetrievalConfig',
    'RetrievalMode',
    # Memory 管理
    'ConversationMemoryManager',
    'ConversationMemory',
    # 面试功能
    'InterviewManager',
    'InterviewSession',
    'initialize_interview_manager',

    # 工具函数
    'calculate_file_hash',
    'clean_text_newlines',
    # Pydantic 模型
    'KeyTerm',
    'DocumentSummaryResponse',
    'DocumentTitleResponse',
    'QuizQuestion',
    'QuizResponse',
    'Flashcard',
    'FlashcardsResponse',
    'DocumentLearningNode',
    'DocumentLearningPathResponse',
    'MindMapNode',
    'MindMapResponse',
    'KnowledgePoint',
    'KnowledgePointsResponse',
    'InterviewQuestion',
    'InterviewEvaluation',
    'InterviewSummary',
]

