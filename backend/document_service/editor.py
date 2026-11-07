
import json
import time
from typing import Optional, List, Dict, Any, AsyncGenerator
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage
from langchain_core.language_models import BaseChatModel
import llm_service as llm_config
import db_models
from utils.logger import get_logger
logger = get_logger(__name__)


QUICK_ACTIONS = {
    "translate_en": {
        "label": "翻译为英文",
        "icon": "🌐",
        "instruction": "Translate the selected text into fluent, natural English. Preserve the original formatting.",
    },
    "translate_zh": {
        "label": "翻译为中文",
        "icon": "🇨🇳",
        "instruction": "将选中的文本翻译为流畅、自然的中文。保留原有格式。",
    },
    "expand": {
        "label": "扩写",
        "icon": "📝",
        "instruction": "扩展选中的文本，增加更多细节、例子和解释，使内容更加丰富。保持原有语气和风格。",
    },
    "condense": {
        "label": "缩写",
        "icon": "✂️",
        "instruction": "精简选中的文本，去除冗余内容，保留核心信息。使表达更加简洁有力。",
    },
    "fix_grammar": {
        "label": "修复语法",
        "icon": "🔧",
        "instruction": "修复选中文本中的语法错误、拼写错误和标点问题。不要改变原意。",
    },
    "academic": {
        "label": "学术化",
        "icon": "🎓",
        "instruction": "将选中的文本改写为学术论文风格。使用正式措辞、被动语态、专业术语。",
    },
    "casual": {
        "label": "口语化",
        "icon": "💬",
        "instruction": "将选中的文本改写为轻松日常的口语风格。使表达更加亲切自然。",
    },
    "continue": {
        "label": "续写",
        "icon": "➡️",
        "instruction": "根据上下文继续写作。保持与前文一致的风格、主题和逻辑。生成 200-400 字的内容。",
    },
}

# 续写生成的是接在选区（或光标）之后的新内容，前端插入而不是替换选区
CONTINUE_ACTION = "continue"

EDIT_SYSTEM_PROMPT = (
    "你是一个专业的 AI 文本编辑助手。\n"
    "你的任务是根据用户的指令，改写「选中文本」部分。\n\n"
    "规则：\n"
    "1. 只输出改写后的替换文本，不要包含任何解释、开场白或总结。\n"
    "2. 不要输出 Markdown 代码块标记（```），除非内容本身就是代码。\n"
    "3. 除非指令要求改变格式，否则保持原有的格式和排版。\n"
    "4. 注意上下文，确保修改后的文本与前后内容衔接自然。\n"
    "5. 如果选中内容为空，则视为在光标位置生成新内容。\n"
)

CONTINUE_SYSTEM_PROMPT = (
    "你是一个专业的写作助手。\n"
    "你的任务是紧接「续写起点」往下写新的内容。\n\n"
    "规则：\n"
    "1. 只输出新写的内容，不要重复续写起点或上文中已有的文字，不要包含任何解释、开场白或总结。\n"
    "2. 不要输出 Markdown 代码块标记（```），除非内容本身就是代码。\n"
    "3. 保持与前文一致的语言、风格、主题和逻辑，并与下文自然衔接。\n"
)


class AIEditorService:
    """AI 辅助编辑服务"""

    def __init__(self):
        pass

    def _create_llm(self) -> BaseChatModel:
        # 模型固定按功能映射选择，不接受客户端指定（否则同样的积分可以调用更贵的模型）
        return llm_config.get_qa_llm(llm_config.FEATURE_MODEL_MAP["editor"])

    @staticmethod
    def get_quick_actions() -> List[Dict[str, str]]:
        return [
            {"key": k, "label": v["label"], "icon": v["icon"]}
            for k, v in QUICK_ACTIONS.items()
        ]

    def resolve_instruction(self, instruction: str, action_key: Optional[str] = None) -> str:
        if action_key and action_key in QUICK_ACTIONS:
            return QUICK_ACTIONS[action_key]["instruction"]
        return instruction

    async def stream_edit(
        self,
        selection: str,
        instruction: str,
        context_before: str = "",
        context_after: str = "",
        action_key: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """
        流式 AI 编辑。

        快捷操作通过 action_key 映射到预设 instruction，
        自定义指令直接使用 instruction 参数。
        模型调用失败时直接抛出异常，由调用方退款并返回错误——不能把报错文字当成编辑结果输出。
        """
        resolved_instruction = self.resolve_instruction(instruction, action_key)
        llm = self._create_llm()

        if action_key == CONTINUE_ACTION:
            system_prompt = CONTINUE_SYSTEM_PROMPT
            user_content = (
                f"上文（供参考）：\n...{context_before[-800:]}\n\n"
                f"⭐ 【续写起点】（新内容紧接在这段之后）：\n{selection or '（光标位置，紧接上文结尾）'}\n\n"
                f"下文（供参考）：\n{context_after[:800]}...\n\n"
                f"🛠️ 指令：{resolved_instruction}\n\n"
                f"请直接输出续写的新内容："
            )
        else:
            system_prompt = EDIT_SYSTEM_PROMPT
            user_content = (
                f"上文（供参考）：\n...{context_before[-800:]}\n\n"
                f"⭐ 【选中文本】（请改写这段）：\n{selection}\n\n"
                f"下文（供参考）：\n{context_after[:800]}...\n\n"
                f"🛠️ 指令：{resolved_instruction}\n\n"
                f"请输出改写后的文本："
            )

        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ]

        async for chunk in llm.astream(messages):
            if chunk.content:
                yield chunk.content

    async def get_document_content(self, document_id: int, user_id: int) -> Dict[str, Any]:
        from database import AsyncSessionLocal
        from sqlalchemy import select

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(db_models.Document).filter(
                    db_models.Document.id == document_id,
                    db_models.Document.user_id == user_id,
                )
            )
            doc = result.scalar_one_or_none()

            if not doc:
                return {"success": False, "error": "文档不存在"}

            return {
                "success": True,
                "document": {
                    "id": doc.id,
                    "filename": doc.filename,
                    "display_name": doc.display_name,
                    "type": doc.file_type,
                },
                "content": doc.original_text or "",
            }

