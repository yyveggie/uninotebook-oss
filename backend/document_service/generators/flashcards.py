from typing import Optional, List, Dict
from db_models import Document
from ..schema import FlashcardsResponse, SearchQueriesResponse
from ..retrieval_strategy import RetrievalConfig
import json
from utils.logger import get_logger
logger = get_logger(__name__)

class FlashcardsGeneratorMixin:
    """闪卡生成功能混入类"""

    async def _generate_flashcard_queries(self, document: Document) -> SearchQueriesResponse:
        """基于文档摘要生成覆盖主要知识点的检索查询"""
        planning_prompt = """你是一个专业的知识萃取专家。请分析以下文档内容，提取出5个核心的、互不重叠的知识点，用于检索文档的详细信息以制作学习闪卡。

文档：{filename}
摘要/内容：
{base_content}

要求：

请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：
1. `semantic_queries`: 3-5个自然语言问题，侧重于理解文档的核心概念和原理（例如："什么是Transformer架构？"）。
2. `keywords`: 5-8个具体的专有名词、术语或关键实体，用于精准匹配（例如："Self-Attention"）。
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
        return await self._generate_search_plan(
            document=document,
            feature_name="闪卡",
            planning_prompt=planning_prompt,
            fallback_semantic_query=f"Key concepts in {document.filename}",
            fallback_keywords=[document.filename],
        )

    async def generate_flashcards_from_context(
        self,
        context_text: str,
        title: str = "对话内容",
        num_cards: int = 10
    ) -> List[Dict[str, str]]:
        """基于预构建的上下文文本生成闪卡（跳过检索，用于对话闪卡）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🃏 开始生成对话闪卡 (Context Mode)...")
            logger.info(f"{'='*60}")
            
            schema_json = json.dumps(FlashcardsResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            prompt = f"""基于以下对话内容，生成{num_cards}张高质量的学习闪卡。

对话来源：{title}
对话内容：
{context_text[:12000]}

要求：
1. 每张闪卡包含一个问题(question)和一个答案(answer)。
2. **问题必须基于对话中讨论的具体知识点**，涵盖核心概念、原理或结论。
3. 答案应准确完整（50-150字），包含必要的解释和背景。
4. 优先提取对话中 AI 解释的核心知识点，而非用户提问本身。
5. **Latex 格式规范**：行内公式 $...$，独立公式 $$...$$。严禁 Unicode 数学符号。

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
            messages = [{"role": "user", "content": prompt}]
            ai_response = await self.lc_client.ainvoke(messages)
            
            from ..utils import parse_json_safely
            from utils.latex import fix_latex_format
            
            data = parse_json_safely(ai_response.content)
            flashcards_response = FlashcardsResponse.model_validate(data)
            
            if not flashcards_response.flashcards:
                raise ValueError("AI返回的闪卡列表为空")
            
            flashcards = []
            for fc in flashcards_response.flashcards:
                flashcards.append({
                    "question": fix_latex_format(fc.question),
                    "answer": fix_latex_format(fc.answer)
                })
            
            logger.info(f"✅ 对话闪卡生成成功！数量: {len(flashcards)}")
            return flashcards
            
        except Exception as e:
            logger.error(f"❌ 对话闪卡生成失败: {e}")
            raise

    async def generate_flashcards(
        self,
        document: Document,
        num_cards: int = 10,
        user_id: Optional[int] = None,
        difficulty: Optional[str] = None,
        requirements: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """基于文档生成学习闪卡（使用多路覆盖检索策略）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🃏 开始生成文档闪卡 (Multi-Query RAG Mode)...")
            logger.info(f"{'='*60}")
            
            # 1. 生成覆盖性查询词 (Hybrid)
            search_plan = await self._generate_flashcard_queries(document)

            # 2. 统一 RAG 检索与上下文构建
            context_text, _, _ = await self._retrieve_rag_context(
                document=document,
                search_plan=search_plan,
                top_k=RetrievalConfig.FLASHCARDS["top_k"],
                max_context_chars=10000,
                max_chunk_chars=1000,
            )

            # 4. 构建 Prompt
            schema_json = json.dumps(FlashcardsResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            safe_num_cards = num_cards if num_cards > 0 else 10
            safe_difficulty = (difficulty or "").strip()
            safe_requirements = (requirements or "").strip()

            difficulty_instruction = (
                f"6. 难度要求：请将题目难度控制在“{safe_difficulty}”水平。\n"
                if safe_difficulty else ""
            )
            custom_instruction = (
                f"7. 用户自定义要求：{safe_requirements}\n"
                if safe_requirements else ""
            )

            prompt = f"""基于以下文档内容，生成{safe_num_cards}张高质量的学习闪卡。

文档：{document.filename}
参考内容（来自文档不同部分的片段）：
{context_text}

要求：
1. 每张闪卡包含一个问题(question)和一个答案(answer)。
2. **问题必须基于提供的参考内容细节**，涵盖核心概念、原理或事实。
3. 答案应准确完整（50-150字），包含必要的解释和背景，便于用户理解和记忆。
4. 避免生成过于简单或仅依赖标题的问题。
5. **Latex 格式规范**：
   - 行内公式使用 $...$ 包裹。
   - 独立公式使用 $$...$$ 包裹。
   - **严禁**使用 Unicode 数学符号（如 ∈, ∑, ∫, ×, → 等），必须使用对应的 Latex 命令（如 \\in, \\sum, \\int, \\times, \\rightarrow）。
   - 例如：使用 $x \\in \\mathbb{{R}}$ 而不是 x ∈ ℝ。
{difficulty_instruction}{custom_instruction}

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串，不要包含 markdown 代码块：

{schema_json}
"""
            messages = [{"role": "user", "content": prompt}]

            # 5. 解析和处理结果
            from ..utils import parse_json_safely
            from utils.latex import fix_latex_format

            def _parse_flashcards(content: str):
                data = parse_json_safely(content)
                flashcards_response = FlashcardsResponse.model_validate(data)
                if not flashcards_response.flashcards:
                    raise ValueError("AI返回的闪卡列表为空")
                parsed = []
                for fc in flashcards_response.flashcards:
                    parsed.append({
                        "question": fix_latex_format(fc.question),
                        "answer": fix_latex_format(fc.answer)
                    })
                return parsed

            flashcards = await self._invoke_structured_generation(
                feature_name="闪卡",
                messages=messages,
                parser=_parse_flashcards,
            )
            
            logger.info(f"✅ 闪卡生成成功！")
            logger.info(f"   生成数量: {len(flashcards)}")
            logger.info(f"{'='*60}\n")
            
            return flashcards
            
        except Exception as e:
            context = self._build_context(document, {
                "num_cards": num_cards,
                "difficulty": difficulty,
                "requirements": requirements,
                "user_id": user_id
            })
            self._log_error("学习闪卡", f"生成失败: {e}", error=e, context=context)
            error_msg = str(e)
            if "Connection error" in error_msg or "ConnectError" in error_msg:
                raise Exception("网络连接失败，请检查网络连接或稍后重试") from e
            else:
                raise
