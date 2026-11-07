"""
渐进式对话摘要更新器 (Progressive Summary Updater)

替代原有的 AU2 实时压缩算法。核心区别：
- AU2: 在 Agent 每次迭代时调用 LLM 压缩中间消息（关键路径上，高成本）
- 新方案: 在每次问答结束后，后台异步更新运行摘要（非关键路径，零延迟影响）

每次更新逻辑：
  new_summary = LLM(old_summary + 本轮 Q&A) 
  → 写入 Redis（供下次问答加载）
"""

from langchain_core.messages import HumanMessage
from utils.logger import get_logger
logger = get_logger(__name__)


class SummaryUpdater:
    """
    渐进式对话摘要更新器
    
    每次问答结束后调用 update()，将本轮对话追加到运行摘要中。
    LLM 调用在后台异步执行，不影响用户响应延迟。
    """
    
    def __init__(self, llm):
        """
        Args:
            llm: LangChain LLM 实例（建议使用低成本模型，如 router LLM）
        """
        self.llm = llm
    
    async def update(
        self,
        existing_summary: str,
        new_question: str,
        new_answer: str,
        max_summary_length: int = 800
    ) -> str:
        """
        将本轮问答追加到运行摘要中
        
        Args:
            existing_summary: 现有的运行摘要（可为空，表示第一轮）
            new_question: 本轮用户问题
            new_answer: 本轮 AI 回答
            max_summary_length: 摘要最大长度（字符）
            
        Returns:
            更新后的运行摘要
        """
        if not new_question or not new_answer:
            return existing_summary
        
        # 截断过长的回答（摘要不需要完整原文，只需要要点）
        answer_preview = new_answer[:1500]
        if len(new_answer) > 1500:
            answer_preview += "..."
        
        if existing_summary:
            prompt = (
                "你是一个对话记忆管理器。请将已有的对话摘要与最新一轮对话合并，生成更新后的摘要。\n\n"
                "**要求**：\n"
                "1. 保留之前摘要中的关键信息（事实、结论、用户偏好）\n"
                "2. 整合最新对话的新信息\n"
                "3. 如果新信息与旧信息矛盾，以新信息为准\n"
                "4. 摘要应简洁，重点记录：讨论过的核心主题、得出的关键结论、用户特别关注的方面\n"
                "5. 使用第三人称（'用户讨论了...'、'AI 解释了...'）\n"
                f"6. 控制在 {max_summary_length} 字符以内\n\n"
                f"【已有摘要】\n{existing_summary}\n\n"
                f"【最新一轮对话】\n"
                f"用户: {new_question}\n"
                f"AI: {answer_preview}\n\n"
                "【更新后的摘要】"
            )
        else:
            # 第一轮对话，直接生成初始摘要
            prompt = (
                "你是一个对话记忆管理器。请为以下对话生成一个简洁的摘要。\n\n"
                "**要求**：\n"
                "1. 记录讨论的核心主题和关键结论\n"
                "2. 使用第三人称（'用户询问了...'、'AI 解释了...'）\n"
                f"3. 控制在 {max_summary_length} 字符以内\n\n"
                f"【对话内容】\n"
                f"用户: {new_question}\n"
                f"AI: {answer_preview}\n\n"
                "【摘要】"
            )
        
        try:
            response = await self.llm.ainvoke([HumanMessage(content=prompt)])
            summary = str(response.content).strip()
            
            # 硬限制：防止 LLM 输出超长
            if len(summary) > max_summary_length:
                summary = summary[:max_summary_length] + "..."
            
            logger.info(f"✅ [SummaryUpdater] Summary updated: {len(existing_summary)} → {len(summary)} chars")
            return summary
            
        except Exception as e:
            logger.warning(f"⚠️ [SummaryUpdater] Update failed: {e}")
            # 失败时保留旧摘要，不中断流程
            return existing_summary or ""
