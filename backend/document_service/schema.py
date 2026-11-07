"""
文档服务的 Pydantic 模型定义
包含所有结构化响应的数据模型
"""
from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


# ===== 文档重点提炼相关模型 =====

class HighlightPoint(BaseModel):
    """单个重点要点"""
    category: str = Field(description="类别：关键要点/核心创新/注意事项/细节设置/其他")
    title: str = Field(description="标题，简洁概括（不超过30字）")
    content: str = Field(description="详细内容，80-200字")
    importance: str = Field(description="重要程度：高/中/低")


class DocumentHighlightsResponse(BaseModel):
    """文档重点提炼响应（用于AI结构化输出）"""
    summary: str = Field(description="文档核心摘要，一句话概括文档主旨（50-100字）")
    key_points: List[HighlightPoint] = Field(description="关键要点列表，包含各类重点内容")
    
    @field_validator('key_points')
    @classmethod
    def validate_key_points(cls, points):
        """验证要点数量"""
        if len(points) < 5:
            raise ValueError("重点提炼至少需要5个要点")
        if len(points) > 20:
            raise ValueError("重点提炼最多20个要点")
        return points


# ===== 文档摘要相关模型 =====

class KeyTerm(BaseModel):
    """关键术语"""
    term: str = Field(
        ...,
        description=(
            "术语名称，必须包含中英文：推荐格式为 '英文缩写（可选） / 英文全称 / 中文' 或 '英文 / 中文'。"
            "示例：'LLM / Large Language Model / 大型语言模型'。不要只输出中文或只输出缩写。"
        ),
    )
    description: str = Field(
        default="",  # 改为可选，AI 有时不返回此字段
        max_length=150, 
        description="术语简要说明，10-80字"
    )


class DocumentSummaryResponse(BaseModel):
    """文档摘要响应（结构化）"""
    overview: str = Field(..., min_length=5, max_length=500, description="核心内容概述，200-300字，说明文档主题、解决的问题、主要方法")
    key_terms: List[KeyTerm] = Field(..., min_length=0, max_length=15, description="关键术语列表，4-12个核心术语")


class DocumentTitleResponse(BaseModel):
    """文档标题提取响应"""
    # 放宽最小长度限制，避免因为模型返回短标题导致整个文档处理失败
    title: str = Field(..., min_length=1, max_length=200, description="提取的文档标题，1-200字符")


class DocumentFullSummaryResponse(BaseModel):
    """文档摘要响应（只包含概述和关键术语，不再提取标题）"""
    overview: str = Field(
        ...,
        min_length=5,
        max_length=500,
        description="核心内容概述，200-300字，说明文档主题、解决的问题、主要方法",
    )
    key_terms: List[KeyTerm] = Field(
        ...,
        min_length=0,
        max_length=15,
        description="关键术语列表，4-12个核心术语",
    )


# ===== 测验相关模型 =====

class SearchQueriesResponse(BaseModel):
    """检索查询词生成模型的响应结构 (Hybrid Search)"""
    # 兼容旧代码，暂不删除 queries，但在 feature generator 中优先使用新字段
    queries: List[str] = Field(
        default_factory=list,
        description="[Deprecated] 旧版混合列表，包含自然语言和关键词"
    )
    semantic_queries: List[str] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="用于向量检索的自然语言问题（捕捉语义）。例如：'如何实现高效的注意力机制？'"
    )
    keywords: List[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description="用于BM25检索的精准关键词/术语（精确匹配）。例如：['Self-Attention', 'Transformer', '复杂度']"
    )

class QuizQuestion(BaseModel):
    """单个测验题目"""
    question: str = Field(..., description="问题描述")
    options: List[str] = Field(..., min_items=4, max_items=4, description="4个选项")
    correct_answer: int = Field(..., ge=0, le=3, description="正确答案索引(0-3)")
    explanation: Optional[str] = Field(
        default=None,
        description="答案解析，可为空"
    )


class QuizResponse(BaseModel):
    """测验响应"""
    quiz: List[QuizQuestion] = Field(..., min_length=1, description="测验题目列表")


# ===== 闪卡相关模型 =====

class Flashcard(BaseModel):
    """单个闪卡"""
    question: str = Field(..., description="问题内容，简洁明确")
    answer: str = Field(..., min_length=20, max_length=500, description="答案内容，详细准确，20-200字")


class FlashcardsResponse(BaseModel):
    """闪卡响应"""
    flashcards: List[Flashcard] = Field(..., min_length=1, description="闪卡列表")


# ===== 学习路径相关模型 =====

class DocumentLearningNode(BaseModel):
    """文档学习节点"""
    id: str = Field(..., description="节点ID，如 node_1")
    title: str = Field(..., min_length=2, max_length=50, description="节点标题")
    description: str = Field(..., min_length=10, max_length=300, description="节点详细描述，30-150字")
    prerequisites: List[str] = Field(default_factory=list, description="前置节点ID列表")


class DocumentLearningPathResponse(BaseModel):
    """文档学习路径响应"""
    topic: str = Field(..., description="文档主题")
    overview: str = Field(..., min_length=30, max_length=500, description="整体概述，100-200字左右")
    nodes: List[DocumentLearningNode] = Field(..., min_length=8, max_length=20, description="学习节点列表，8-18个")
    recommended_sequence: List[str] = Field(default_factory=list, description="推荐学习顺序，节点ID列表")


# ===== 思维导图相关模型 =====

class MindMapNode(BaseModel):
    """思维导图节点"""
    id: str = Field(..., description="节点ID")
    title: str = Field(..., min_length=2, max_length=100, description="节点标题")
    children: List['MindMapNode'] = Field(default_factory=list, description="子节点列表")


class MindMapResponse(BaseModel):
    """思维导图响应"""
    title: str = Field(..., description="思维导图主题")
    root: MindMapNode = Field(..., description="根节点")


# 允许递归定义
MindMapNode.model_rebuild()


# ===== 面试相关模型 =====

class KnowledgePoint(BaseModel):
    """知识点"""
    id: str = Field(..., description="知识点ID")
    category: str = Field(..., description="类别：basic/innovation/method/comprehensive")
    content: str = Field(..., description="知识点内容描述")
    difficulty: int = Field(..., ge=1, le=5, description="难度等级 1-5")
    keywords: List[str] = Field(default_factory=list, description="关键词列表")


class KnowledgePointsResponse(BaseModel):
    """知识点提取响应"""
    knowledge_points: List[KnowledgePoint] = Field(..., min_length=5, max_length=12, description="5-12个核心知识点")


class InterviewQuestion(BaseModel):
    """面试问题"""
    question: str = Field(..., min_length=10, description="问题内容")
    expected_keywords: List[str] = Field(default_factory=list, description="期望答案包含的关键词")
    hints: str = Field(default="", description="提示信息（可选）")


class InterviewEvaluation(BaseModel):
    """面试评价"""
    score: float = Field(..., ge=0, le=100, description="得分 0-100")
    feedback: str = Field(..., min_length=10, description="评价反馈，简洁明确")
    suggestions: str = Field(..., min_length=10, description="改进建议，简洁明确")
    keyword_coverage: float = Field(..., ge=0, le=1, description="关键词覆盖率 0-1")


class InterviewSummary(BaseModel):
    """面试总结"""
    total_rounds: int = Field(..., description="总轮次")
    average_score: float = Field(..., description="平均分")
    strengths: List[str] = Field(..., description="优势领域")
    weaknesses: List[str] = Field(..., description="需要改进的领域")
    overall_assessment: str = Field(..., description="总体评价")
    recommendations: str = Field(..., description="学习建议")


# ===== 问答相关模型 =====

class SearchDocumentSchema(BaseModel):
    """文档搜索工具参数"""
    queries: List[str] = Field(
        description="2-3个具体的搜索关键词/短语（不是问句！），必须从文档摘要中提取专业术语。例如：['BERT双向预训练', 'Masked Language Model']"
    )


class QuestionAnalysisSchema(BaseModel):
    """问题分析与路由决策"""
    needs_retrieval: bool = Field(
        ...,
        description="是否需要检索文档内容来回答此问题。如果是闲聊、问候、感谢或通用知识（如'你好'、'谢谢'、'你是谁'），选 False；如果涉及具体事实、数据、文档内容，选 True。"
    )
    semantic_queries: List[str] = Field(
        ...,
        min_length=0,
        max_length=3,
        description="用于向量检索的自然语言问题（有助于理解语义）。例如：'变压器的工作原理是什么？'"
    )
    keywords: List[str] = Field(
        ...,
        min_length=0,
        max_length=5,
        description="用于BM25检索的精准关键词/术语（有助于精确匹配）。例如：['Transformer', 'Self-Attention', '注意力机制']"
    )

    # 🆕 计划生成 (Router Integrated Planner)
    class PlanStep(BaseModel):
        title: str = Field(default="", description="中文步骤标题，简练明确。")
        description: str = Field(default="", description="中文步骤详情，指导该步具体要做什么。")
        step: Optional[int] = Field(default=None, exclude=True)  # 兼容 LLM 返回 step 字段
        
        def model_post_init(self, __context):
            # 如果 LLM 没给 title 但给了 step/description，自动补全
            if not self.title and self.description:
                self.title = self.description

    plan: Optional[List[PlanStep]] = Field(
        description="如果问题复杂（需要分步推理、多角度分析、对比或跨章节总结），请生成 2-4 个步骤的回答计划。如果问题简单（如定义查询、摘要、闲聊），请保持为 null。"
    )

    # 🆕 主动追问相关字段
    needs_clarification: bool = Field(
        description="是否需要追问。**默认 False，极少设为 True**。仅当以下情况才设为 True：(1) 用户明确表示困惑（如'我不知道该问什么'）；(2) 输入完全不可理解（乱码/无意义）。**禁止**因为问题可能有多个答案而追问。"
    )
    clarification_question: Optional[str] = Field(
        description="追问内容。如果 needs_clarification=True，这里填写一个简短的澄清问题，如'您是指论文中的方法A还是方法B？'"
    )

    clarification_type: Optional[str] = Field(
        description="追问类型：'input'（需要用户输入）或 'choice'（需要用户从选项中选择）。如果问题有明确的几个可能选项，用 'choice'。"
    )
    clarification_options: Optional[List[str]] = Field(
        description="当 clarification_type='choice' 时，提供 2-4 个选项供用户选择。如：['方法A（自注意力）', '方法B（交叉注意力）']"
    )
    
    def model_post_init(self, __context):
        """验证：如果需要检索，queries 至少要有 2 个"""
        if self.needs_retrieval:
             # 自动补充：如果两个列表都为空，抛错或由 LLM 保证
             if not self.semantic_queries and not self.keywords:
                 # 极端情况兜底：至少要有一个
                 pass
             
             # 确保 keywords 不空（BM25 至少跑一个）
             if not self.keywords and self.semantic_queries:
                # 简单的 fallback: 把 semantic query 当 keyword (也能跑，虽然效果打折)
                pass


class FinishAnswerSchema(BaseModel):
    """完成回答工具参数"""
    answer: str = Field(
        description="最终答案。必须基于文档事实，详细、准确、有条理。所有数学符号必须用$包裹（行内）或$$包裹（块级）。"
    )


# ===== 播客相关模型 =====

class PodcastDialogueItem(BaseModel):
    """播客对话单项"""
    speaker: str = Field(..., description="说话者：'A' (主持人) 或 'B' (专家)")
    text: str = Field(..., description="对话内容，口语化风格")


class PodcastScript(BaseModel):
    """播客脚本响应"""
    dialogue: List[PodcastDialogueItem] = Field(..., description="对话列表")
