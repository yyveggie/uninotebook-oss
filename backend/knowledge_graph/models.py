from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float, UniqueConstraint, Index, JSON
from sqlalchemy.sql import func

from database import Base


class EurekaMoment(Base):
    """用户保存的灵感时刻 (Eureka Moment)"""
    __tablename__ = "eureka_moments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    
    # 存储相关的节点信息 [{"id": 1, "label": "xx"}, ...]
    source_nodes = Column(JSON, default=list)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class KnowledgeGapAnalysis(Base):
    """用户保存的知识间隙分析结果"""
    __tablename__ = "knowledge_gap_analyses"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    gaps = Column(JSON, default=list)
    communities = Column(JSON, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now())


class KnowledgeTerm(Base):
    """用户级知识图谱中的关键词节点

    按用户隔离，每个 (user_id, term) 唯一。
    term 用于归一化去重（例如全部小写），display_term 保留原始展示形式。
    """

    __tablename__ = "knowledge_terms"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # 归一化后的术语（例如全部小写，用于去重）
    term = Column(String(255), nullable=False, index=True)
    # 展示用术语，保留中英文、大小写等
    display_term = Column(String(255), nullable=False)

    # 可选的合并说明（来自多篇文档时可以做简单拼接或覆盖）
    description = Column(Text, nullable=True)

    # 被多少篇文档引用（DocumentKnowledgeTerm 计数缓存）
    document_count = Column(Integer, default=0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "term", name="uq_user_term"),
    )


class DocumentKnowledgeTerm(Base):
    """文档与关键词的关联关系

    一条记录表示：某用户的一篇文档中包含某个关键词节点。
    """

    __tablename__ = "document_knowledge_terms"

    id = Column(Integer, primary_key=True, index=True)

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    term_id = Column(Integer, ForeignKey("knowledge_terms.id", ondelete="CASCADE"), nullable=False, index=True)

    # 预留字段，后续可以根据重要程度调整图布局
    importance = Column(Float, default=1.0)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("document_id", "term_id", name="uq_doc_term"),
        Index("ix_dkt_user_term", "user_id", "term_id"),
        Index("ix_dkt_user_doc", "user_id", "document_id"),
    )
