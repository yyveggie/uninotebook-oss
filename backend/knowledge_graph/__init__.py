"""用户知识图谱相关模型与服务.

此模块仅导出 ORM 模型，避免在导入时引入复杂的依赖链。
运行时功能代码（service）请直接从 ``knowledge_graph.service`` 导入。
"""

from .models import KnowledgeTerm, DocumentKnowledgeTerm  # noqa: F401
from .utils import invalidate_kg_cache  # noqa: F401
