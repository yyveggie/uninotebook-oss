from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class IDocumentParser(ABC):
    """统一的文档解析接口。"""

    @abstractmethod
    async def parse(self, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        """解析文件并返回结构化元素列表。"""


