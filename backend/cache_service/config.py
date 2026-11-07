"""
缓存 TTL 配置文件

所有缓存的过期时间（TTL）统一在此管理，便于调整优化。
单位：秒
"""

# ==================== 后端缓存 TTL ====================

class CacheTTL:
    """缓存过期时间配置（秒）"""
    
    # ---------- 文档相关 ----------
    # 文档列表：变化相对频繁，但不需要实时
    DOCUMENT_LIST = 120  # 2 分钟
    
    # 文档内容：变化少，可以较长
    DOCUMENT_CONTENT = 600  # 10 分钟
    
    # 文档处理状态：处理中需要频繁刷新
    DOCUMENT_STATUS_PROCESSING = 5  # 5 秒（处理中）
    DOCUMENT_STATUS_COMPLETED = 60  # 1 分钟（已完成）

    # ---------- 生成内容历史 ----------
    # 这些是 AI 生成的内容，生成成本高，应该缓存更久
    MINDMAP_HISTORY = 300  # 5 分钟
    FLASHCARDS_HISTORY = 300  # 5 分钟
    FLOWCHART_HISTORY = 300  # 5 分钟
    HIGHLIGHTS_HISTORY = 300  # 5 分钟
    QUIZ_HISTORY = 300  # 5 分钟
    INTERVIEW_HISTORY = 300  # 5 分钟
    
    # ---------- 聊天相关 ----------
    # 聊天历史：用户可能频繁添加新消息
    CHAT_HISTORY = 120  # 2 分钟
    
    # ---------- 笔记相关 ----------
    # 用户笔记：编辑频繁
    USER_NOTES = 120  # 2 分钟
    
    # ---------- 用户行为 ----------
    # 用户文档访问历史
    USER_DOCUMENT_HISTORY = 300  # 5 分钟
    
    # ---------- 知识图谱 ----------
    KNOWLEDGE_GRAPH = 600  # 10 分钟
    EUREKA_LIST = 180  # 3 分钟
    
    # ---------- 默认值 ----------
    DEFAULT = 180  # 3 分钟


# 导出便捷访问
CACHE_TTL = CacheTTL()
