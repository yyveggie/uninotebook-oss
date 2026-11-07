"""
数据库模型定义
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON, Float, Index, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    phone = Column(String(20), unique=True, index=True, nullable=True)
    username = Column(String(100), index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    is_vip = Column(Boolean, default=False)
    my_invite_code = Column(String(20), unique=True, index=True, nullable=True)  # 用户的专属邀请码
    invited_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)       # 邀请人的ID
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_login = Column(DateTime(timezone=True), nullable=True)
    

class IssueReport(Base):
    """问题报告表"""
    __tablename__ = "issue_reports"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    content = Column(Text, nullable=False)
    images = Column(JSON, default=list)  # 存储图片URL列表
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    user = relationship("User")


class EmailVerificationCode(Base):
    """邮箱验证码表"""
    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, index=True)
    code = Column(String(10), nullable=False)
    purpose = Column(String(50), nullable=False, default='register')
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)


class SmsVerificationCode(Base):
    """短信验证码表"""
    __tablename__ = "sms_verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    phone = Column(String(20), nullable=False, index=True)
    code = Column(String(10), nullable=False)
    purpose = Column(String(50), nullable=False, default='register')
    is_used = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=False)
    verified_at = Column(DateTime(timezone=True), nullable=True)


class LoginHistory(Base):
    """登录历史表（安全审计）"""
    __tablename__ = "login_history"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    ip_address = Column(String(45), nullable=True)  # 支持IPv6
    user_agent = Column(Text, nullable=True)
    login_successful = Column(Boolean, default=True)
    login_at = Column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    """文档表（用于文档学习模式）"""
    __tablename__ = "documents"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # 上传者/所有者
    filename = Column(String(255), nullable=False)  # 原始文件名

    display_name = Column(String(255), nullable=True)  # 自定义显示名称
    file_type = Column(String(50), nullable=False)  # 'pdf' or 'text'
    original_text = Column(Text, nullable=False)  # 完整文本内容
    summary = Column(Text, nullable=True)  # AI生成的文档摘要
    chunk_count = Column(Integer, default=0)  # 切块数量
    vector_store_id = Column(String(255), nullable=False)  # ChromaDB collection ID
    file_size = Column(Integer, nullable=True)  # 文件大小（字节）
    file_hash = Column(String(64), nullable=True, index=True)  # 文件内容SHA256哈希，用于识别相同文件
    extracted_title = Column(Text, nullable=True)  # LLM 提取的文档标题，用于相似度匹配
    original_file_cos_key = Column(String(500), nullable=True)  # 原始文件在 COS 中的对象路径
    
    # 异步处理状态
    processing_status = Column(String(20), default='pending', index=True)  # pending | processing | completed | failed
    processing_error = Column(Text, nullable=True)  # 处理失败时的错误信息
    processing_progress = Column(Integer, default=0)  # 处理进度 0-100
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_accessed_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # 关系（级联删除所有关联数据）
    # passive_deletes=True: 在 async 模式下避免懒加载导致的 MissingGreenlet 错误，
    # 信任数据库的 ON DELETE CASCADE 来级联删除关联行
    chat_history = relationship("DocumentChatHistory", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    learning_paths = relationship("DocumentLearningPath", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    flashcards = relationship("DocumentFlashcards", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    quizzes = relationship("DocumentQuiz", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    mindmaps = relationship("DocumentMindmap", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    highlights = relationship("DocumentHighlights", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    reports = relationship("DocumentReport", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    interview_records = relationship("InterviewRecord", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    notes = relationship("Note", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)
    sources = relationship("DocumentSource", back_populates="document", cascade="all, delete-orphan", passive_deletes=True)


class DocumentSource(Base):
    """文档来源表（用于多来源检索）"""
    __tablename__ = "document_sources"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    source_type = Column(String(50), nullable=False, default="text")  # text | web | web_research | file
    source_name = Column(String(255), nullable=False)
    source_url = Column(String(1000), nullable=True)
    source_preview = Column(Text, nullable=True)
    vector_store_id = Column(String(255), nullable=True)
    chunk_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document", back_populates="sources")


class DocumentChatHistory(Base):
    """文档问答历史表"""
    __tablename__ = "document_chat_history"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String(100), nullable=True, index=True)  # 会话ID，用于分组对话
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    relevant_chunks = Column(JSON, nullable=True)  # 检索到的相关文档片段
    search_count = Column(Integer, nullable=True)  # 查询次数
    elapsed_time = Column(Float, nullable=True)  # 消耗时间（秒）
    
    # 🆕 追问相关字段
    is_clarification = Column(Boolean, default=False, nullable=True)  # 是否为追问消息
    clarification_options = Column(JSON, nullable=True)  # 追问选项列表
    selected_option = Column(String(255), nullable=True)  # 用户选择的选项
    
    # 🆕 状态历史和图表数据
    status_history = Column(JSON, nullable=True)  # 状态历史记录
    flowchart = Column(JSON, nullable=True)  # 流程图数据
    chart_data = Column(JSON, nullable=True)  # 图表数据
    plan = Column(JSON, nullable=True)  # 🆕 执行计划数据
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    document = relationship("Document", back_populates="chat_history")

    __table_args__ = (
        # 高频查询：按用户+文档(+会话)读取历史并按时间排序
        Index("ix_dch_user_doc_created", "user_id", "document_id", "created_at"),
        Index("ix_dch_user_doc_session_created", "user_id", "document_id", "session_id", "created_at"),
    )


class ConversationSummary(Base):
    """对话运行摘要表 — 持久化保存 LLM 生成的会话摘要（Redis 作缓存层）"""
    __tablename__ = "conversation_summaries"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    session_id = Column(String(100), nullable=False)
    summary = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_cs_user_doc_session", "user_id", "document_id", "session_id", unique=True),
    )


class DocumentFlashcards(Base):
    """文档闪卡表（支持多次生成历史记录）"""
    __tablename__ = "document_flashcards"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False, default='闪卡')
    flashcards_data = Column(JSON, nullable=False)  # 闪卡数据 [{"question": "", "answer": ""}]
    card_count = Column(Integer, default=0)  # 卡片数量
    version = Column(Integer, default=1)  # 版本号，支持多次生成
    is_current = Column(Boolean, default=True)  # 是否为当前使用的版本
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    document = relationship("Document", back_populates="flashcards")


class DocumentLearningPath(Base):
    """文档学习路径表（支持多次生成历史记录）"""
    __tablename__ = "document_learning_paths"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False, default='学习路径')
    learning_path_data = Column(JSON, nullable=False)
    node_count = Column(Integer, default=0)
    version = Column(Integer, default=1)
    is_current = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document", back_populates="learning_paths")


class DocumentQuiz(Base):
    """文档测验表（支持多次生成历史记录）"""
    __tablename__ = "document_quiz"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False, default='测验')
    quiz_data = Column(JSON, nullable=False)  # 测验数据 [{"question": "", "options": ["A", "B", "C", "D"], "correct_answer": 0}]
    question_count = Column(Integer, default=0)  # 问题数量
    version = Column(Integer, default=1)  # 版本号，支持多次生成
    is_current = Column(Boolean, default=True)  # 是否为当前使用的版本
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    document = relationship("Document", back_populates="quizzes")


class DocumentMindmap(Base):
    """文档思维导图表（支持多次生成历史记录）"""
    __tablename__ = "document_mindmaps"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False, default='思维导图')
    mindmap_data = Column(JSON, nullable=False)  # 思维导图数据 {"title": "", "root": {...}}
    node_count = Column(Integer, default=0)  # 节点数量
    version = Column(Integer, default=1)  # 版本号，支持多次生成
    is_current = Column(Boolean, default=True)  # 是否为当前使用的版本
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    document = relationship("Document", back_populates="mindmaps")


class DocumentHighlights(Base):
    """文档重点提炼表（支持多次生成历史记录）"""
    __tablename__ = "document_highlights"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False, default='重点提炼')
    highlights_data = Column(JSON, nullable=False)  # 重点提炼数据 {"key_points": [...], "innovations": [...], ...}
    point_count = Column(Integer, default=0)  # 要点总数
    version = Column(Integer, default=1)  # 版本号，支持多次生成
    is_current = Column(Boolean, default=True)  # 是否为当前使用的版本
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    document = relationship("Document", back_populates="highlights")


class DocumentReport(Base):
    """文档长报告表（支持多次生成历史记录）"""
    __tablename__ = "document_reports"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False, default='长报告')
    report_markdown = Column(Text, nullable=False)
    report_data = Column(JSON, nullable=True)  # outline/sections/sources 等结构化信息
    word_count = Column(Integer, default=0)
    version = Column(Integer, default=1)
    is_current = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    document = relationship("Document", back_populates="reports")


class InterviewRecord(Base):
    """面试记录表"""
    __tablename__ = "interview_records"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String(100), unique=True, nullable=False, index=True)
    title = Column(String(500), default='面试记录')  # 面试记录标题
    
    # 面试基本信息
    total_rounds = Column(Integer, nullable=False)
    average_score = Column(Float, nullable=False)
    status = Column(String(20), default='completed')  # completed | aborted
    
    # 面试详细数据（JSON格式）
    qa_history = Column(JSON, nullable=False)  # 问答历史
    summary = Column(JSON, nullable=True)  # 面试总结
    knowledge_points = Column(JSON, nullable=True)  # 知识点列表
    
    # 版本控制
    version = Column(Integer, default=1)
    is_current = Column(Boolean, default=True)
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    
    # 关系
    document = relationship("Document", back_populates="interview_records")
    user = relationship("User")


class Note(Base):
    """笔记表"""
    __tablename__ = "notes"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)  # 可选，关联文档（文档删除时级联删除）
    title = Column(String(255), nullable=False)  # 笔记标题
    content = Column(Text, nullable=False)  # 笔记内容
    type = Column(String(50), default='general')  # 笔记类型：general, ai_response, manual
    is_shared = Column(Boolean, default=False, index=True)  # 是否公开共享
    shared_at = Column(DateTime(timezone=True), nullable=True)  # 共享时间
    source_note_id = Column(Integer, nullable=True, index=True)  # 来源笔记ID（从共享笔记复制而来）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    user = relationship("User")
    document = relationship("Document", back_populates="notes")


class EurekaChatHistory(Base):
    """灵感对话历史表"""
    __tablename__ = "eureka_chat_history"
    
    id = Column(Integer, primary_key=True, index=True)
    eureka_id = Column(Integer, nullable=False, index=True)  # 灵感ID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_id = Column(String(100), nullable=True, index=True)  # 会话ID，用于分组对话
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    user = relationship("User")


class EurekaNote(Base):
    """灵感笔记表"""
    __tablename__ = "eureka_notes"
    
    id = Column(Integer, primary_key=True, index=True)
    eureka_id = Column(Integer, nullable=False, index=True)  # 灵感ID
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    user = relationship("User")


class InviteCode(Base):
    """邀请码表"""
    __tablename__ = "invite_codes"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    max_uses = Column(Integer, default=1)  # 最大使用次数
    used_count = Column(Integer, default=0)  # 已使用次数
    is_active = Column(Boolean, default=True)  # 是否启用
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 过期时间


class DocumentPPT(Base):
    """文档PPT生成记录表"""
    __tablename__ = "document_ppts"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 基本信息
    title = Column(String(255), nullable=False, default='Generated PPT')
    style = Column(String(50), default='academic')  # academic, doraemon, business...
    
    # 状态追踪
    status = Column(String(20), default='pending', index=True)  # pending, processing, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    current_stage = Column(String(50), nullable=True)  # rag, summary, plan, generate
    error_message = Column(Text, nullable=True)
    
    # Internal Pipeline Data
    session_id = Column(String(100), nullable=True, index=True) # Pipeline Session ID
    source_file_path = Column(String(500), nullable=True) # Input file path
    file_size = Column(Integer, default=0) # Input file size
    
    # 结果存储
    ppt_file_path = Column(String(500), nullable=True)  # 生成的PPT文件相对路径
    preview_images = Column(JSON, nullable=True)  # 预览图片列表 (前几页)
    
    # PPT 结构数据 (可选，用于后续编辑)
    outline = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    document = relationship("Document")
    user = relationship("User")


class DocumentPoster(Base):
    """文档海报生成记录表 (独立表)"""
    __tablename__ = "document_posters"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 基本信息
    title = Column(String(255), nullable=False, default='Generated Poster')
    style = Column(String(50), default='academic')  # academic, flat, etc.
    
    # 状态追踪
    status = Column(String(20), default='pending', index=True)  # pending, processing, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    current_stage = Column(String(50), nullable=True)  # rag, summary, plan, generate
    error_message = Column(Text, nullable=True)
    
    # Internal Pipeline Data
    session_id = Column(String(100), nullable=True, index=True) # Pipeline Session ID
    source_file_path = Column(String(500), nullable=True) # Input file path
    file_size = Column(Integer, default=0) # Input file size
    
    # 结果存储
    poster_file_path = Column(String(500), nullable=True)  # 生成的海报文件路径 (PDF/Image)
    preview_image = Column(String(500), nullable=True)     # 预览小图 URL
    
    # 结构数据 (可选)
    outline = Column(JSON, nullable=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    document = relationship("Document")
    user = relationship("User")


class DocumentPodcast(Base):
    """文档播客生成记录表"""
    __tablename__ = "document_podcasts"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    # 基本信息
    title = Column(String(255), nullable=False, default='Generated Podcast')
    style = Column(String(50), default='fun')  # fun, serious, summary
    style_label = Column(String(50), default='Fun') # For UI
    
    # 状态追踪
    status = Column(String(20), default='pending', index=True)  # pending, processing, completed, failed
    progress = Column(Integer, default=0)  # 0-100
    current_stage = Column(String(50), nullable=True)  # generating_script, generating_audio, merging
    error_message = Column(Text, nullable=True)
    
    # 结果存储
    script = Column(JSON, nullable=True)  # 对话脚本 [{"speaker": "A", "text": "..."}]
    audio_file_path = Column(String(500), nullable=True)  # 最终音频文件路径
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    document = relationship("Document")
    user = relationship("User")


class VisitLog(Base):
    """访问记录表"""
    __tablename__ = "visit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    visitor_id = Column(String(64), nullable=False, index=True)  # IP+UA的MD5哈希
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # 登录用户ID（可选）
    ip_address = Column(String(45), nullable=False)  # IP地址（支持IPv6）
    user_agent = Column(Text, nullable=True)  # User-Agent
    path = Column(String(500), nullable=False, index=True)  # 访问路径
    method = Column(String(10), nullable=False)  # HTTP方法（GET/POST等）
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)  # 访问时间
    
    # 关系（可选）
    user = relationship("User")

    __table_args__ = (
        # 统计场景：按时间窗口查询用户/路径访问数据
        Index("ix_visit_logs_user_created", "user_id", "created_at"),
        Index("ix_visit_logs_path_created", "path", "created_at"),
    )


class UserPromptTemplate(Base):
    """用户提示词模板表"""
    __tablename__ = "user_prompt_templates"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(100), nullable=False)  # 显示名称，如"观点逻辑"
    icon = Column(String(10), nullable=True)     # Emoji图标，如"🧠"
    prompt = Column(Text, nullable=False)        # 完整提示词内容
    sort_order = Column(Integer, default=0)      # 排序顺序
    is_default = Column(Boolean, default=False)  # 是否为系统预设（首次复制的）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    user = relationship("User")


# ==================== 积分系统模型 ====================

class UserCredit(Base):
    """用户积分余额表"""
    __tablename__ = "user_credits"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    balance = Column(Integer, default=0, nullable=False)  # 当前余额
    total_earned = Column(Integer, default=0, nullable=False)  # 累计获得
    total_spent = Column(Integer, default=0, nullable=False)  # 累计消耗
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # 关系
    user = relationship("User")


class CreditTransaction(Base):
    """积分流水表"""
    __tablename__ = "credit_transactions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    amount = Column(Integer, nullable=False)  # 变动金额（正=获得，负=消耗）
    balance_after = Column(Integer, nullable=False)  # 交易后余额
    type = Column(String(50), nullable=False, index=True)  # 类型: qa/document/flashcard/quiz/mindmap/highlights/interview/ppt/poster/register/daily_checkin/invite/recharge
    description = Column(String(500), nullable=True)  # 描述
    reference_id = Column(String(100), nullable=True, index=True)  # 关联ID（如文档ID、订单号）
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # 数据库级防重复退款：同一用户、同一退款类型、同一关联 ID 只能退一次。
    # 应用层（tasks.utils.refund_task_credits_once）已先查重，这里兜住并发竞态。
    # 已有数据库的建索引方式见 migrations/add_refund_unique_index.py
    __table_args__ = (
        Index(
            "uq_credit_transactions_refund_ref",
            "user_id", "type", "reference_id",
            unique=True,
            postgresql_where=text("type LIKE '%refund' AND reference_id IS NOT NULL"),
            sqlite_where=text("type LIKE '%refund' AND reference_id IS NOT NULL"),
        ),
    )

    # 关系
    user = relationship("User")


class RechargeOrder(Base):
    """充值订单表"""
    __tablename__ = "recharge_orders"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    order_no = Column(String(64), unique=True, nullable=False, index=True)  # 订单号
    amount_cents = Column(Integer, nullable=False)  # 金额（分）
    credits = Column(Integer, nullable=False)  # 积分数
    package_type = Column(String(50), nullable=False)  # 套餐类型: basic/standard/advanced
    status = Column(String(20), default='pending', nullable=False, index=True)  # pending/paid/cancelled/expired
    payment_method = Column(String(20), nullable=True)  # wechat/alipay
    paid_at = Column(DateTime(timezone=True), nullable=True)  # 支付时间
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)  # 订单过期时间
    
    # 关系
    user = relationship("User")


class DailyCheckin(Base):
    """每日签到记录表"""
    __tablename__ = "daily_checkins"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    checkin_date = Column(DateTime(timezone=True), nullable=False, index=True)  # 签到日期
    credits_earned = Column(Integer, nullable=False)  # 获得的积分
    streak_days = Column(Integer, default=1)  # 连续签到天数（预留，后续可做连续签到奖励）
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    user = relationship("User")
    
    __table_args__ = (
        {'sqlite_autoincrement': True},
    )


class StudioDraft(Base):
    """创作空间草稿"""
    __tablename__ = "studio_drafts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(255), nullable=False, default="未命名文档.md")
    content = Column(Text, nullable=False, default="")
    source_type = Column(String(50), nullable=False, default="local")
    source_document_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User")

