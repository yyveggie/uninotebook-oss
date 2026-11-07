from typing import Optional, List, Dict
from db_models import Document
from document_service.schema import DocumentHighlightsResponse
from ..schema import SearchQueriesResponse
import json
from utils.logger import get_logger
logger = get_logger(__name__)

class HighlightsGeneratorMixin:
    """重点提炼生成功能混入类"""

    async def _generate_highlight_queries(self, document: Document) -> SearchQueriesResponse:
        """基于文档摘要生成覆盖文档核心价值的检索查询"""
        planning_prompt = """你是一个专业的文档分析师。请分析以下文档内容，设计一组检索查询词，用于获取文档的“核心要点”和“精华内容”。

文档：{filename}
摘要/内容：
{base_content}

要求：

请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：
1. `semantic_queries`: 3-5个自然语言问题，侧重于挖掘文档的核心价值和精华内容（例如："本文提出的核心算法是如何解决XXX问题的？"）。
2. `keywords`: 5-8个具体的专有名词、关键数据指标或核心实体，用于精准匹配（例如："ResNet-50", "Accuracy 98.5%"）。

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
        return await self._generate_search_plan(
            document=document,
            feature_name="重点提炼",
            planning_prompt=planning_prompt,
            fallback_semantic_query=f"Key takeaways from {document.filename}",
            fallback_keywords=[document.filename],
        )

    async def generate_highlights_from_context(
        self,
        context_text: str,
        title: str = "对话内容"
    ) -> Dict:
        """基于预构建的上下文文本生成重点提炼（跳过检索，用于对话提炼）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"💡 开始生成对话提炼 (Context Mode)...")
            logger.info(f"{'='*60}")
            
            schema_json = json.dumps(DocumentHighlightsResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            prompt = f"""# 任务：对话核心要点提炼

## 对话信息
- 来源：{title}

## 对话内容
{context_text[:15000]}

## 提炼规则

### 类别定义
| 类别 | 定义 |
|------|------|
| 关键要点 | 对话中的核心知识点、主要结论、关键发现 |
| 核心创新 | 独特的见解、创新方法、突破性观点 |
| 注意事项 | 限制条件、潜在风险、使用前提 |
| 细节设置 | 具体参数、实现步骤、技术细节 |

### 质量标准
✅ 优先提取 AI 回答中的核心知识点和深入分析
✅ 提取具体的数据、结论、方法
❌ 忽略问候、追问、闲聊等非知识内容
❌ 不要提取通用常识

### 输出要求
1. 提炼 6-12 个要点，覆盖多个类别
2. 每个要点：title（≤25字）、content（80-150字）、importance（高/中/低）
3. 按重要程度排序
4. 数学公式用 LaTeX：行内 $...$ ，块级 $$...$$

### 摘要
用一句话（50-80字）概括对话讨论的核心内容。

输出格式：
请严格遵循以下 JSON Schema 输出：

{schema_json}
"""
            messages = [
                {"role": "system", "content": (
                    "你是一位资深的知识提炼专家。你的任务是从对话记录中提炼出真正有价值的核心知识要点。\n"
                    "原则：宁缺毋滥、具体优先、知识导向。"
                )},
                {"role": "user", "content": prompt}
            ]
            
            ai_response = await self.lc_client.ainvoke(messages)
            
            from ..utils import parse_json_safely
            data = parse_json_safely(ai_response.content)
            highlights_response = DocumentHighlightsResponse.model_validate(data)
            highlights_data = highlights_response.model_dump()
            
            logger.info(f"✅ 对话提炼生成成功！要点数: {len(highlights_data['key_points'])}")
            return highlights_data
            
        except Exception as e:
            logger.error(f"❌ 对话提炼生成失败: {e}")
            raise

    async def generate_highlights(
        self,
        document: Document,
        user_id: Optional[int] = None,
        num_points: Optional[int] = None,
        difficulty: Optional[str] = None,
        requirements: Optional[str] = None,
    ) -> Dict:
        """从文档生成重点提炼（使用多路覆盖检索策略）"""
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"💡 开始生成重点提炼 (Multi-Query RAG Mode)...")
            logger.info(f"{'='*60}\n")
            
            # 1. 生成覆盖性查询词 (Hybrid)
            search_plan = await self._generate_highlight_queries(document)

            # 2. 统一 RAG 检索与上下文构建
            context_text, part_info, _ = await self._retrieve_rag_context(
                document=document,
                search_plan=search_plan,
                top_k=8,
                max_context_chars=15000,
                max_chunk_chars=1200,
                short_doc_fulltext_threshold=5000,
                short_doc_label="文档完整内容",
            )
            
            # 获取 JSON Schema
            schema_json = json.dumps(DocumentHighlightsResponse.model_json_schema(), indent=2, ensure_ascii=False)

            safe_num_points = num_points if num_points and num_points > 0 else None
            safe_difficulty = (difficulty or "").strip()
            safe_requirements = (requirements or "").strip()

            point_instruction = (
                f"1. 提炼 {safe_num_points} 个要点，覆盖多个类别\n"
                if safe_num_points else "1. 提炼 8-15 个要点，覆盖多个类别\n"
            )
            difficulty_instruction = (
                f"5. 提炼深度要求：请按“{safe_difficulty}”难度输出（easy=基础结论，medium=标准分析，hard=深入机制与边界）\n"
                if safe_difficulty else ""
            )
            custom_instruction = (
                f"6. 用户自定义要求：{safe_requirements}\n"
                if safe_requirements else ""
            )

            prompt = f"""# 任务：文档核心要点提炼

## 文档信息
- 标题：{document.filename}
- 范围：{part_info}

## 参考内容
{context_text}

## 提炼规则

### 类别定义（严格按此分类）
| 类别 | 定义 | 示例 |
|------|------|------|
| 关键要点 | 文档的核心论点、主要结论、关键发现 | "研究发现X方法比Y方法效率提升30%" |
| 核心创新 | 首次提出的概念、独创方法、突破性观点 | "提出了一种新的XX算法" |
| 注意事项 | 限制条件、潜在风险、使用前提、作者警告 | "该方法仅适用于XX场景" |
| 细节设置 | 具体参数、实现步骤、技术细节、配置要求 | "学习率设为0.001，batch_size=32" |

### 质量标准
✅ 必须提取：
- 文档的独特贡献和核心观点
- 具体的数据、结论、方法
- 作者明确强调的重要信息

❌ 禁止提取：
- 通用常识（如"深度学习需要大量数据"）
- 背景介绍、文献综述中的他人观点
- 模糊笼统的描述（如"该方法很有效"）

### 输出要求
{point_instruction}2. 每个要点：
   - title：一句话概括（≤25字），直接点明核心
   - content：详细说明（80-150字），包含具体信息
   - importance：高（核心贡献）/ 中（重要补充）/ 低（细节参考）
3. 按重要程度排序（高→中→低）
4. 数学公式用 LaTeX：行内 $...$ ，块级 $$...$$
{difficulty_instruction}{custom_instruction}

### 摘要要求
用一句话（50-80字）概括文档核心贡献，格式："本文/该研究/该文档 + 动词 + 核心内容"

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串，不要包含 markdown 代码块：

{schema_json}
"""
            messages = [
                {"role": "system", "content": (
                    "你是一位资深的学术文档分析专家。你的任务是从文档中提炼出真正有价值的核心要点。\n\n"
                    "你的原则：\n"
                    "1. 宁缺毋滥：只提取真正重要的信息，不凑数\n"
                    "2. 具体优先：用具体数据、方法、结论替代模糊描述\n"
                    "3. 原创为主：优先提取文档的独特贡献，而非引用的他人观点\n"
                    "4. 实用导向：提取的信息应该对读者理解和应用文档有直接帮助"
                )},
                {"role": "user", "content": prompt}
            ]
            
            from ..utils import parse_json_safely

            def _parse_highlights(content: str):
                data = parse_json_safely(content)
                highlights_response = DocumentHighlightsResponse.model_validate(data)
                highlights_data = highlights_response.model_dump()
                logger.info(f"✅ 成功转换为字典格式")
                return highlights_data

            highlights_data = await self._invoke_structured_generation(
                feature_name="重点提炼",
                messages=messages,
                parser=_parse_highlights,
            )
            
            logger.info(f"✅ 重点提炼生成成功！")
            logger.info(f"   要点总数: {len(highlights_data['key_points'])}")
            logger.info(f"{'='*60}\n")

            return highlights_data
        
        except Exception as e:
            context = self._build_context(document, {
                "user_id": user_id,
                "num_points": num_points,
                "difficulty": difficulty,
                "requirements": requirements,
            })
            self._log_error("重点提炼", f"生成失败: {e}", error=e, context=context)
            error_msg = str(e)
            if "Connection error" in error_msg or "ConnectError" in error_msg:
                raise Exception("网络连接失败，请检查网络连接或稍后重试") from e
            else:
                raise
