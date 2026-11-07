"""Core parsers: no OCR, cloud SDKs or optional inference packages at import time."""
from __future__ import annotations

import asyncio
import io
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from .parser_base import IDocumentParser


class SimpleParser(IDocumentParser):
    supported_extensions = {".pdf", ".docx", ".txt", ".md", ".markdown", ".csv", ".html", ".htm", ""}

    async def parse(self, file_bytes: bytes, filename: str) -> list[dict]:
        if Path(filename).suffix.lower() not in self.supported_extensions:
            try:
                from .parser_unstructured import UnstructuredParser
            except ImportError as exc:
                raise ValueError("该格式需要高级解析依赖，请安装 uninotebook[parsing] 后重试") from exc
            return await UnstructuredParser().parse(file_bytes, filename)
        return await asyncio.to_thread(self._parse, file_bytes, filename)

    def _parse(self, file_bytes: bytes, filename: str) -> list[dict]:
        suffix = Path(filename).suffix.lower()
        if suffix == ".pdf":
            from .pdf_text import extract_text_elements

            return extract_text_elements(file_bytes)
        if suffix == ".docx":
            with ZipFile(io.BytesIO(file_bytes)) as archive:
                info = archive.getinfo("word/document.xml")
                if info.file_size > 50 * 1024 * 1024:
                    raise ValueError("DOCX 正文过大，请拆分后上传")
                root = ElementTree.fromstring(archive.read(info))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text = "\n\n".join(
                "".join(p.itertext()) for p in root.findall(".//w:p", ns)
            )
        elif suffix in {".txt", ".md", ".markdown", ".csv", ".html", ".htm", ""}:
            try:
                text = file_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = file_bytes.decode("gb18030")
            if suffix in {".html", ".htm"}:
                import trafilatura

                text = trafilatura.extract(text, include_tables=True) or ""
        else:
            raise ValueError("该格式需要高级解析依赖，请安装 uninotebook[parsing] 后重试")
        return [
            {"text": part.strip(), "category": "NarrativeText", "metadata": {"paragraph_index": i}}
            for i, part in enumerate(text.split("\n\n"))
            if part.strip()
        ]
