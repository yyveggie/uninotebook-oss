from typing import Optional, List, Dict
from db_models import Document
from ..schema import QuizResponse, SearchQueriesResponse
from ..retrieval_strategy import RetrievalConfig
import json
from utils.logger import get_logger
logger = get_logger(__name__)

class QuizGeneratorMixin:
    """测验生成功能混入类"""

    async def _generate_coverage_queries(self, document: Document) -> SearchQueriesResponse:
        """基于文档摘要生成覆盖主要知识点的检索查询"""
        planning_prompt = """你是一个专业的文档分析师。请分析以下文档内容，提取出5个核心的、互不重叠的主题或知识点，用于检索文档的详细内容以生成测验题目。

文档：{filename}
摘要/内容：
{base_content}

要求：

请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：
1. `semantic_queries`: 3-5个自然语言问题，涵盖文档中的核心概念、重要结论（例如："变压器的三大核心优势是什么？"）。
2. `keywords`: 5-8个具体的专有名词、术语或关键流程，用于精准匹配（例如："Transformer", "Multi-head Attention"）。
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
        return await self._generate_search_plan(
            document=document,
            feature_name="测验",
            planning_prompt=planning_prompt,
            fallback_semantic_query=f"Main topics in {document.filename}",
            fallback_keywords=[document.filename],
        )

    async def generate_quiz_from_context(
        self,
        context_text: str,
        title: str = "对话内容",
        num_questions: int = 10
    ) -> List[Dict]:
        """基于预构建的上下文文本生成测验（跳过检索，用于对话测验）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"📝 开始生成对话测验 (Context Mode)...")
            logger.info(f"{'='*60}")
            
            schema_json = json.dumps(QuizResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            prompt = f"""基于以下对话内容，生成{num_questions}道高质量的选择题。

对话来源：{title}
对话内容：
{context_text[:14000]}

要求：
1. 每道题包含 question、4个 options、correct_answer(0-3)、explanation
2. **题目必须基于对话中讨论的具体知识点**，避免过于笼统的问题。
3. 优先考察 AI 在对话中解释的核心概念和结论。
4. 选项要有区分度，干扰项合理但明显错误。
5. 解析需要详细说明逻辑。
6. **Latex 格式规范**：行内公式 $...$，独立公式 $$...$$。严禁 Unicode 数学符号。
7. 不要出通用常识题或无关紧要的细节题。

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
            messages = [
                {"role": "system", "content": "你是一个专业的测验题目生成专家。请严格遵循 JSON Schema 输出数据。"},
                {"role": "user", "content": prompt}
            ]
            
            ai_response = await self.lc_client.ainvoke(messages)
            
            from ..utils import parse_and_fix_quiz_json
            quiz = parse_and_fix_quiz_json(ai_response.content)
            
            logger.info(f"✅ 对话测验生成成功！题目数量: {len(quiz)}")
            return quiz
            
        except Exception as e:
            logger.error(f"❌ 对话测验生成失败: {e}")
            raise

    async def generate_quiz(
        self,
        document: Document,
        num_questions: int = 10,
        user_id: Optional[int] = None,
        difficulty: Optional[str] = None,
        requirements: Optional[str] = None,
    ) -> List[Dict]:
        """基于文档生成测验题目（使用多路覆盖检索策略）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"📝 开始生成文档测验 (Multi-Query RAG Mode)...")
            logger.info(f"{'='*60}")
            
            # 1. 生成覆盖性查询词 (Hybrid)
            search_plan = await self._generate_coverage_queries(document)

            # 2. 统一 RAG 检索与上下文构建
            context_text, _, _ = await self._retrieve_rag_context(
                document=document,
                search_plan=search_plan,
                top_k=RetrievalConfig.QUIZ["top_k"],
                max_context_chars=12000,
                max_chunk_chars=1000,
            )
            
            # 获取标准 Schema 用于约束
            import json
            schema_json = json.dumps(QuizResponse.model_json_schema(), indent=2, ensure_ascii=False)

            safe_num_questions = num_questions if num_questions > 0 else 10
            safe_difficulty = (difficulty or "").strip()
            safe_requirements = (requirements or "").strip()

            difficulty_instruction = (
                f"7. 难度要求：请将题目难度控制在“{safe_difficulty}”水平。\n"
                if safe_difficulty else ""
            )
            custom_instruction = (
                f"8. 用户自定义要求：{safe_requirements}\n"
                if safe_requirements else ""
            )

            prompt = f"""基于以下文档内容，生成{safe_num_questions}道高质量的选择题。

文档：{document.filename}
参考内容（来自文档不同部分的片段）：
{context_text}

要求：
1. 每道题包含一个问题(question)、4个选项(options)、正确答案索引(correct_answer, 0-3)、答案解析(explanation)
2. **问题必须基于提供的参考内容细节**，避免提问过于笼统或仅基于标题的问题。
3. 选项要有区分度，干扰项要看起来合理但明显错误。
4. 解析需要详细说明逻辑，引用参考内容中的事实。
5. **Latex 格式规范**：
   - 行内公式使用 $...$ 包裹。
   - 独立公式使用 $$...$$ 包裹。
   - **严禁**使用 Unicode 数学符号（如 ∈, ∑, ∫, ×, → 等），必须使用对应的 Latex 命令（如 \\in, \\sum, \\int, \\times, \\rightarrow）。
   - 例如：使用 $x \\in \\mathbb{{R}}$ 而不是 x ∈ ℝ。

6.限制：不要把以下信息列举出来：
   - 通用的常识，没人不知道
   - 无关紧要的知识，根本不是文章的重点
{difficulty_instruction}{custom_instruction}

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串，不要包含 markdown 代码块：

{schema_json}
"""
            
            messages = [
                {"role": "system", "content": "你是一个专业的测验题目生成专家。请严格遵循 JSON Schema 输出数据。"},
                {"role": "user", "content": prompt}
            ]
            
            from ..utils import parse_and_fix_quiz_json

            def _parse_quiz(content: str):
                quiz_list = parse_and_fix_quiz_json(content)
                logger.info(f"✅ 成功处理并转换 {len(quiz_list)} 道题目")
                return quiz_list

            quiz = await self._invoke_structured_generation(
                feature_name="测验",
                messages=messages,
                parser=_parse_quiz,
            )
            
            logger.info(f"✅ 测验生成成功！")
            
            logger.info(f"✅ 测验生成成功！")
            logger.info(f"   题目数量: {len(quiz)}")
            logger.info(f"{'='*60}\n")
            
            # 注意：不再保存到旧的 DocumentQuiz 表，改为在 main.py 中统一保存到 Note 表
            
            return quiz
            
        except Exception as e:
            context = self._build_context(document, {
                "num_questions": num_questions,
                "difficulty": difficulty,
                "requirements": requirements,
                "user_id": user_id
            })
            self._log_error("文档测验", f"生成失败: {e}", error=e, context=context)
            error_msg = str(e)
            if "Connection error" in error_msg or "ConnectError" in error_msg:
                raise Exception("网络连接失败，请检查网络连接或稍后重试") from e
            else:
                raise
