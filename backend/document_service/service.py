"""
文档服务主类
整合所有文档处理、内容生成、问答功能
"""
from typing import Optional, List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI
import httpx

from config import settings
from db_models import Document
from llm_service import get_llm_for_feature, get_model_config

from .core import DocumentProcessor
from .generator import ContentGenerator
from .qa import QAManager
from utils.logger import get_logger
logger = get_logger(__name__)



class DocumentService:
    """Request/task-scoped service. Database sessions are never shared."""

    def __init__(self, db: AsyncSession = None):
        self.db = db
        self._processor = None
        self._generator = None
        self._qa_manager = None

    @property
    def model(self):
        return get_model_config("qwen")["model"]

    @property
    def lc_client(self):
        return get_llm_for_feature("doc_general", temperature=0.3)

    @property
    def doc_parsing_client(self):
        return get_llm_for_feature("doc_parsing", temperature=0.3)

    @property
    def processor(self):
        if self._processor is None:
            self._processor = DocumentProcessor(lc_client=None, db=self.db)
        return self._processor

    @property
    def generator(self):
        if self._generator is None:
            self._generator = ContentGenerator(lc_client=self.lc_client, db=self.db)
        return self._generator

    @property
    def qa_manager(self):
        if self._qa_manager is None:
            self._qa_manager = QAManager(db=self.db, model=self.model)
        return self._qa_manager
    
    # ===== 文档处理核心功能（委托给 DocumentProcessor） =====
    
    async def extract_structured_elements_from_file(self, file_content: bytes, filename: str):
        """从文档提取结构化元素 - 支持多种格式（PDF, DOCX, TXT, MD, HTML等）"""
        return await self.processor.extract_structured_elements_from_file(file_content, filename)
    
    async def create_document_from_url(self, url: str, user_id: int) -> Document:
        """从URL抓取内容并创建文档"""
        try:
            import trafilatura
        except ImportError:
            raise ImportError("请安装 trafilatura: pip install trafilatura")

        # 1. 抓取网页（SSRF 防护：地址校验 + 逐跳校验重定向；阻塞 I/O 放线程池）
        from starlette.concurrency import run_in_threadpool
        from utils.safe_fetch import fetch_html

        downloaded = await run_in_threadpool(fetch_html, url)
        if downloaded is None:
            raise ValueError(f"无法访问 URL: {url}")
            
        # 2. 提取正文和元数据
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=True)
        if not text or len(text.strip()) < 50:
            raise ValueError(f"无法从 URL 提取有效内容 (内容过短或为空): {url}")
            
        metadata = trafilatura.extract_metadata(downloaded)
        title = metadata.title if metadata and metadata.title else "未命名网页"
        
        # 3. 调用 process_document 处理
        # 构造一个简单的结构化元素列表，模拟文件解析结果
        structured_elements = [{
            "type": "NarrativeText",
            "element_id": "url_content_0",
            "text": text,
            "metadata": {
                "source": url,
                "title": title,
                "author": metadata.author if metadata else None,
                "date": metadata.date if metadata else None
            }
        }]
        
        # 生成一个伪造的文件名，确保唯一性避免覆盖，或者让 process_document 处理
        # 这里我们加上时间戳
        import time
        safe_title = "".join([c for c in title if c.isalnum() or c in (' ', '-', '_')]).strip()
        if not safe_title:
            safe_title = "webpage"
        filename = f"{safe_title}_{int(time.time())}.txt"
        
        # 调用核心处理逻辑
        return await self.process_document(
            filename=filename,
            content=text,
            file_type="url",
            user_id=user_id,
            structured_elements=structured_elements
        )

    async def process_document(
        self,
        filename: str,
        content: str,
        file_type: str,
        user_id: int,
        structured_elements: Optional[List[Dict[str, Any]]] = None
    ) -> Document:
        """处理文档"""
        return await self.processor.process_document(
            filename, content, file_type, user_id, structured_elements
        )
    
    async def get_document_by_id(self, document_id: int) -> Optional[Document]:
        """获取文档"""
        return await self.processor.get_document_by_id(document_id)
    
    async def list_documents(self, user_id: int, limit: int = 50) -> List[Document]:
        """列出指定用户的文档（user_id 必填）"""
        return await self.processor.list_documents(user_id=user_id, limit=limit)
    
    async def delete_document(self, document_id: int, user_id: Optional[int] = None):
        """删除文档"""
        return await self.processor.delete_document(document_id, user_id=user_id)
    
    # ===== 内容生成功能（委托给 ContentGenerator） =====
    

    
    async def generate_flashcards_from_document(
        self,
        document: Document,
        num_cards: int = 10,
        user_id: Optional[int] = None,
        difficulty: Optional[str] = None,
        requirements: Optional[str] = None,
    ):
        """生成学习闪卡"""
        return await self.generator.generate_flashcards(
            document=document,
            num_cards=num_cards,
            user_id=user_id,
            difficulty=difficulty,
            requirements=requirements,
        )
    
    async def generate_quiz_from_document(
        self,
        document: Document,
        num_questions: int = 10,
        user_id: Optional[int] = None,
        difficulty: Optional[str] = None,
        requirements: Optional[str] = None,
    ):
        """生成测验题目"""
        return await self.generator.generate_quiz(
            document=document,
            num_questions=num_questions,
            user_id=user_id,
            difficulty=difficulty,
            requirements=requirements,
        )
    
    async def generate_mindmap_from_document(
        self,
        document: Document,
        user_id: Optional[int] = None
    ):
        """生成思维导图"""
        return await self.generator.generate_mindmap(document, user_id)
    
    async def generate_highlights_from_document(
        self,
        document: Document,
        user_id: Optional[int] = None,
        num_points: Optional[int] = None,
        difficulty: Optional[str] = None,
        requirements: Optional[str] = None,
    ):
        """生成重点提炼"""
        return await self.generator.generate_highlights(
            document=document,
            user_id=user_id,
            num_points=num_points,
            difficulty=difficulty,
            requirements=requirements,
        )
 
    async def expand_mindmap_node(
        self,
        document: Document,
        node_id: str,
        current_mindmap: Dict[str, Any],
        user_id: Optional[int] = None
    ):
        """扩展思维导图节点"""
        return await self.generator.expand_mindmap_node(document, node_id, current_mindmap, user_id)
    
    # ===== 问答功能（基于 LangChain 1.0） =====
    # 统一的问答接口，支持流式和非流式，使用中间件系统
    
    async def ask_document(self, *args, **kwargs):
        """
        统一的文档问答接口
        
        参数：
            - max_iterations: 最大迭代次数（默认3）
            - stream: 是否流式响应（默认False）
            - db_question: 存入数据库的问题文本（可选）
        """
        return await self.qa_manager.ask_document(*args, **kwargs)

    def ask_document_stream(self, *args, **kwargs):
        """
        统一的文档问答接口（流式）
        """
        return self.qa_manager.ask_document_stream(*args, **kwargs)


def get_document_service(db: AsyncSession = None) -> DocumentService:
    """创建当前请求/任务的服务；LLM 客户端由模型工厂按凭据与事件循环缓存。"""
    return DocumentService(db=db)

