import asyncio
import hashlib
import json
import os
import tempfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from unstructured.partition.auto import partition
from utils.logger import get_logger
logger = get_logger(__name__)

try:  # 可选依赖
    from unstructured.partition.pdf import partition_pdf
except ImportError:  # pragma: no cover
    partition_pdf = None

try:  # 可选依赖
    import fitz  # type: ignore
except ImportError:  # pragma: no cover
    fitz = None  # type: ignore

from cache_service import cache_get_bytes, cache_set_bytes
from .parser_base import IDocumentParser


_GLOBAL_EXECUTOR: ThreadPoolExecutor | None = None


class UnstructuredParser(IDocumentParser):
    """使用 Unstructured `partition` 的基础解析器，支持逐页并发解析与内容清洗。"""

    EXCLUDED_CATEGORIES = {"Header", "Footer", "PageHeader", "PageFooter"}

    def __init__(
        self,
        strategy: str = "fast",
        languages: Optional[List[str]] = None,
        max_workers: int = 16,  # 🚀 [2026-02-05] 优化：提高并发数 8 -> 16
        min_text_length: int = 20,
    ):
        self.strategy = strategy
        self.languages = languages or ["chi_sim", "eng"]
        self.max_workers = max_workers
        self.min_text_length = min_text_length
        self.min_chunk_length = 200  # 仅做基础清洗合并，最终 chunk 尺寸由 VectorStore 控制
        self._local_cache: "OrderedDict[str, bytes]" = OrderedDict()
        self._local_cache_max = 32

    async def parse(self, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        cache_key = hashlib.sha256(file_bytes).hexdigest()

        cached = await self._cache_get(cache_key)
        if cached is not None:
            return cached

        loop = asyncio.get_running_loop()
        executor = self._get_executor()
        cleaned = await loop.run_in_executor(
            executor,
            self._parse_sync,
            file_bytes,
            filename,
        )
        await self._cache_set(cache_key, cleaned)
        return cleaned

    # 🚀 [2026-02-25] 纯文本格式快速路径——直接按段落拆分，跳过 Unstructured
    _PLAINTEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".text", ".log"}

    def _parse_sync(self, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        suffix = (os.path.splitext(filename)[1] or "").lower()

        # 纯文本文件：直接按段落切分，无需 Unstructured 解析
        if suffix in self._PLAINTEXT_SUFFIXES:
            return self._parse_plaintext(file_bytes, filename, suffix)

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        try:
            if suffix == ".pdf" and partition_pdf and fitz:
                elements = self._parse_pdf_by_page(tmp_path)
            else:
                elements = self._parse_generic(tmp_path)

            cleaned = self._clean_elements(elements)
            return cleaned
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _parse_plaintext(self, file_bytes: bytes, filename: str, suffix: str) -> List[Dict[str, Any]]:
        """纯文本快速解析：直接解码并按段落拆分，跳过 Unstructured 开销。"""
        # 尝试常见编码
        text = None
        for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
            try:
                text = file_bytes.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue
        if text is None:
            text = file_bytes.decode("utf-8", errors="replace")

        # 按双换行或单换行拆分段落
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        # 如果整个文本没有双换行，fallback 到单换行拆分
        if len(paragraphs) <= 1 and "\n" in text:
            paragraphs = [p.strip() for p in text.split("\n") if p.strip()]

        # Markdown 类型标记为 Title / NarrativeText
        is_markdown = suffix in (".md", ".markdown")
        elements: List[Dict[str, Any]] = []
        for idx, para in enumerate(paragraphs):
            if not para or len(para) < self.min_text_length:
                continue
            category = "NarrativeText"
            if is_markdown and para.startswith("#"):
                category = "Title"
            elements.append({
                "id": f"elem_{idx}",
                "text": para,
                "category": category,
                "metadata": {"page_number": 1, "filename": filename},
            })

        logger.info(f"⚡ 纯文本快速解析 '{filename}': {len(elements)} 个段落（跳过 Unstructured）")
        return elements

    def _get_executor(self) -> ThreadPoolExecutor:
        global _GLOBAL_EXECUTOR
        if _GLOBAL_EXECUTOR is None:
            _GLOBAL_EXECUTOR = ThreadPoolExecutor(max_workers=self.max_workers)
        return _GLOBAL_EXECUTOR

    def _parse_generic(self, path: str) -> List[Dict[str, Any]]:
        elements = partition(
            filename=path,
            strategy=self.strategy,
            languages=self.languages,
        )
        structured: List[Dict[str, Any]] = []
        for idx, element in enumerate(elements):
            metadata = element.metadata.to_dict() if hasattr(element, "metadata") else {}
            structured.append(
                {
                    "id": f"elem_{idx}",
                    "text": str(element),
                    "category": getattr(element, "category", "text"),
                    "metadata": metadata,
                }
            )
        return structured

    def _parse_pdf_by_page(self, path: str) -> List[Dict[str, Any]]:
        """
        🚀 [2026-02-05] 优化：一次性解析整个 PDF，而非逐页调用
        
        之前的问题：逐页调用 partition_pdf，每页产生独立的库调用开销
        优化后：一次性解析整个 PDF，大幅减少调用次数
        """
        # 页数统计已改用可插拔后端，此处不再要求 fitz
        assert partition_pdf is not None

        logger.info("📄 使用 Unstructured 解析 PDF（批量模式）...")

        # 获取页数用于日志（后端可插拔，见 parsers/pdf_text.py）
        from document_service.parsers.pdf_text import get_page_count

        total_pages = get_page_count(path)
        logger.info(f"   总页数: {total_pages if total_pages is not None else '未知'}")

        # 🚀 一次性解析整个 PDF（不指定 page_numbers）
        try:
            elements = partition_pdf(
                filename=path,
                strategy=self.strategy,
                languages=self.languages,
                # 不指定 page_numbers，解析全部页面
            )
            logger.info(f"   ✅ 批量解析完成，共 {len(elements)} 个元素")
        except Exception as e:
            logger.warning(f"   ⚠️ 批量解析失败: {e}，尝试逐页解析...")
            # 降级：逐页解析作为后备方案
            return self._parse_pdf_by_page_fallback(path, total_pages)

        # 转换为标准格式
        results: List[Dict[str, Any]] = []
        for idx, element in enumerate(elements):
            metadata = element.metadata.to_dict() if hasattr(element, "metadata") else {}
            # 尝试获取页码
            page_no = metadata.get("page_number", metadata.get("page", None))
            if page_no:
                metadata["page"] = page_no
            results.append(
                {
                    "id": f"elem_{idx}",
                    "text": str(element),
                    "category": getattr(element, "category", "text"),
                    "metadata": metadata,
                }
            )

        return results

    def _parse_pdf_by_page_fallback(self, path: str, total_pages: int) -> List[Dict[str, Any]]:
        """后备方案：逐页解析（当批量解析失败时使用）"""
        logger.info("📄 使用逐页解析（后备模式）...")
        
        results: List[Dict[str, Any]] = []
        page_numbers = list(range(1, total_pages + 1))
        workers = min(self.max_workers, total_pages) or 1

        def parse_page(page_no: int) -> List[Dict[str, Any]]:
            page_elements = partition_pdf(
                filename=path,
                strategy=self.strategy,
                languages=self.languages,
                page_numbers=[page_no],
            )
            structured: List[Dict[str, Any]] = []
            for idx, element in enumerate(page_elements):
                metadata = element.metadata.to_dict() if hasattr(element, "metadata") else {}
                metadata.setdefault("page", page_no)
                structured.append(
                    {
                        "id": f"p{page_no}_elem_{idx}",
                        "text": str(element),
                        "category": getattr(element, "category", "text"),
                        "metadata": metadata,
                    }
                )
            return structured

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(parse_page, page_no) for page_no in page_numbers]
            for future in futures:
                results.extend(future.result())

        return results

    def _clean_elements(self, elements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        buffer_text: List[str] = []
        buffer_categories: List[str] = []
        buffer_metadata: Dict[str, Any] | None = None
        buffer_length = 0

        def flush_buffer(force: bool = False) -> None:
            nonlocal buffer_text, buffer_categories, buffer_metadata, buffer_length
            if not buffer_text:
                return
            if not force and buffer_length < self.min_chunk_length:
                return
            
            # 智能合并文本
            merged_text = ""
            for i, t in enumerate(buffer_text):
                if i == 0:
                    merged_text = t
                    continue
                
                prev = buffer_text[i-1].strip()
                curr = t.strip()
                
                if not prev or not curr:
                    merged_text += "\n" + curr
                    continue

                # 检查是否需要紧密连接（无空格）
                # 1. 公式断裂修复：前一个以 _, ^, \, { 结尾
                if prev[-1] in ('_', '^', '\\', '{', '['):
                    merged_text += curr
                # 2. 连字符断行修复 (e.g. "process-\ning")
                elif prev.endswith('-') and len(prev) > 1 and prev[-2].isalpha() and curr[0].isalpha():
                    merged_text = merged_text[:-1] + curr
                # 3. 中文连接（无空格）
                elif self._is_cjk(prev[-1]) and self._is_cjk(curr[0]):
                    merged_text += curr
                # 4. 默认用空格连接（代替换行，使段落更紧凑）
                else:
                    merged_text += " " + curr
            
            merged_text = merged_text.strip()
            
            # 后处理修复 LaTeX
            merged_text = self._fix_text_formatting(merged_text)
            
            if not merged_text:
                buffer_text = []
                buffer_categories = []
                buffer_metadata = None
                buffer_length = 0
                return

            if buffer_categories:
                dominant = buffer_categories[0]
                if len(set(cat for cat in buffer_categories if cat)) > 1:
                    dominant = "MergedText"
            else:
                dominant = "Text"

            cleaned.append(
                {
                    "id": None,
                    "text": merged_text,
                    "category": dominant,
                    "metadata": buffer_metadata or {},
                }
            )
            buffer_text = []
            buffer_categories = []
            buffer_metadata = None
            buffer_length = 0

        for element in elements:
            text = (element.get("text") or "").strip()
            if not text:
                continue

            category = element.get("category", "")
            if category in self.EXCLUDED_CATEGORIES:
                continue

            text_lower = text.lower()
            if category.lower().startswith("title") and "references" in text_lower and len(text_lower) < 50:
                continue

            metadata = element.get("metadata") or {}
            metadata.setdefault("page", metadata.get("page_number"))
            metadata.pop("page_number", None)

            if len(text) < self.min_text_length:
                buffer_text.append(text)
                buffer_categories.append(category)
                if buffer_metadata is None:
                    buffer_metadata = metadata
                buffer_length += len(text)
                flush_buffer()
                continue

            buffer_text.append(text)
            buffer_categories.append(category)
            if buffer_metadata is None:
                buffer_metadata = metadata
            buffer_length += len(text)
            flush_buffer()

        flush_buffer(force=True)

        for idx, element in enumerate(cleaned):
            element["id"] = element.get("id") or f"elem_{idx}"

        return cleaned

    def _is_cjk(self, char: str) -> bool:
        """检查字符是否为 CJK (中日韩) 字符"""
        if not char:
            return False
        code = ord(char)
        return (
            0x4E00 <= code <= 0x9FFF or  # CJK Unified Ideographs
            0x3400 <= code <= 0x4DBF or  # CJK Unified Ideographs Extension A
            0x20000 <= code <= 0x2A6DF or  # CJK Unified Ideographs Extension B
            0x2A700 <= code <= 0x2B73F or  # CJK Unified Ideographs Extension C
            0x2B740 <= code <= 0x2B81F or  # CJK Unified Ideographs Extension D
            0x2B820 <= code <= 0x2CEAF or  # CJK Unified Ideographs Extension E
            0xF900 <= code <= 0xFAFF or  # CJK Compatibility Ideographs
            0x2F800 <= code <= 0x2FA1F     # CJK Compatibility Ideographs Supplement
        )

    def _fix_text_formatting(self, text: str) -> str:
        """修复文本格式，特别是 LaTeX 公式"""
        import re
        
        # 1. 修复 LaTeX 双反斜杠转义 (e.g., p_\\theta -> p_\theta)
        # 匹配 \\ 后跟希腊字母或常见 LaTeX 命令
        latex_commands = [
            "alpha", "beta", "gamma", "delta", "epsilon", "zeta", "eta", "theta", "iota", "kappa",
            "lambda", "mu", "nu", "xi", "omicron", "pi", "rho", "sigma", "tau", "upsilon", "phi",
            "chi", "psi", "omega", "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma",
            "Upsilon", "Phi", "Psi", "Omega", "infty", "sum", "prod", "int", "frac", "sqrt",
            "partial", "nabla", "approx", "sim", "leq", "geq", "neq", "in", "notin", "subset",
            "subseteq", "cup", "cap", "times", "cdot", "circ", "oplus", "otimes", "vec", "hat",
            "bar", "tilde", "mathcal", "mathbb", "mathbf", "mathrm", "text", "left", "right"
        ]
        # 使用单词边界 \b 避免误伤
        pattern = r'\\\\(' + '|'.join(latex_commands) + r')\b'
        text = re.sub(pattern, r'\\\1', text)
        
        # 2. 修复公式中断行 (e.g., $p_\n\theta$)
        # 移除数学公式环境中的换行符
        def fix_math_newlines(match):
            return match.group(0).replace('\n', ' ').replace('\r', '')
        
        # 匹配 $...$ (行内公式) 和 $$...$$ (块级公式)
        # 注意：这里使用简单的正则，可能无法处理嵌套或复杂的 LaTeX 环境
        text = re.sub(r'\$[^$]+\$', fix_math_newlines, text)
        
        return text

    async def _cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        redis_key = f"doc:parsed:{key}"
        cached_bytes = await cache_get_bytes(redis_key)
        if cached_bytes:
            try:
                return json.loads(cached_bytes.decode("utf-8"))
            except Exception:
                pass

        local_bytes = self._local_cache.get(redis_key)
        if local_bytes:
            try:
                self._local_cache.move_to_end(redis_key)
                return json.loads(local_bytes.decode("utf-8"))
            except Exception:
                return None
        return None

    async def _cache_set(self, key: str, value: List[Dict[str, Any]]) -> None:
        redis_key = f"doc:parsed:{key}"
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        await cache_set_bytes(redis_key, payload)

        self._local_cache[redis_key] = payload
        self._local_cache.move_to_end(redis_key)
        if len(self._local_cache) > self._local_cache_max:
            self._local_cache.popitem(last=False)


