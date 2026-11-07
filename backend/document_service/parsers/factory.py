from __future__ import annotations

from functools import lru_cache

from config import settings

from .parser_base import IDocumentParser
from .parser_simple import SimpleParser
from utils.logger import get_logger
logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_document_parser() -> IDocumentParser:
    strategy = getattr(settings, "document_parser_strategy", "simple").lower()

    if strategy == "hybrid":
        try:
            from .parser_hybrid import HybridParser

            parser = HybridParser(
                extract_tables=getattr(settings, "hybrid_extract_tables", True),
                extract_figures=getattr(settings, "hybrid_extract_figures", True),
                extract_formulas=getattr(settings, "hybrid_extract_formulas", True),
            )
            logger.info("🔧 使用 Hybrid 文档解析策略 (PyMuPDF + Unstructured)")
            return parser
        except Exception as exc:  # pragma: no cover - 回退
            logger.warning(f"⚠️ 初始化 HybridParser 失败，回退到基础解析: {exc}")

    if strategy == "unstructured":
        try:
            from .parser_unstructured import UnstructuredParser

            return UnstructuredParser()
        except ImportError:
            logger.warning("高级解析依赖未安装，使用基础解析")
    return SimpleParser()

