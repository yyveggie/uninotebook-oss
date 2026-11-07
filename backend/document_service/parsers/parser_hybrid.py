from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .parser_base import IDocumentParser
from .parser_unstructured import UnstructuredParser
from utils.logger import get_logger
logger = get_logger(__name__)

try:
    import fitz  # type: ignore
except ImportError:  # pragma: no cover - 依赖可选
    fitz = None  # type: ignore

try:
    import camelot  # type: ignore
except ImportError:  # pragma: no cover
    camelot = None  # type: ignore

try:
    from pix2tex.cli import LatexOCR  # type: ignore
except ImportError:  # pragma: no cover
    LatexOCR = None

@dataclass
class _ImageElement:
    page: int
    bbox: List[float]
    image_bytes: bytes


class HybridParser(IDocumentParser):
    """增强型解析：优先使用 PyMuPDF + Camelot + Pix2Tex，失败时回退到 Unstructured。"""

    def __init__(
        self,
        fallback: Optional[IDocumentParser] = None,
        extract_tables: bool = True,
        extract_figures: bool = True,
        extract_formulas: bool = True,
    ):
        self.fallback = fallback or UnstructuredParser()
        self._latex_ocr = LatexOCR() if LatexOCR else None
        self.extract_tables = extract_tables and camelot is not None
        self.extract_figures = extract_figures
        self.extract_formulas = extract_formulas and self._latex_ocr is not None

    async def parse(self, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        suffix = (os.path.splitext(filename)[1] or "").lower()
        if suffix != ".pdf" or fitz is None:
            logger.info("[HybridParser] 非 PDF 或未安装 PyMuPDF，回退到 Unstructured 解析。")
            return await self.fallback.parse(file_bytes, filename)

        loop = asyncio.get_event_loop()

        # 并行执行：Unstructured 解析 + PyMuPDF 深度解析
        # 注意：直接使用 asyncio.gather() 而不是 create_task()，
        # 在 Celery prefork 模式下更稳定
        base, extras = await asyncio.gather(
            self.fallback.parse(file_bytes, filename),
            loop.run_in_executor(None, self._parse_pdf_sync, file_bytes)
        )

        if not base:
            base = []

        return self._merge_results(base, extras)

    def _parse_pdf_sync(self, file_bytes: bytes) -> List[Dict[str, Any]]:
        assert fitz is not None  # for type checker

        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
        except Exception as exc:
            logger.info(f"[HybridParser] 打开 PDF 失败，回退到基础解析: {exc}")
            return []

        aggregated: List[Dict[str, Any]] = []

        # 需要临时文件供 Camelot 使用
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_pdf:
            tmp_pdf.write(file_bytes)
            tmp_pdf_path = tmp_pdf.name

        try:
            for page_index, page in enumerate(doc, start=1):
                # 表格识别
                if self.extract_tables:
                    aggregated.extend(
                        self._extract_tables(tmp_pdf_path, page_index)
                    )

                # 图片 / 公式
                if self.extract_figures or self.extract_formulas:
                    aggregated.extend(self._extract_media(doc, page, page_index))

            return aggregated if aggregated else []
        except Exception as exc:
            logger.info(f"[HybridParser] PDF 深度解析失败: {exc}")
            return []
        finally:
            if os.path.exists(tmp_pdf_path):
                os.unlink(tmp_pdf_path)

    def _extract_tables(self, pdf_path: str, page_number: int) -> List[Dict[str, Any]]:
        if not self.extract_tables or camelot is None:
            return []
        try:
            tables = camelot.read_pdf(
                pdf_path,
                pages=str(page_number),
                flavor="lattice",
                suppress_stdout=True,
            )
        except Exception as exc:  # pragma: no cover - Camelot 可选
            logger.info(f"[HybridParser] Camelot 解析失败: {exc}")
            return []

        elements: List[Dict[str, Any]] = []
        for idx, table in enumerate(tables):
            table_json = table.df.fillna("").values.tolist()
            elements.append(
                {
                    "id": f"p{page_number}_table_{idx}",
                    "category": "Table",
                    "text": json.dumps(table_json, ensure_ascii=False),
                    "metadata": {
                        "page": page_number,
                        "bbox": getattr(table, "bbox", None),
                    },
                }
            )
        return elements

    def _extract_media(self, doc, page, page_number: int) -> List[Dict[str, Any]]:
        if not (self.extract_figures or self.extract_formulas):
            return []

        results: List[Dict[str, Any]] = []
        image_elements: List[_ImageElement] = []

        for image_info in page.get_images(full=True):
            xref = image_info[0]
            base_image = doc.extract_image(xref)
            if not base_image:
                continue
            image_bytes = base_image.get("image", b"")
            bbox = page.get_image_bbox(image_info)
            image_elements.append(
                _ImageElement(page=page_number, bbox=list(bbox), image_bytes=image_bytes)
            )

        for idx, img in enumerate(image_elements):
            metadata = {
                "page": img.page,
                "bbox": img.bbox,
            }
            latex_value: Optional[str] = None
            if self.extract_formulas:
                latex_value = self._try_formula_recognition(img.image_bytes)

            include_figure = self.extract_figures
            include_formula = bool(latex_value)

            if not include_figure and not include_formula:
                continue

            base64_image = base64.b64encode(img.image_bytes).decode("utf-8")

            element: Dict[str, Any] = {
                "id": f"p{img.page}_figure_{idx}",
                "category": "Figure",
                "text": "",
                "metadata": {**metadata, "image_base64": base64_image},
            }

            if include_formula:
                element["category"] = "Formula"
                element["text"] = latex_value
            elif not include_figure:
                # 只保留公式结果
                continue

            results.append(element)

        return results

    def _try_formula_recognition(self, image_bytes: bytes) -> Optional[str]:
        if not self.extract_formulas or self._latex_ocr is None:
            return None
        try:
            image_stream = io.BytesIO(image_bytes)
            formula = self._latex_ocr(image_stream)
            return formula.strip() if formula else None
        except Exception as exc:  # pragma: no cover - 公式识别可选
            logger.info(f"[HybridParser] 公式识别失败: {exc}")
            return None

    def _merge_results(
        self,
        base: List[Dict[str, Any]],
        extras: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not extras:
            return base

        merged = list(base)
        existing_ids = {element.get("id") for element in base}
        for extra in extras:
            if extra.get("id") not in existing_ids:
                merged.append(extra)
        return merged


