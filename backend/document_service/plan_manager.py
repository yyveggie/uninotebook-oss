from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class PlanStep:
    index: int
    title: str
    description: str
    status: str = "pending"  # pending, in_progress, completed
    answer: Optional[str] = None


class PlanManager:
    """
    计划执行状态管理器。
    
    精简设计：completed_steps 从 current_step_index 自动推导，
    无需外部传入（因为步骤是严格顺序执行的）。
    """
    
    def __init__(
        self, 
        plan: List[dict], 
        current_step_index: int,
        step_answers: Dict[int, str]
    ):
        self.raw_plan = plan
        self.current_step_index = current_step_index
        self.step_answers = step_answers
        
        # 从 current_step_index 推导 completed_steps（步骤严格顺序执行）
        self.completed_indices = set(range(current_step_index))
        
        # 构建步骤对象
        self.steps: List[PlanStep] = []
        for idx, item in enumerate(plan):
            if idx in self.completed_indices:
                status = "completed"
            elif idx == current_step_index:
                status = "in_progress"
            else:
                status = "pending"
            
            self.steps.append(PlanStep(
                index=idx,
                title=item.get("title", f"步骤 {idx + 1}"),
                description=item.get("description", ""),
                status=status,
                answer=step_answers.get(idx)
            ))

    def get_current_step(self) -> Optional[PlanStep]:
        if 0 <= self.current_step_index < len(self.steps):
            return self.steps[self.current_step_index]
        return None

    def _render_progress(self) -> str:
        """渲染计划进度表"""
        lines = ["📋 **计划进度**:"]
        for step in self.steps:
            if step.status == "completed":
                lines.append(f"✅ {step.index + 1}. {step.title} [已完成]")
            elif step.status == "in_progress":
                lines.append(f"▶️ {step.index + 1}. {step.title} **[当前]**")
            else:
                lines.append(f"⏳ {step.index + 1}. {step.title}")
        return "\n".join(lines)

    def _render_completed_context(self, max_chars_per_step: int = 150) -> str:
        """
        渲染已完成步骤的上下文摘要。
        
        使用 150 字符精简摘要而非 500 字符原文。
        原因：后续步骤只需知道前面"做了什么"，不需要完整回答文本。
        完整回答已在对话历史的 AIMessage 中，LLM 能通过上下文看到。
        精简后 3 个已完成步骤仅占 ~450 字符（原来 ~1500 字符）。
        """
        summaries = []
        for s in self.steps:
            if s.status == "completed" and s.answer:
                preview = s.answer[:max_chars_per_step]
                if len(s.answer) > max_chars_per_step:
                    preview += "..."
                summaries.append(f"✅ 步骤 {s.index + 1}: {s.title}\n{preview}")
        
        if not summaries:
            return ""
        return "\n\n**【已完成步骤摘要】**:\n" + "\n---\n".join(summaries)

    def render_system_instruction(self, user_question: str) -> str:
        """生成当前步骤的系统指令（首轮执行用）"""
        step = self.get_current_step()
        if not step:
            return "Plan completed or invalid."
        
        completed_ctx = self._render_completed_context()
        
        # 🔧 移除"后续步骤预览"：LLM 不需要看未来步骤（进度表已标注"⏳"），
        # 且指令已明确说"不要处理后续步骤"。每步节省 ~100-200 字符。

        return (
            f"🎯 **用户问题**: {user_question}\n\n"
            f"{self._render_progress()}\n\n"
            f"🚀 **当前任务 ({step.index + 1}/{len(self.steps)})**: {step.title}\n"
            f"{step.description}\n"
            f"{completed_ctx}\n\n"
            f"⚠️ **执行准则**:\n"
            f"1. 严格专注当前步骤，不要处理后续步骤。\n"
            f"2. 回答必须详实准确，基于文档事实。\n"
            f"3. 如需更多信息，可调用 search_document（必须使用 Function Calling 机制，不要直接打印代码）。\n\n"
        )
