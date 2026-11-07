"""
PDF 纯文本提取（可插拔后端）

为什么需要抽象：原实现直接 `import fitz`（PyMuPDF）。PyMuPDF 采用
**AGPL-3.0 / 商业双许可**，对不打算开源的商业服务而言，其网络条款可能构成
合规负担。因此默认走 pypdf（BSD 许可），装了 PyMuPDF 则自动使用它——
PyMuPDF 更快、对复杂版面的还原也更好。

两种后端的输出结构完全一致，上层无需感知差异。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from utils.logger import get_logger

logger = get_logger(__name__)

# 短于此长度的段落视为噪声（页眉页脚、页码等）
_MIN_PARAGRAPH_CHARS = 20


def _split_paragraphs(text: str) -> List[str]:
    """按空行切分段落并滤掉过短的碎片"""
    if not text or not text.strip():
        return []
    return [
        p.strip()
        for p in text.split("\n\n")
        if p.strip() and len(p.strip()) > _MIN_PARAGRAPH_CHARS
    ]


def _to_elements(pages: List[str]) -> List[Dict[str, Any]]:
    """把逐页文本转为统一的结构化元素"""
    elements: List[Dict[str, Any]] = []
    for page_num, text in enumerate(pages):
        for i, para in enumerate(_split_paragraphs(text)):
            elements.append(
                {
                    "text": para,
                    "category": "NarrativeText",
                    "metadata": {
                        "page_number": page_num + 1,
                        "paragraph_index": i,
                    },
                }
            )
    return elements


def available_backend() -> str:
    """返回当前可用的提取后端名：'pymupdf' | 'pypdf' | 'none'"""
    try:
        import fitz  # noqa: F401
        return "pymupdf"
    except ImportError:
        pass
    try:
        import pypdf  # noqa: F401
        return "pypdf"
    except ImportError:
        return "none"


def extract_text_elements(file_content: bytes) -> List[Dict[str, Any]]:
    """
    从 PDF 字节内容提取结构化文本元素。

    后端优先级：PyMuPDF（若已安装）→ pypdf。两者都没有时返回空列表，
    由调用方按解析失败处理。
    """
    backend = available_backend()

    if backend == "pymupdf":
        try:
            import fitz

            doc = fitz.open(stream=file_content, filetype="pdf")
            try:
                pages = [page.get_text("text") for page in doc]
            finally:
                doc.close()
            elements = _to_elements(pages)
            logger.info("   ✅ PyMuPDF 提取完成: %d 个元素", len(elements))
            return elements
        except Exception as e:
            logger.error("   ❌ PyMuPDF 提取失败: %s", e)
            return []

    if backend == "pypdf":
        try:
            import io

            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(file_content))
            pages = [(page.extract_text() or "") for page in reader.pages]
            elements = _to_elements(pages)
            logger.info("   ✅ pypdf 提取完成: %d 个元素", len(elements))
            return elements
        except Exception as e:
            logger.error("   ❌ pypdf 提取失败: %s", e)
            return []

    logger.error(
        "   ❌ 未安装任何 PDF 提取后端。请安装 pypdf（pip install pypdf），"
        "或安装 PyMuPDF 以获得更好的解析效果（注意其 AGPL 许可）"
    )
    return []


def get_page_count(path_or_bytes) -> Optional[int]:
    """
    获取 PDF 页数。传入文件路径或字节内容均可。
    无可用后端时返回 None。
    """
    backend = available_backend()

    if backend == "pymupdf":
        try:
            import fitz

            if isinstance(path_or_bytes, (bytes, bytearray)):
                doc = fitz.open(stream=path_or_bytes, filetype="pdf")
            else:
                doc = fitz.open(path_or_bytes)
            try:
                return doc.page_count
            finally:
                doc.close()
        except Exception as e:
            logger.warning("获取 PDF 页数失败: %s", e)
            return None

    if backend == "pypdf":
        try:
            import io

            from pypdf import PdfReader

            source = (
                io.BytesIO(path_or_bytes)
                if isinstance(path_or_bytes, (bytes, bytearray))
                else path_or_bytes
            )
            return len(PdfReader(source).pages)
        except Exception as e:
            logger.warning("获取 PDF 页数失败: %s", e)
            return None

    return None
