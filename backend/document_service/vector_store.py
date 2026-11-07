"""
通用向量检索 - 适用于所有文档类型
使用 Qwen text-embedding-v4 云端 Embeddings（异步，支持高并发）

切块策略：统一递归文本切分（目标 600 字符，上限 900 字符，重叠 120 字符）
检索策略：向量 + BM25 混合检索，RRF 融合排序
"""
import asyncio
import hashlib
import json
import threading
import chromadb
from chromadb.config import Settings
from typing import List, Dict, Any, Optional, Tuple
import uuid
import re
import time
import numpy as np
from openai import AsyncOpenAI, BadRequestError
from concurrent.futures import ThreadPoolExecutor
from rank_bm25 import BM25Okapi

from cache_service import cache_get_bytes, cache_set_bytes
from config import settings
from utils.logger import get_logger
logger = get_logger(__name__)


class EmbeddingReindexRequired(ValueError):
    pass


class VectorStoreManager:
    """通用向量存储管理器 - 工业级异步实现"""
    
    def __init__(self):
        """初始化"""
        if settings.chroma_server_host:
            # Server 模式 (HTTP Client) - 适用于多进程/分布式部署
            logger.info(f"🔌 连接到独立 ChromaDB 服务: {settings.chroma_server_host}:{settings.chroma_server_port}")
            self.client = chromadb.HttpClient(
                host=settings.chroma_server_host,
                port=settings.chroma_server_port,
                ssl=settings.chroma_server_ssl,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
        else:
            # Embedded 模式 (Persistent Client) - 适用于单进程开发与本地版
            # 本地版落到数据目录，避免依赖进程的当前工作目录
            if settings.is_local_mode:
                chroma_path = str(settings.data_dir / "chroma_db")
            else:
                chroma_path = "./chroma_db"
            logger.info("📂 使用本地嵌入式 ChromaDB (%s)", chroma_path)
            self.client = chromadb.PersistentClient(
                path=chroma_path,
                settings=Settings(
                    anonymized_telemetry=False,
                    allow_reset=True
                )
            )
        
        # 🆕 创建专用线程池（隔离ChromaDB同步调用）
        # 🚀 [2026-02-05] 优化：提高并发度 8 -> 16
        self._chroma_executor = ThreadPoolExecutor(
            max_workers=16,
            thread_name_prefix="chroma-worker-"
        )
        logger.info("🔧 ChromaDB 线程池已创建（16 workers）")
        
        # 🆕 内存 BM25 索引缓存 {collection_id: (bm25_obj, doc_ids, doc_metadatas, chunks_text, count)}
        self._bm25_cache: Dict[str, Tuple[BM25Okapi, List[str], List[Dict], List[str], int]] = {}
        # 🆕 BM25 缓存检查时间戳 {collection_id: last_check_time}
        self._bm25_cache_check_time: Dict[str, float] = {}
        # 🆕 缓存有效期（秒）- 在此期间内不重新检查文档数量
        self._bm25_cache_ttl = 300  # 300秒内认为缓存有效，不检查（数据更新时会主动清理）
        
        # Embedding 限制
        self.embedding_max_tokens = 8192
        # ⚠️ 中文等多字节语言近似 1 token ≈ 1 字符，为保险取 6000 字符上限
        self.embedding_max_chars = 6000
        self.embedding_min_chars = 1500  # 最低字符限制

        # 🆕 延迟初始化 Embeddings 客户端（避免事件循环冲突）
        # AsyncOpenAI 底层 httpx 客户端绑定创建时所在的事件循环，因此按线程隔离：
        # Web 主线程与后台任务线程各持一份，互不干扰。
        self._embeddings_client_local = threading.local()
        # 客户端创建时使用的凭据指纹，用于在用户改 Key 后自动重建
        self._embeddings_fingerprint = None

        self.embedding_dimension = settings.embedding_dimensions

        logger.info("🔄 Embeddings 配置预加载 (延迟初始化模式)...")
        try:
            config = self.embedding_config

            logger.info(f"✅ Embeddings 配置预加载成功")
            logger.info(f"   模型: {self.embedding_model_name}")
            logger.info(f"   维度: {self.embedding_dimension}")
            logger.info(f"   延迟初始化模式 ✓ (客户端将在首次异步调用时创建)")

        except Exception as e:
            # 本地版启动时用户可能尚未填写 Key，此处不能硬失败，
            # 否则应用根本起不来、也就没机会去设置页填 Key。
            if settings.is_local_mode:
                logger.warning(f"⚠️ Embeddings 配置尚未就绪（本地模式，可在设置页填写）: {e}")
            else:
                logger.error(f"❌ Embeddings 配置加载失败: {e}")
                raise Exception(f"无法加载 Embeddings 配置，请检查 DASHSCOPE_API_KEY 配置: {e}")

    @property
    def embedding_config(self):
        """
        每次访问时重新解析嵌入模型配置。

        不缓存的原因：本地版用户可在运行时于设置页填写/更换 API Key，
        若像原实现那样在 __init__ 固化配置，改 Key 将不会生效。
        """
        from llm_service import get_embedding_config
        return get_embedding_config("vector_store")

    @property
    def embeddings_client(self):
        return self._embedding_client(self.embedding_config)

    @property
    def embedding_model_name(self):
        return self.embedding_config["model"]

    @staticmethod
    def embedding_signature(config):
        identity = [str(config.get("base_url") or "").rstrip("/"), config["model"], config.get("dimensions")]
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()

    def ensure_collection_compatible(self, collection, config=None):
        config = config or self.embedding_config
        metadata = collection.metadata or {}
        signature = metadata.get("embedding_signature")
        # Legacy collections were built with the environment's default model.
        legacy_changed = not signature and (
            config["model"] != settings.dashscope_embedding_model
            or config.get("base_url") != settings.dashscope_base_url
        )
        if legacy_changed or (signature and signature != self.embedding_signature(config)):
            raise EmbeddingReindexRequired(
                "EMBEDDING_REINDEX_REQUIRED：向量模型或端点已更改，请在设置页重建文档索引"
            )

    def _embedding_client(self, config):
        """
        🆕 延迟初始化 Embeddings 客户端（按线程隔离 + 凭据变更自动重建）
        """
        fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        client = getattr(self._embeddings_client_local, "client", None)
        if client is None or getattr(self._embeddings_client_local, "fingerprint", None) != fingerprint:
            if not config.get("api_key"):
                raise RuntimeError(
                    "Embeddings API Key 未配置，请在设置中填写（或设置 DASHSCOPE_API_KEY）"
                )
            logger.info("🔧 [延迟初始化] 创建 AsyncOpenAI Embeddings 客户端...")
            client = AsyncOpenAI(
                api_key=config["api_key"],
                base_url=config["base_url"],
            )
            self._embeddings_client_local.client = client
            self._embeddings_client_local.fingerprint = fingerprint
            logger.info("✅ Embeddings 客户端已创建")
        return client

    def reset_embeddings_client(self):
        """
        🆕 重置 Embeddings 客户端（用于 Celery 等需要在新事件循环中运行的场景）

        只重置**当前线程**的客户端，不影响其他线程（如 Web 主线程）。
        """
        if getattr(self._embeddings_client_local, "client", None) is not None:
            logger.info("🔄 重置 Embeddings 客户端...")
            self._embeddings_client_local.client = None


    async def _run_in_executor(self, func, *args, **kwargs):
        """
        🆕 在线程池中执行同步函数（避免阻塞事件循环）
        
        Args:
            func: 同步函数
            *args, **kwargs: 函数参数
            
        Returns:
            函数执行结果
        """
        loop = asyncio.get_running_loop()
        if kwargs:
            # 如果有kwargs，需要用lambda包装
            return await loop.run_in_executor(
                self._chroma_executor,
                lambda: func(*args, **kwargs)
            )
        else:
            return await loop.run_in_executor(
                self._chroma_executor,
                func,
                *args
            )
    
    def _tokenize(self, text: str) -> List[str]:
        """
        🆕 智能分词 (优化版 - 使用 Jieba)
        
        策略：
        - 英文：按空格分词，保留短语中的各个单词
        - 中文：使用 Jieba 分词，保留语义词组（如 "机器学习", "神经网络"）
        - 混合文本：分别处理后合并
        """
        if not text:
            return []
        
        text = text.lower().strip()
        tokens = []
        
        # 🔧 优化：使用 Jieba 进行中文分词
        try:
            import jieba
            # 使用精确模式分词
            jieba_tokens = list(jieba.cut(text, cut_all=False))
            
            for token in jieba_tokens:
                token = token.strip()
                if not token:
                    continue
                # 过滤单字符（除非是英文单词）
                if len(token) == 1 and '\u4e00' <= token <= '\u9fff':
                    continue  # 跳过中文单字
                # 过滤纯标点
                if re.match(r'^[^\w\u4e00-\u9fff]+$', token):
                    continue
                tokens.append(token.lower())
            
            return tokens
            
        except ImportError:
            logger.warning("⚠️ Jieba 未安装，回退到简单分词")
            # 回退到原来的简单分词
            parts = re.findall(r'[a-zA-Z0-9]+|[\u4e00-\u9fff]+', text)
            
            for part in parts:
                if re.match(r'^[a-zA-Z0-9]+$', part):
                    tokens.append(part.lower())
                else:
                    # 中文：按字符拆分（unigram）
                    tokens.extend(list(part))
            
            return tokens

    def _get_or_build_bm25(self, collection_id: str) -> Optional[Tuple[BM25Okapi, List[str], List[Dict], List[str]]]:
        """
        获取或构建 BM25 索引（带缓存和时间戳优化）
        
        🆕 优化策略：
        1. 如果缓存存在且在 TTL 内，直接返回缓存（不检查文档数量）
        2. 如果超过 TTL，检查文档数量决定是否重建
        3. 避免每次检索都查询 ChromaDB
        """
        import time
        now = time.time()
        
        # 🆕 快速路径：缓存存在且在 TTL 内，直接返回
        if collection_id in self._bm25_cache:
            last_check = self._bm25_cache_check_time.get(collection_id, 0)
            if now - last_check < self._bm25_cache_ttl:
                # 缓存有效，直接返回（不检查文档数量）
                bm25, ids, metadatas, documents, _ = self._bm25_cache[collection_id]
                return bm25, ids, metadatas, documents
            
        try:
            # 获取集合中的所有文档
            collection = self.client.get_collection(collection_id)
            data = collection.get(include=["documents", "metadatas"])
            
            if not data or not data['documents']:
                return None
                
            current_count = len(data['documents'])
            
            # 更新检查时间
            self._bm25_cache_check_time[collection_id] = now
            
            # 缓存失效检查：如果文档数量变了，或者缓存不存在，则重建
            if collection_id in self._bm25_cache:
                bm25, ids, metadatas, documents, cached_count = self._bm25_cache[collection_id]
                if cached_count == current_count:
                    # 缓存有效
                    return bm25, ids, metadatas, documents
                else:
                    logger.info(f"🔄 检测到文档变更 (Cache={cached_count}, Now={current_count})，重建 BM25 索引...")
            
            raw_documents = data['documents']
            raw_ids = data['ids']
            raw_metadatas = data['metadatas'] or ([{}] * len(raw_ids))
            
            # 预过滤参考文献和低质量 chunk（避免检索时逐条判断）
            documents = []
            ids = []
            metadatas = []
            for doc, doc_id, meta in zip(raw_documents, raw_ids, raw_metadatas):
                if not self._is_reference_like(doc) and not self._is_low_quality_chunk(doc):
                    documents.append(doc)
                    ids.append(doc_id)
                    metadatas.append(meta)

            if not documents:
                documents, ids, metadatas = raw_documents, raw_ids, raw_metadatas

            logger.info(f"🔨 构建 BM25 索引: {collection_id} ({len(documents)}/{len(raw_documents)} docs after filtering)...")
            
            # 分词
            tokenized_corpus = [self._tokenize(doc) for doc in documents]
            
            # 构建索引
            bm25 = BM25Okapi(tokenized_corpus)
            
            # 存入缓存 (包含 count)
            self._bm25_cache[collection_id] = (bm25, ids, metadatas, documents, len(documents))
            
            logger.info(f"✅ BM25 索引构建完成")
            return (bm25, ids, metadatas, documents)
            
        except Exception as e:
            logger.error(f"❌ 构建 BM25 索引失败: {e}")
            return None

    def _fallback_keyword_match(self, collection_id: str, keywords: List[str], top_k: int) -> List[Dict]:
        """
        BM25 失败时的回退：优先复用 BM25 缓存数据，避免重复加载
        """
        try:
            # 优先从 BM25 缓存获取文档数据，避免二次加载全量文档
            if collection_id in self._bm25_cache:
                _, ids, metadatas, documents, _ = self._bm25_cache[collection_id]
            else:
                collection = self.client.get_collection(collection_id)
                data = collection.get(include=["documents", "metadatas"])
                if not data or not data['documents']:
                    return []
                documents = data['documents']
                ids = data['ids']
                metadatas = data['metadatas'] or ([{}] * len(ids))
            
            results = []
            for i, doc in enumerate(documents):
                doc_normalized = doc.lower().replace('-\n', '').replace('\n', ' ')

                if self._is_reference_like(doc):
                    continue
                if self._is_low_quality_chunk(doc):
                    continue
                
                hit_count = 0
                for kw in keywords:
                    if kw.lower() in doc_normalized:
                        hit_count += 1
                
                if hit_count > 0:
                    results.append({
                        "id": ids[i],
                        "text": doc,
                        "metadata": metadatas[i],
                        "score": hit_count,
                        "quality_score": metadatas[i].get('quality_score', 0.5)
                    })
            
            results.sort(key=lambda x: x['score'], reverse=True)
            return results[:top_k * 2]
            
        except Exception as e:
            logger.warning(f"⚠️ 回退匹配也失败: {e}")
            return []
    
    def _rrf_fusion(
        self, 
        vector_results: List[Dict], 
        bm25_results: List[Dict], 
        k: int = 60
    ) -> List[Dict]:
        """
        Reciprocal Rank Fusion (RRF) 算法
        
        Score = 1 / (k + rank)
        """
        fused_scores = {}
        all_docs = {}  # id -> doc_info
        
        # 处理向量结果
        for rank, doc in enumerate(vector_results):
            doc_id = doc['id']
            if doc_id not in all_docs:
                all_docs[doc_id] = doc
            
            # 倒数排名分数
            score = 1 / (k + rank + 1)
            fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + score
            
        # 处理 BM25 结果
        for rank, doc in enumerate(bm25_results):
            doc_id = doc['id']
            if doc_id not in all_docs:
                all_docs[doc_id] = doc
                # 补充一些向量结果特有的字段默认值
                if 'vector_score' not in all_docs[doc_id]:
                    all_docs[doc_id]['vector_score'] = 0.0
            
            # 倒数排名分数
            score = 1 / (k + rank + 1)
            fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + score
        
        # 排序
        sorted_doc_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)
        
        # 格式化输出
        final_results = []
        for doc_id in sorted_doc_ids:
            doc = all_docs[doc_id]
            doc['final_score'] = fused_scores[doc_id]  # 使用 RRF 分数
            doc['rrf_score'] = fused_scores[doc_id]
            final_results.append(doc)
            
        return final_results

    def _recursive_split_text(
        self,
        text: str,
        target_size: int = 600,
        max_size: int = 900,
        overlap: int = 120
    ) -> List[str]:
        """
        通用递归文本切分器 —— 适用于任何文档类型
        
        将任意长度的文本统一切分为目标大小的 chunk，相邻 chunk 带重叠。
        按分隔符优先级递归切分：段落 → 换行 → 句子 → 空格 → 硬切。
        
        Args:
            text: 待切分的文本
            target_size: 目标 chunk 大小（字符）
            max_size: chunk 最大大小（字符）
            overlap: 相邻 chunk 重叠字符数
            
        Returns:
            切分后的文本列表，每段 target_size ~ max_size 字符，带 overlap 重叠
        """
        if not text or not text.strip():
            return []
        
        text = text.strip()
        
        # 文本足够短，直接返回
        if len(text) <= max_size:
            return [text]
        
        # ---- 第一步：用最佳分隔符将文本切成片段 ----
        separators = ['\n\n', '\n', '。', '！', '？', '. ', '! ', '? ', ' ']
        
        segments: List[str] = []
        for sep in separators:
            if sep in text:
                raw_parts = text.split(sep)
                # 将分隔符保留在前一段末尾（句子边界更自然）
                parts: List[str] = []
                for i, part in enumerate(raw_parts):
                    if i < len(raw_parts) - 1:
                        parts.append(part + sep)
                    else:
                        parts.append(part)
                # 过滤空片段
                parts = [p for p in parts if p.strip()]
                if len(parts) > 1:
                    segments = parts
                    break
        
        # 所有分隔符都找不到多个片段 → 硬切
        if not segments:
            segments = []
            for i in range(0, len(text), target_size):
                segments.append(text[i:i + target_size])
        
        # ---- 第二步：将片段合并到目标大小，超大片段递归切分 ----
        merged: List[str] = []
        buffer = ""
        
        for seg in segments:
            # 如果单个片段就超过 max_size，递归切分它
            if len(seg) > max_size:
                # 先把 buffer 输出
                if buffer.strip():
                    merged.append(buffer.strip())
                    buffer = ""
                # 递归切分超大片段
                sub_chunks = self._recursive_split_text(seg, target_size, max_size, overlap=0)
                merged.extend(sub_chunks)
                continue
            
            # 尝试累积到 buffer
            if len(buffer) + len(seg) <= target_size:
                buffer += seg
            else:
                # buffer 已满，输出并开始新 buffer
                if buffer.strip():
                    merged.append(buffer.strip())
                buffer = seg
        
        # 最后的 buffer（如果太短，合并到前一个 chunk 避免尾部碎片）
        if buffer.strip():
            tail = buffer.strip()
            if len(tail) < target_size // 3 and merged:
                merged[-1] = merged[-1] + "\n\n" + tail
            else:
                merged.append(tail)
        
        # ---- 第三步：添加重叠 ----
        if overlap > 0 and len(merged) > 1:
            overlapped = [merged[0]]
            for i in range(1, len(merged)):
                prev = merged[i - 1]
                tail = prev[-overlap:]
                # 优先在句子/词边界开始重叠
                for boundary in ['。', '！', '？', '. ', '! ', '? ', '\n', ' ']:
                    pos = tail.find(boundary)
                    if pos >= 0:
                        tail = tail[pos + len(boundary):]
                        break
                overlap_prefix = tail.strip()
                if overlap_prefix:
                    overlapped.append(overlap_prefix + " " + merged[i])
                else:
                    overlapped.append(merged[i])
            merged = overlapped
        
        return [c for c in merged if c.strip()]
    
    def _is_reference_like(self, text: str) -> bool:
        """
        检测是否为参考文献/纯噪音内容（仅过滤最明确的模式，宁多留不误杀）
        """
        sample = text[:300].strip()
        if len(sample) < 10:
            return True
        # 纯页码 ("123", "Page 5")
        if re.match(r'^(Page\s*)?\d+\s*$', sample, re.IGNORECASE):
            return True
        # [数字] 开头的参考文献
        if re.match(r'^\s*\[\d+\]\s+[A-Z]', sample):
            return True
        # DOI / arXiv / URL 开头
        if re.match(r'^(doi|arXiv|https?://)', sample, re.IGNORECASE):
            return True
        return False

    def _is_low_quality_chunk(self, text: str, min_chars: int = 120) -> bool:
        """
        轻量质量过滤：拦截明显无语义价值的碎片（数字/符号/短行堆叠等）。

        目标：最小改动下尽量减少“短碎、断裂、全编号”chunk进入向量库与检索结果。
        """
        if not text:
            return True

        raw = text.replace('\u00a0', ' ').strip()
        if not raw:
            return True

        compact = re.sub(r'\s+', ' ', raw).strip()
        if len(compact) < min_chars:
            return True

        # 多行且多数为极短行：典型断裂/OCR 噪音
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) >= 4:
            short_lines = sum(1 for line in lines if len(line) <= 6)
            if short_lines / len(lines) >= 0.6:
                return True

        return False
    
    def create_collection(self, document_id: int) -> str:
        """创建向量集合"""
        collection_id = f"doc_{document_id}_{uuid.uuid4().hex[:8]}"
        try:
            # 🔧 修复：指定 cosine 距离度量，使 1-distance 等于余弦相似度
            self.client.get_or_create_collection(
                name=collection_id,
                metadata={
                    "document_id": str(document_id),
                    "hnsw:space": "cosine",
                    # 孤立集合清理据此判断年龄，避免误删正在写入的集合（见 vector_maintenance）
                    "created_at": int(time.time()),
                }
            )
            logger.info(f"✅ 创建向量集合: {collection_id}")
            return collection_id
        except Exception as e:
            logger.error(f"❌ 创建集合失败: {e}")
            raise
    
    async def add_documents(
        self, 
        collection_id: str, 
        text: str,
        metadata: Dict[str, Any] = None,
        structured_elements: Optional[List[Dict[str, Any]]] = None
    ) -> int:
        """
        文档切块并添加到向量库（通用，适用于任何文档类型）
        
        流程：拼接文本 → 统一递归切分 → 过滤参考文献 → 生成 Embedding → 存储
        
        Args:
            collection_id: 集合ID
            text: 文本内容（当无 structured_elements 时使用）
            metadata: 元数据
            structured_elements: 结构化元素列表（Unstructured 输出，可选）
            
        Returns:
            chunk_count: 切块数量
        """
        SKIP_CATEGORIES = {'Header', 'Footer', 'PageHeader', 'PageFooter', 'PageBreak'}
        
        try:
            collection = self.client.get_collection(collection_id)
            embedding_config = self.embedding_config
            # ===== 第一步：构建全文 =====
            if structured_elements:
                full_text = '\n\n'.join(
                    elem['text'].strip()
                    for elem in structured_elements
                    if elem.get('category') not in SKIP_CATEGORIES
                    and elem['text'].strip()
                )
                logger.info(f"📄 从 {len(structured_elements)} 个结构化元素拼接文本（{len(full_text)} 字符）")
            else:
                full_text = text or ""
                logger.info(f"📄 使用原始文本（{len(full_text)} 字符）")
            
            if not full_text.strip():
                logger.warning(f"   ⚠️ 文本为空，跳过向量化")
                return 0
            
            # ===== 第二步：统一递归切分（一个函数处理所有文档类型）=====
            chunk_texts = self._recursive_split_text(
                full_text, target_size=600, max_size=900, overlap=120
            )
            logger.info("   切分参数: target=600 chars, max=900 chars, overlap=120 chars")
            logger.info(f"   切分完成: {len(chunk_texts)} 个 chunk")
            
            if not chunk_texts:
                logger.warning(f"   ⚠️ 切分结果为空，跳过向量化")
                return 0
            
            # ===== 第三步：过滤明确的参考文献/噪音 =====
            all_chunks = chunk_texts  # 保存原始副本用于兜底
            filtered_reference = 0
            filtered_low_quality = 0
            clean_chunks = []
            for c in chunk_texts:
                if self._is_reference_like(c):
                    filtered_reference += 1
                elif self._is_low_quality_chunk(c):
                    filtered_low_quality += 1
                else:
                    clean_chunks.append(c)
            chunk_texts = clean_chunks
            
            if filtered_reference > 0:
                logger.info(f"   过滤参考文献/噪音: {filtered_reference} 个")
            if filtered_low_quality > 0:
                logger.info(f"   过滤低质量碎片: {filtered_low_quality} 个")
            
            if not chunk_texts:
                # 兜底：如果全被过滤，保留较长且非参考文献的 chunk，避免完全空库
                fallback_candidates = [c for c in all_chunks if not self._is_reference_like(c)]
                if not fallback_candidates:
                    fallback_candidates = all_chunks
                fallback_candidates.sort(key=len, reverse=True)
                chunk_texts = fallback_candidates[:10]
                logger.warning(f"   ⚠️ 全部被过滤，兜底保留 {len(chunk_texts)} 个 chunk")
            
            total_chunks = len(chunk_texts)
            avg_len = sum(len(c) for c in chunk_texts) // max(total_chunks, 1)
            logger.info(f"   最终: {total_chunks} 个 chunk，平均 {avg_len} 字符")
            
            # ===== 第四步：生成 Embedding =====
            # 🚀 [2026-02-25] 移除 Contextual Embedding 前缀
            #    旧逻辑：在每个 chunk 前拼接 [文档: title] summary_preview\n---\n
            #    问题：摘要与向量化并行执行(asyncio.gather)，metadata 中 summary 始终为空，
            #    前缀仅包含文件名（如 "[文档: xxx.pdf] \n---\n"），信息量极低，
            #    却额外增加 ~20-30 字符/chunk 的 token 消耗（×整个文档的 chunk 数）。
            #    实测移除后检索质量无明显下降，embedding API 开销减少约 3-5%。
            logger.info(f"🔄 正在生成向量（Qwen 异步 API）...")
            
            chunk_embeddings = await self._generate_embeddings_batch(chunk_texts, config=embedding_config)
            if chunk_embeddings:
                dimensions = len(chunk_embeddings[0])
                if any(len(vector) != dimensions for vector in chunk_embeddings):
                    raise ValueError("向量服务返回的维度不一致")
                self.embedding_dimension = dimensions
                collection.modify(metadata={
                    **{k: v for k, v in (collection.metadata or {}).items() if not k.startswith("hnsw:")},
                    "embedding_signature": self.embedding_signature(embedding_config),
                    "embedding_model": embedding_config["model"],
                    "embedding_dimensions": dimensions,
                })
            logger.info(f"✅ 向量生成完成（{len(chunk_embeddings)} 个向量，维度 {self.embedding_dimension}）")
            
            # ===== 第六步：准备 metadata 并存储 =====
            ids = [f"chunk_{i}" for i in range(total_chunks)]
            metadatas = []
            
            # 🚀 [2026-02-25] metadata 瘦身：移除搜索时未使用的冗余字段
            #   移除: text[:800](已在 documents 参数中存储完整文本)、
            #          prev_context/next_context(搜索中未使用)、
            #          quality_score/final_score(固定值0.7，搜索时已有 fallback 默认值)、
            #          length(搜索时用 len(text) 现算)
            #   保留: chunk_index、position(搜索排序用)、source_id(过滤用)、document_id、filename、source_name、source_type
            for i, chunk_text in enumerate(chunk_texts):
                chunk_metadata = {
                    "chunk_index": i,
                    "position": i / max(total_chunks, 1),
                    **(metadata or {})
                }
                metadatas.append(chunk_metadata)
            
            # 添加到 ChromaDB（线程池执行）
            def _sync_add():
                collection.add(
                    ids=ids,
                    embeddings=chunk_embeddings,
                    documents=chunk_texts,
                    metadatas=metadatas
                )
            
            await self._run_in_executor(_sync_add)
            
            # BM25 缓存失效并预构建（避免首次检索延迟）
            self._bm25_cache.pop(collection_id, None)
            try:
                await self._run_in_executor(self._get_or_build_bm25, collection_id)
                logger.info(f"✅ BM25 索引预构建完成: {collection_id}")
            except Exception as bm25_err:
                logger.warning(f"⚠️ BM25 预构建失败（不影响主流程）: {bm25_err}")
            
            logger.info(f"✅ 已添加 {total_chunks} 个向量到存储")
            return total_chunks
            
        except Exception as e:
            logger.error(f"❌ 添加文档失败: {e}")
            raise
    
    async def _generate_embeddings_batch(
        self,
        texts: List[str],
        batch_size: int = 10,
        max_concurrency: int = None,  # 🆕 动态调整
        config: Optional[Dict[str, Any]] = None,
    ) -> List[List[float]]:
        """
        批量生成 embeddings（工业级：动态并发+重试）
        
        Args:
            texts: 文本列表
            batch_size: 每批处理的文本数量（DashScope text-embedding-v4 最大：10）
            max_concurrency: 并发请求数（None=自动调整）

            
        Returns:
            embeddings 列表
        """
        if not texts:
            return []
        config = config or self.embedding_config

        # 🚀 优化：提高并发数上限，减少总耗时
        # 🚀 [2026-02-05] 进一步提高并发度
        if max_concurrency is None:
            chunk_count = len(texts)
            if chunk_count < 50:
                max_concurrency = 10   # 小文档 (原6)
            elif chunk_count < 150:
                max_concurrency = 20   # 中文档 (原12)
            elif chunk_count < 400:
                max_concurrency = 32   # 大文档 (原20)
            else:
                max_concurrency = 48   # 超大文档 (原32)
        if settings.is_local_mode:
            max_concurrency = min(max_concurrency, max(1, settings.local_embedding_concurrency))

        batches: List[List[str]] = [
            texts[i:i + batch_size]
            for i in range(0, len(texts), batch_size)
        ]

        total_batches = len(batches)
        logger.info(f"🔄 开始向量化：共 {len(texts)} 个片段，分 {total_batches} 批处理（每批{batch_size}个），动态并发 {max_concurrency}")

        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        results: List[Optional[List[List[float]]]] = [None] * total_batches

        async def process_batch(index: int, batch_payload: List[str], max_retries: int = 3) -> None:
            """处理单个批次（带重试和指数退避）"""
            async with semaphore:
                for attempt in range(max_retries):
                    try:
                        batch_embeddings: List[Optional[List[float]]] = [None] * len(batch_payload)
                        cache_keys = [self._embedding_cache_key(text, config) for text in batch_payload]

                        # 🚀 优化：并行检查缓存
                        cached_results = await asyncio.gather(*[
                            cache_get_bytes(cache_key) for cache_key in cache_keys
                        ])

                        
                        missing_indices: List[int] = []
                        missing_texts: List[str] = []
                        
                        for pos, cached_bytes in enumerate(cached_results):
                            if cached_bytes:
                                try:
                                    batch_embeddings[pos] = json.loads(cached_bytes.decode("utf-8"))
                                    continue
                                except Exception:
                                    pass
                            missing_indices.append(pos)
                            missing_texts.append(batch_payload[pos])

                        if missing_texts:
                            # 🆕 带超时的API调用
                            response = await asyncio.wait_for(
                                self._embedding_client(config).embeddings.create(
                                    model=config["model"],
                                    input=missing_texts,
                                    encoding_format="float",
                                    **({"dimensions": config["dimensions"]} if config.get("dimensions") else {}),
                                ),
                                timeout=30.0  # 30秒超时
                            )

                            # 🚀 优化：并行写入缓存
                            cache_tasks = []
                            for offset, item in enumerate(response.data):
                                emb = item.embedding
                                idx = missing_indices[offset]
                                batch_embeddings[idx] = emb
                                cache_tasks.append(cache_set_bytes(
                                    cache_keys[idx],
                                    json.dumps(emb).encode("utf-8"),
                                ))
                            if cache_tasks:
                                await asyncio.gather(*cache_tasks, return_exceptions=True)

                        if any(emb is None for emb in batch_embeddings):
                            raise RuntimeError("Embedding generation failed for some inputs")

                        results[index] = [emb for emb in batch_embeddings if emb is not None]

                        if index % max(1, total_batches // 10) == 0 or index == total_batches - 1:
                            logger.info(f"   进度: {index + 1}/{total_batches} ({int(100 * (index + 1) / total_batches)}%)")

                        break  # 成功，退出重试循环

                    except asyncio.TimeoutError:
                        if attempt == max_retries - 1:
                            logger.error(f"❌ 批次 {index + 1} 超时，已重试{max_retries}次")
                            raise
                        logger.warning(f"⚠️ 批次 {index + 1} 超时，{attempt + 1}秒后重试...")
                        await asyncio.sleep(2 ** attempt)  # 指数退避：1s, 2s, 4s

                    except Exception as exc:
                        if attempt == max_retries - 1:
                            logger.error(f"❌ 生成 embeddings 失败（批次 {index + 1}/{total_batches}）: {exc}")
                            raise
                        logger.warning(f"⚠️ 批次 {index + 1} 失败，{attempt + 1}秒后重试...")
                        await asyncio.sleep(2 ** attempt)

        # 直接使用 asyncio.gather() 调用协程，不使用 create_task()
        # 在 Celery prefork 模式下更稳定
        try:
            await asyncio.gather(*[
                process_batch(idx, batch) for idx, batch in enumerate(batches)
            ])
        except Exception:
            raise
        
        # 按原始顺序拼接
        all_embeddings: List[List[float]] = []
        for idx, batch_result in enumerate(results):
            if batch_result is None:
                raise RuntimeError(f"批次 {idx + 1}/{total_batches} 没有生成向量结果")
            all_embeddings.extend(batch_result)
        
        logger.info(f"✅ 向量化完成：共生成 {len(all_embeddings)} 个向量")
        return all_embeddings

    def _embedding_cache_key(self, text: str, config=None) -> str:
        """生成 Embedding 缓存键"""
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return f"embedding:{self.embedding_signature(config or self.embedding_config)}:{digest}"

    
    async def _generate_embedding_single(self, text: str) -> List[float]:
        """
        生成单个文本的 embedding（用于查询），带 Redis 短期缓存
        """
        try:
            config = self.embedding_config
            cache_key = self._embedding_cache_key(text, config)
            cached = await cache_get_bytes(cache_key)
            if cached:
                return json.loads(cached)

            response = await self._embedding_client(config).embeddings.create(
                model=config["model"],
                input=text,
                encoding_format="float",
                **({"dimensions": config["dimensions"]} if config.get("dimensions") else {}),
            )
            embedding = response.data[0].embedding

            try:
                await cache_set_bytes(cache_key, json.dumps(embedding).encode(), ttl=300)
            except Exception:
                pass

            return embedding
        except Exception as e:
            logger.error(f"❌ 生成查询向量失败: {e}")
            return None

    async def search(
        self,
        collection_id: str,
        query: str,
        top_k: int = 5,
        source_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        通用智能搜索（带重排序）- 异步版本
        """
        try:
            collection = self.client.get_collection(collection_id)

            self.ensure_collection_compatible(collection)
            query_embedding = await self._generate_embedding_single(query)
            if query_embedding is None:
                logger.warning("⚠️ 无法生成查询向量，返回空结果")
                return []

            # 🆕 在线程池中执行ChromaDB查询
            def _sync_query():
                where_clause = None
                if source_ids:
                    where_clause = (
                        {"source_id": source_ids[0]}
                        if len(source_ids) == 1
                        else {"source_id": {"$in": source_ids}}
                    )
                return collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                include=["documents", "metadatas", "distances"],
                where=where_clause,
            )
            
            candidate_results = await self._run_in_executor(_sync_query)
            
            # 重排序 + 参考文献过滤
            reranked_results = []
            filtered_references = 0
            filtered_low_quality = 0
            
            for i in range(len(candidate_results['ids'][0])):
                text = candidate_results['documents'][0][i]
                metadata = candidate_results['metadatas'][0][i]
                
                # 🚫 强力过滤参考文献（二次过滤）
                if self._is_reference_like(text):
                    filtered_references += 1
                    continue  # 跳过这个结果
                if self._is_low_quality_chunk(text):
                    filtered_low_quality += 1
                    continue
                
                # cosine 距离：1-distance 即为余弦相似度
                vector_score = 1 - candidate_results['distances'][0][i]
                
                # 长度因子：惩罚极短/极长 chunk
                text_length = len(text)
                if text_length < 100:
                    length_factor = 0.5
                elif text_length < 200:
                    length_factor = 0.8
                elif text_length > 2000:
                    length_factor = 0.95
                else:
                    length_factor = 1.0
                
                # 评分 = 向量相似度 × 长度因子（简洁，让语义主导排序）
                final_score = vector_score * length_factor
                
                reranked_results.append({
                    "id": candidate_results['ids'][0][i],
                    "text": text,
                    "metadata": metadata,
                    "vector_score": vector_score,
                    "quality_score": metadata.get('quality_score', 0.7),
                    "final_score": final_score
                })
            
            if filtered_references > 0:
                logger.info(f"   🚫 过滤掉 {filtered_references} 个参考文献片段")
            if filtered_low_quality > 0:
                logger.info(f"   🚫 过滤掉 {filtered_low_quality} 个低质量片段")
            
            # 按综合分数排序
            reranked_results.sort(key=lambda x: x['final_score'], reverse=True)
            
            # 返回 top_k
            final_results = reranked_results[:top_k]
            
            # 打印调试信息
            logger.info(f"\n🔍 检索结果 (query: {query[:50]}...):")
            for i, res in enumerate(final_results, 1):
                logger.info(f"   [{i}] 向量:{res['vector_score']:.3f} | "
                      f"质量:{res['quality_score']:.3f} | "
                      f"综合:{res['final_score']:.3f} | "
                      f"内容:{res['text'][:60]}...")
            
            return final_results
            
        except Exception as e:
            logger.error(f"❌ 智能搜索失败: {e}")
            if isinstance(e, EmbeddingReindexRequired):
                raise
            import traceback
            traceback.print_exc()
            return []
    
    def _compute_cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算两个向量的余弦相似度"""
        vec1 = np.array(vec1)
        vec2 = np.array(vec2)
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)
    
    async def search_with_mmr(
        self,
        collection_id: str,
        query: str,
        top_k: int = 5,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        exclude_chunk_ids: Optional[List[str]] = None,
        source_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        使用 MMR (Maximal Marginal Relevance) 算法进行搜索（改进版 v2）
        
        改进:
        1. 🆕 动态调整fetch_k（更大的候选池）
        2. 🆕 位置多样性奖励（避免所有结果来自同一区域）
        3. 🆕 支持排除已选chunk（用于多查询增量检索）
        4. 🆕 chunk长度检查（避免过短或过长）
        """
        try:
            collection = self.client.get_collection(collection_id)

            self.ensure_collection_compatible(collection)
            query_embedding = await self._generate_embedding_single(query)
            if query_embedding is None:
                logger.warning("⚠️ 无法生成查询向量，跳过MMR检索")
                return []

            # 🆕 动态调整fetch_k - 从3倍提升到6倍
            fetch_k = max(fetch_k, top_k * 6)
            
            # 🆕 在线程池中执行ChromaDB查询（包含embeddings，数据量大）
            def _sync_query_mmr():
                where_clause = None
                if source_ids:
                    where_clause = (
                        {"source_id": source_ids[0]}
                        if len(source_ids) == 1
                        else {"source_id": {"$in": source_ids}}
                    )
                return collection.query(
                query_embeddings=[query_embedding],
                n_results=fetch_k,
                include=["documents", "metadatas", "distances", "embeddings"],
                where=where_clause,
            )
            
            candidate_results = await self._run_in_executor(_sync_query_mmr)
            
            # 2. 准备候选文档（包含向量、文本、元数据）
            exclude_ids = set(exclude_chunk_ids or [])
            candidates = []
            
            for i in range(len(candidate_results['ids'][0])):
                chunk_id = candidate_results['ids'][0][i]
                text = candidate_results['documents'][0][i]
                
                # 🆕 排除已选chunk（用于多查询检索）
                if chunk_id in exclude_ids:
                    continue
                
                # 过滤参考文献
                if self._is_reference_like(text):
                    continue
                if self._is_low_quality_chunk(text):
                    continue
                
                # 🔧 修复：统一长度因子（与 search 一致）
                text_length = len(text)
                if text_length < 100:
                    length_factor = 0.5
                elif text_length < 200:
                    length_factor = 0.8
                elif text_length > 2000:
                    length_factor = 0.95
                else:
                    length_factor = 1.0
                
                # 获取文档向量（用于计算相似度）
                doc_embedding = candidate_results['embeddings'][0][i] if candidate_results['embeddings'] else None
                
                # cosine 距离：1-distance 即为余弦相似度
                vector_score = 1 - candidate_results['distances'][0][i]
                metadata = candidate_results['metadatas'][0][i]
                position = metadata.get('position', 0.5)
                
                # 评分 = 向量相似度 × 长度因子（与 search 一致）
                relevance_score = vector_score * length_factor
                
                candidates.append({
                    "id": chunk_id,
                    "text": text,
                    "metadata": metadata,
                    "vector_score": vector_score,
                    "quality_score": metadata.get('quality_score', 0.7),
                    "relevance_score": relevance_score,
                    "position": position,
                    "embedding": doc_embedding
                })
            
            if not candidates:
                return []
            
            # 3. MMR 算法：选择既相关又多样的结果（改进版）
            selected = []
            remaining = candidates.copy()
            
            # 首先选择最相关的文档
            remaining.sort(key=lambda x: x['relevance_score'], reverse=True)
            selected.append(remaining.pop(0))
            
            # 如果文档没有向量，回退到传统排序
            if not any(c.get('embedding') is not None for c in remaining):
                logger.warning("   ⚠️ 文档向量不可用，回退到传统排序")
                selected.extend(remaining[:top_k - 1])
                return selected[:top_k]
            
            # 逐步选择剩余文档
            while len(selected) < top_k and remaining:
                best_score = -float('inf')
                best_idx = 0
                
                # 🆕 收集已选文档的位置
                selected_positions = [doc.get('position', 0.5) for doc in selected]
                
                for idx, candidate in enumerate(remaining):
                    # 计算与查询的相关性
                    relevance = candidate['relevance_score']
                    
                    # 计算与已选文档的最大相似度（内容多样性）
                    max_similarity = 0.0
                    if candidate.get('embedding') is not None:
                        for selected_doc in selected:
                            if selected_doc.get('embedding') is not None:
                                similarity = self._compute_cosine_similarity(
                                    candidate['embedding'],
                                    selected_doc['embedding']
                                )
                                max_similarity = max(max_similarity, similarity)
                    
                    # 🆕 计算位置多样性（避免所有chunk来自同一区域）
                    candidate_position = candidate.get('position', 0.5)
                    min_position_distance = min([
                        abs(candidate_position - p) for p in selected_positions
                    ]) if selected_positions else 1.0
                    
                    # 位置多样性奖励（距离越远奖励越高）
                    position_diversity_bonus = min_position_distance * 0.15
                    
                    # MMR 分数 = lambda * relevance - (1 - lambda) * max_similarity + position_diversity
                    mmr_score = (
                        lambda_mult * relevance 
                        - (1 - lambda_mult) * max_similarity 
                        + position_diversity_bonus  # 🆕 位置多样性奖励
                    )
                    
                    if mmr_score > best_score:
                        best_score = mmr_score
                        best_idx = idx
                
                # 添加最佳候选
                selected.append(remaining.pop(best_idx))
            
            # 格式化输出
            raw_results = []
            for doc in selected:
                raw_results.append({
                    "id": doc["id"],
                    "text": doc["text"],
                    "metadata": doc["metadata"],
                    "vector_score": doc["vector_score"],
                    "quality_score": doc["quality_score"],
                    "final_score": doc["relevance_score"]  # 保持兼容性
                })
            
            final_results = raw_results
            
            logger.info(f"\n🔍 MMR 检索结果 (query: {query[:50]}..., lambda={lambda_mult}):")
            for i, res in enumerate(final_results, 1):
                logger.info(f"   [{i}] 向量:{res['vector_score']:.3f} | "
                      f"质量:{res['quality_score']:.3f} | "
                      f"综合:{res['final_score']:.3f} | "
                      f"内容:{res['text'][:60]}...")
            
            return final_results

        except Exception as e:
            logger.error(f"❌ MMR 搜索失败: {e}")
            if isinstance(e, EmbeddingReindexRequired):
                raise
            import traceback
            traceback.print_exc()
            logger.info("   🔄 回退到传统搜索...")
            return await self.search(collection_id, query, top_k)
    
    async def advanced_hybrid_search(
        self,
        collection_id: str,
        semantic_queries: List[str],
        keywords: List[str],
        top_k: int = 5,
        source_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        🆕 高级分层混合检索 (Decoupled Hybrid Search)
        
        策略：
        1. 语义路 (Semantic Track): 对 semantic_queries 运行 Vector Search
        2. 关键词路 (Keyword Track): 对 keywords 运行 BM25 Search
        3. 融合 (Fusion): 使用 Multi-Way RRF 将所有结果列表融合
        """
        if not semantic_queries and not keywords:
            return []
            
        logger.info(f"🚀 启动高级混合检索:")
        logger.info(f"   - 语义查询 ({len(semantic_queries)}): {semantic_queries}")
        logger.info(f"   - 关键词 ({len(keywords)}): {keywords}")
        
        tasks = []
        
        # 1. 语义路任务 (Vector)
        for q in semantic_queries:
            tasks.append(self.search(collection_id, q, top_k=top_k * 2, source_ids=source_ids))
            
        # 2. 关键词路任务 (🔧 修复：使用 BM25 而非简单字符串匹配)
        async def run_bm25_search():
            """使用 BM25 进行关键词检索"""
            if not keywords:
                return []
            
            def _do_bm25():
                try:
                    bm25_data = self._get_or_build_bm25(collection_id)
                    if not bm25_data:
                        logger.warning("   ⚠️ BM25 索引构建失败，回退到字符串匹配")
                        return self._fallback_keyword_match(collection_id, keywords, top_k)
                    
                    bm25_tuple = bm25_data
                    bm25, ids, metadatas, documents = bm25_tuple[:4]
                    
                    # 🔧 优化：使用 Jieba 分词后的关键词列表
                    # 对于 Router 提取的关键词，直接使用（它们已经是有意义的词组）
                    tokenized_query = []
                    for kw in keywords:
                        # 对每个关键词进行分词（处理可能的词组）
                        kw_tokens = self._tokenize(kw)
                        tokenized_query.extend(kw_tokens)
                    
                    # 去重但保持顺序
                    seen = set()
                    unique_tokens = []
                    for t in tokenized_query:
                        if t not in seen:
                            seen.add(t)
                            unique_tokens.append(t)
                    tokenized_query = unique_tokens
                    
                    logger.info(f"   🔑 BM25 关键词: {keywords} → 分词: {tokenized_query[:10]}{'...' if len(tokenized_query) > 10 else ''}")
                    
                    # 获取 BM25 分数
                    doc_scores = bm25.get_scores(tokenized_query)
                    
                    # 获取 top_k 索引
                    top_n = min(len(doc_scores), top_k * 2)
                    top_indices = np.argsort(doc_scores)[-top_n:][::-1]
                    
                    results = []
                    for i, idx in enumerate(top_indices):
                        score = doc_scores[idx]
                        if i < 3:
                            logger.info(f"      BM25 Top {i+1}: Score={score:.4f} | {documents[idx][:40]}...")
                        
                        if score <= 0:
                            continue

                        if source_ids:
                            source_id = (metadatas[idx] or {}).get("source_id")
                            if source_id not in source_ids:
                                continue

                        if self._is_reference_like(documents[idx]):
                            continue
                        if self._is_low_quality_chunk(documents[idx]):
                            continue
                        
                        results.append({
                            "id": ids[idx],
                            "text": documents[idx],
                            "metadata": metadatas[idx],
                            "bm25_score": float(score),
                            "score": float(score),
                            "quality_score": metadatas[idx].get('quality_score', 0.5)
                        })
                    
                    logger.info(f"   📊 BM25 召回: {len(results)} 个结果")
                    return results
                    
                except Exception as e:
                    logger.warning(f"⚠️ BM25 检索失败: {e}，回退到字符串匹配")
                    import traceback
                    traceback.print_exc()
                    return self._fallback_keyword_match(collection_id, keywords, top_k)
            
            return await self._run_in_executor(_do_bm25)

        tasks.append(run_bm25_search())
            
        # 3. 并行执行
        logger.info(f"   ⚡ 并行执行 {len(tasks)} 个检索任务...")
        results_lists = await asyncio.gather(*tasks)
        
        # 4. Multi-Way RRF 融合
        k = 60
        fused_scores = {}
        all_docs = {}
        
        for r_list in results_lists:
            for rank, doc in enumerate(r_list):
                doc_id = doc['id']
                if doc_id not in all_docs:
                    all_docs[doc_id] = doc
                
                rrf_score = 1 / (k + rank + 1)
                fused_scores[doc_id] = fused_scores.get(doc_id, 0.0) + rrf_score
        
        sorted_ids = sorted(fused_scores.keys(), key=lambda x: fused_scores[x], reverse=True)
        
        final_results = []
        for doc_id in sorted_ids:
            doc = all_docs[doc_id]
            doc['final_score'] = fused_scores[doc_id]
            doc['rrf_score'] = fused_scores[doc_id]
            final_results.append(doc)
            
        logger.info(f"   ✅ RRF 融合完成，返回 Top {len(final_results)}")
        
        return final_results

    def delete_chunks_by_source_ids(self, collection_id: str, source_ids: List[str]) -> int:
        """按 source_id 删除指定来源的向量块"""
        if not source_ids:
            return 0
        try:
            collection = self.client.get_collection(collection_id)
            where_clause = (
                {"source_id": source_ids[0]}
                if len(source_ids) == 1
                else {"source_id": {"$in": source_ids}}
            )

            data = collection.get(where=where_clause, include=[])
            ids = data.get("ids") or []
            if not ids:
                return 0

            collection.delete(ids=ids)
            self._bm25_cache.pop(collection_id, None)
            self._bm25_cache_check_time.pop(collection_id, None)
            logger.info(f"✅ 已删除来源向量: {collection_id} | count={len(ids)}")
            return len(ids)
        except Exception as e:
            logger.warning(f"⚠️ 删除来源向量失败: {e}")
            return 0

    def delete_collection(self, collection_id: str):
        """删除向量集合"""
        try:
            self.client.delete_collection(collection_id)
            # 🆕 清除 BM25 缓存
            self._bm25_cache.pop(collection_id, None)
            logger.info(f"✅ 已删除集合: {collection_id}")
        except Exception as e:
            logger.warning(f"⚠️ 删除集合失败: {e}")
    
    def get_collection_info(self, collection_id: str) -> Dict[str, Any]:
        """获取集合信息"""
        try:
            collection = self.client.get_collection(collection_id)
            count = collection.count()
            return {
                "id": collection_id,
                "count": count,
                "metadata": collection.metadata
            }
        except Exception as e:
            logger.error(f"❌ 获取集合信息失败: {e}")
            return {}


# 全局实例
vector_store_manager = VectorStoreManager()

