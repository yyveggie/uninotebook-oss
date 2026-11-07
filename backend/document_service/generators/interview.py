"""
面试功能核心逻辑
实现智能面试系统，包含三层缓存策略
"""
import uuid
from typing import Dict, List, Optional, Set, Tuple
from datetime import datetime, timezone
from langchain_core.caches import InMemoryCache
from langchain_openai import ChatOpenAI
from sqlalchemy import select, update, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..schema import (
    KnowledgePoint,
    KnowledgePointsResponse,
    InterviewQuestion,
    InterviewEvaluation,
    InterviewSummary,
    SearchQueriesResponse
)
from utils.logger import get_logger
from database import get_db
from db_models import Document
import json
import traceback

logger = get_logger(__name__)

class InterviewSession:
    """面试会话（内存缓存）"""
    # ... (保持不变)
    
    def __init__(self, session_id: str, document_id: int, cached_content: Dict):
        self.session_id = session_id
        self.document_id = document_id
        self.created_at = datetime.now()
        
        # 第一层缓存：核心内容
        self.cached_content = cached_content
        self.knowledge_points: List[KnowledgePoint] = []
        
        # 第三层缓存：进度追踪
        self.current_round = 0
        self.max_rounds = 10
        self.qa_history: List[Tuple[int, str, str, InterviewEvaluation]] = []  # (round, question, answer, evaluation)
        self.asked_point_ids: Set[str] = set()
        self.scores: List[float] = []
        self.summary = None  # 面试总结（仅在完成时生成）
        self.current_question = None  # 当前问题（用于评分）
        self.original_record_id = None  # 如果是恢复的面试，记录原始记录ID
        
        self.status = "ongoing"  # ongoing | completed | aborted


class InterviewManager:
    """面试管理器"""
    
    def __init__(self, api_key: str = None, base_url: str = None, model: str = None):
        """初始化面试管理器 (现使用统一配置，参数为兼容保留)"""
        # 第二层缓存：LLM缓存
        self.llm_cache = InMemoryCache(maxsize=100)
        
        # 🆕 使用统一配置创建 LLM 客户端
        from llm_service import get_llm_for_feature
        
        # 第一层缓存：活动会话
        self.active_sessions: Dict[str, InterviewSession] = {}

    @property
    def lc_client(self):
        from llm_service import get_llm_for_feature

        return get_llm_for_feature("interview")

    def _get_context_limit(self, round_num: int, phase: str, max_rounds: int) -> int:
        """按轮次和阶段动态控制上下文长度，平衡质量与成本。"""
        safe_max_rounds = max(max_rounds, 1)
        progress = round_num / safe_max_rounds

        if phase == "question":
            if progress <= 0.3:
                return 5200
            if progress <= 0.7:
                return 3800
            return 2600

        if phase == "evaluation":
            if progress <= 0.3:
                return 4600
            if progress <= 0.7:
                return 3200
            return 2200

        return 5000

    def _trim_context_for_round(self, context: str, round_num: int, phase: str, max_rounds: int) -> str:
        """裁剪上下文：优先保留前部结构信息 + 末尾细节，避免只截头导致信息偏置。"""
        limit = self._get_context_limit(round_num, phase, max_rounds)
        if len(context) <= limit:
            return context

        head_len = int(limit * 0.72)
        marker = "\n\n...[上下文已按轮次裁剪]...\n\n"
        tail_len = max(limit - head_len - len(marker), 0)

        if tail_len <= 0:
            return context[:limit]

        return f"{context[:head_len]}{marker}{context[-tail_len:]}"
    
    async def _generate_interview_queries(self, document: Document) -> SearchQueriesResponse:
        """基于文档摘要生成覆盖核心考点的检索查询"""
        try:
            base_content = document.summary if document.summary else document.original_text[:2000]
            
            schema_json = json.dumps(SearchQueriesResponse.model_json_schema(), indent=2, ensure_ascii=False)
            
            prompt = f"""你是一个专业的学术面试官。请分析以下文档内容，设计一组检索查询词，用于获取设计“高难度面试问题”所需的详细信息。

文档：{document.filename}
摘要/内容：
{base_content}

要求：

请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：
1. `semantic_queries`: 3-5个自然语言问题，侧重于挖掘文档的深度内容、争议点或复杂逻辑（例如："文章关于模型泛化能力的讨论有哪些局限性？"）。
2. `keywords`: 5-8个具体的专有名词、术语或关键实体，用于精准匹配（例如："Generalization Error"）。

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""
            messages = [{"role": "user", "content": prompt}]
            response = await self.lc_client.ainvoke(messages)
            
            from ..utils import parse_json_safely
            data = parse_json_safely(response.content)
            
            validated_data = SearchQueriesResponse.model_validate(data)
            
            logger.info(f"🔍 面试检索方案:")
            logger.info(f"   - 语义查询: {validated_data.semantic_queries}")
            logger.info(f"   - 关键词: {validated_data.keywords}")
            
            return validated_data
            
        except Exception as e:
            logger.warning(f"⚠️ 生成检索查询失败，使用默认策略: {e}")
            return SearchQueriesResponse(
                queries=[],
                semantic_queries=[f"Core innovation in {document.filename}"],
                keywords=[document.filename]
            )

    async def initialize_interview(
        self,
        document: Document,
        vector_store_manager,
        db: Optional[AsyncSession] = None
    ) -> Tuple[str, Dict]:
        """
        初始化面试（仅一次向量检索）
        
        Returns:
            (session_id, first_question_data)
        """
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"🎤 开始初始化面试 (Multi-Query RAG Mode)...")
            logger.info(f"   文档: {document.filename}")
            logger.info(f"{'='*60}")
            
            # 1️⃣ 一次性向量检索：提取核心内容
            logger.info(f"📚 步骤1: 向量检索核心内容...")
            key_content = await self._retrieve_innovation_content(
                document,
                vector_store_manager
            )
            
            # 2️⃣ AI分析：提取5-10个知识点（缓存）
            logger.info(f"🧠 步骤2: 提取知识点...")
            knowledge_points = await self._extract_knowledge_points(key_content)
            logger.info(f"   ✅ 提取到 {len(knowledge_points)} 个知识点")
            for i, kp in enumerate(knowledge_points, 1):
                logger.info(f"      {i}. [{kp.category}] {kp.content[:50]}...")
            
            # 3️⃣ 创建会话缓存
            session_id = f"interview-{uuid.uuid4()}"
            session = InterviewSession(session_id, document.id, key_content)
            session.knowledge_points = knowledge_points
            self.active_sessions[session_id] = session
            
            logger.info(f"   ✅ 会话创建成功: {session_id}")
            
            # 4️⃣ 生成第一个问题
            logger.info(f"❓ 步骤3: 生成第一个问题...")
            first_question = await self._generate_question(session, round_num=1)

            # 5️⃣ 立即保存到数据库 (支持跨进程/重启恢复)
            if db:
                await self._save_interview_record(session, db)
            
            logger.info(f"✅ 面试初始化完成！")
            logger.info(f"{'='*60}\n")
            
            return session_id, {
                'round': 1,
                'question': first_question.question,
                'expected_keywords': first_question.expected_keywords,
                'hints': first_question.hints,
                'total_rounds': session.max_rounds,
                'knowledge_points_count': len(knowledge_points)
            }
            
        except Exception as e:
            logger.error(f"❌ 面试初始化失败: {str(e)}")
            raise
    
    async def _retrieve_innovation_content(
        self,
        document: Document,
        vector_store_manager
    ) -> Dict:
        """向量检索：针对性提取创新内容（使用多路覆盖检索）"""
        
        # 1. 生成查询词
        # 1. 生成查询词 (Hybrid)
        search_plan = await self._generate_interview_queries(document)
        
        # 2. 高级多路检索 (Semantic + Keyword)
        relevant_chunks = await vector_store_manager.advanced_hybrid_search(
            collection_id=document.vector_store_id,
            semantic_queries=search_plan.semantic_queries,
            keywords=search_plan.keywords,
            top_k=8 # 面试需要较多上下文
        )
        
        # 3. 构建上下文（较长，用于支撑整个面试过程）
        MAX_CONTEXT_CHARS = 18000 
        context_text = ""
        current_chars = 0
        
        for i, chunk in enumerate(relevant_chunks):
            chunk_text = chunk['text']
            if len(chunk_text) > 1200:
                chunk_text = chunk_text[:1200] + "..."
            
            entry = f"[片段 {i+1}]\n{chunk_text}\n\n"
            if current_chars + len(entry) > MAX_CONTEXT_CHARS:
                break
            context_text += entry
            current_chars += len(entry)
            
        logger.info(f"   ✅ 检索到 {len(relevant_chunks)} 个片段，共 {current_chars} 字符")
        
        return {
            'content': context_text,
            'chunk_count': len(relevant_chunks),
            'timestamp': datetime.now().isoformat(),
            'queries': search_plan.semantic_queries + search_plan.keywords
        }
    
    async def _extract_knowledge_points(
        self,
        cached_content: Dict
    ) -> List[KnowledgePoint]:
        """从缓存内容中提取知识点"""
        
        schema_json = json.dumps(KnowledgePointsResponse.model_json_schema(), indent=2, ensure_ascii=False)

        prompt = f"""基于以下文档核心内容，提取5-10个关键知识点用于面试考察。

文档内容：
{cached_content['content'][:10000]}

要求：
1. 每个知识点应包含：
   - 类别（basic/innovation/method/comprehensive）
   - 简短的内容描述（20-50字）
   - 难度等级（1-5，1最简单，5最难）
   - 3-5个关键词
   
2. 知识点分布：
   - 2-3个基础概念（basic, 难度1-2）
   - 3-4个核心创新/方法（innovation/method, 难度3-4）
   - 1-2个综合应用（comprehensive, 难度4-5）
   
3. 确保知识点覆盖文档的核心内容，而不是无关紧要的内容

输出格式：
请严格遵循以下 JSON Schema 输出，直接返回 JSON 字符串：

{schema_json}
"""

        messages = [
            {"role": "system", "content": "你是一个专业的学术面试官，擅长提取关键知识点。"},
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = await self.lc_client.ainvoke(messages)
            
            from ..utils import parse_json_safely
            data = parse_json_safely(response.content)
            
            kp_response = KnowledgePointsResponse.model_validate(data)
            
        except Exception as llm_error:
            logger.error("❌ 调用LLM提取知识点失败:", repr(llm_error))
            raise
        
        # 为每个知识点生成ID
        try:
            knowledge_points = []
            for i, kp in enumerate(kp_response.knowledge_points, 1):
                kp.id = f"kp_{i}"
                knowledge_points.append(kp)
        except Exception as parse_error:
            logger.error("❌ 解析知识点失败:", repr(parse_error))
            raise
        
        return knowledge_points
    
    async def _generate_question(
        self,
        session: InterviewSession,
        round_num: int
    ) -> InterviewQuestion:
        """智能生成问题（基于缓存内容）"""
        
        # 选择知识点策略
        if round_num <= 3:
            # 前期：基础概念
            kp = self._select_knowledge_point(session, ["basic"])
        elif round_num <= 7:
            # 中期：核心创新/方法
            kp = self._select_knowledge_point(session, ["innovation", "method"])
        else:
            # 后期：综合应用
            kp = self._select_knowledge_point(session, ["comprehensive", "innovation"])
        
        if kp is None:
            # 如果没有符合条件的知识点，随机选择未问过的
            available = [k for k in session.knowledge_points if k.id not in session.asked_point_ids]
            if not available:
                # 所有知识点都问过了，重复一个
                kp = session.knowledge_points[0]
            else:
                kp = available[0]
        
        # 记录已提问
        session.asked_point_ids.add(kp.id)
        
        # 从缓存内容生成问题（不需要向量检索）
        adaptive_context = self._trim_context_for_round(
            session.cached_content['content'],
            round_num=round_num,
            phase="question",
            max_rounds=session.max_rounds,
        )

        prompt = f"""基于以下文档内容，针对指定知识点生成一个面试问题。

文档内容（已缓存）：
    {adaptive_context}

目标知识点：
- 类别：{kp.category}
- 内容：{kp.content}
- 难度：{kp.difficulty}/5
- 关键词：{', '.join(kp.keywords)}

当前是第 {round_num}/{session.max_rounds} 轮

要求：
1. 问题应该测试考生对这个知识点的理解
2. 难度适中，需要考生结合文档内容回答
3. 开放式问题，不是简单的是非题
4. 问题长度50-150字
5. 如果难度较高（4-5级），可以提供一些提示
6. **Latex 格式规范**：
   - 行内公式使用 $...$ 包裹。
   - 独立公式使用 $$...$$ 包裹。
   - **严禁**使用 Unicode 数学符号（如 ∈, ∑, ∫, ×, → 等），必须使用对应的 Latex 命令（如 \\in, \\sum, \\int, \\times, \\rightarrow）。"""

        structured_llm = self.lc_client.with_structured_output(
            InterviewQuestion,
            method="function_calling"
        )
        
        messages = [
            {"role": "system", "content": "你是一个专业的学术面试官，擅长提出有深度的问题。"},
            {"role": "user", "content": prompt}
        ]
        
        question_response = await structured_llm.ainvoke(messages)
        
        if question_response is None:
            raise Exception("AI未能生成有效的问题，请重试")
        
        # 记录当前问题（用于后续评分）
        session.current_question = (kp, question_response)
        
        return question_response
    
    def _select_knowledge_point(
        self,
        session: InterviewSession,
        preferred_categories: List[str]
    ) -> Optional[KnowledgePoint]:
        """选择知识点（优先选择指定类别且未问过的）"""
        
        available = [
            kp for kp in session.knowledge_points
            if kp.category in preferred_categories and kp.id not in session.asked_point_ids
        ]
        
        if not available:
            return None
        
        # 按难度排序，逐步提高
        available.sort(key=lambda x: x.difficulty)
        return available[0]
    
    async def submit_answer(
        self,
        session_id: str,
        answer: str,
        db: Optional[AsyncSession] = None,
        expected_user_id: Optional[int] = None,
        expected_document_id: Optional[int] = None,
    ) -> Dict:
        """提交答案并获取评分+下一个问题"""
        
        session = self.active_sessions.get(session_id)

        if session and expected_document_id is not None and session.document_id != expected_document_id:
            raise ValueError("面试会话与当前文档不匹配")
        
        # 尝试从数据库恢复 (如果内存中没有)
        if not session and db:
            logger.warning(f"⚠️ 内存中未找到会话 {session_id}，尝试从数据库恢复...")
            session = await self.recover_session(
                session_id,
                db,
                expected_user_id=expected_user_id,
                expected_document_id=expected_document_id,
            )

            # 会话ID可能与前端本地状态短暂不一致：尝试回退到该文档最新进行中会话
            if not session and expected_user_id is not None and expected_document_id is not None:
                from db_models import InterviewRecord

                fallback_result = await db.execute(
                    select(InterviewRecord)
                    .filter(
                        InterviewRecord.user_id == expected_user_id,
                        InterviewRecord.document_id == expected_document_id,
                        InterviewRecord.status == "ongoing",
                    )
                    .order_by(InterviewRecord.created_at.desc())
                )
                fallback_record = fallback_result.scalar_one_or_none()
                if fallback_record and fallback_record.session_id != session_id:
                    logger.warning(
                        f"⚠️ 会话ID失配，回退到最新进行中会话: requested={session_id}, fallback={fallback_record.session_id}"
                    )
                    session = await self.recover_session(
                        fallback_record.session_id,
                        db,
                        expected_user_id=expected_user_id,
                        expected_document_id=expected_document_id,
                    )
            
        if not session:
            raise ValueError("面试会话不存在或已过期")
        
        if session.status != "ongoing":
            raise ValueError("面试已结束")
        
        try:
            logger.info(f"\n{'='*60}")
            logger.info(f"📝 第 {session.current_round + 1} 轮答案提交")
            logger.info(f"   答案长度: {len(answer)} 字符")
            logger.info(f"{'='*60}")
            
            # 1️⃣ AI评分（基于缓存的内容）
            logger.info(f"🎯 评分中...")
            evaluation = await self._evaluate_answer(session, answer)
            logger.info(f"   ✅ 得分: {evaluation.score}/100")
            logger.info(f"   📊 关键词覆盖: {evaluation.keyword_coverage:.1%}")
            
            # 2️⃣ 更新进度
            session.current_round += 1
            session.scores.append(evaluation.score)
            
            # 保存QA历史
            kp, question = session.current_question
            session.qa_history.append((
                session.current_round,
                question.question,
                answer,
                evaluation
            ))
            
            # 3️⃣ 判断是否结束
            should_continue = self._should_continue(session)
            
            if not should_continue:
                session.status = "completed"
                logger.info(f"✅ 面试完成！共 {session.current_round} 轮")
                summary = await self._generate_summary(session)
                session.summary = summary  # 保存总结到会话
                logger.info(f"{'='*60}\n")
                
                # 保存面试记录到数据库
                await self._save_interview_record(session, db)
                
                return {
                    'status': 'completed',
                    'round': session.current_round,
                    'evaluation': evaluation.model_dump(),
                    'summary': summary.model_dump()
                }
            
            # 4️⃣ 生成下一个问题
            logger.info(f"❓ 生成下一个问题...")
            next_question = await self._generate_question(session, session.current_round + 1)
            logger.info(f"{'='*60}\n")
            
            return {
                'status': 'ongoing',
                'round': session.current_round,
                'evaluation': evaluation.model_dump(),
                'next_question': {
                    'round': session.current_round + 1,
                    'question': next_question.question,
                    'expected_keywords': next_question.expected_keywords,
                    'hints': next_question.hints
                },
                'progress': {
                    'current': session.current_round + 1,
                    'total': session.max_rounds,
                    'avg_score': sum(session.scores) / len(session.scores) if session.scores else 0
                }
            }
            
        except Exception as e:
            logger.error(f"❌ 处理答案失败: {str(e)}")
            raise
    
    async def _evaluate_answer(
        self,
        session: InterviewSession,
        answer: str
    ) -> InterviewEvaluation:
        """评估答案"""
        
        kp, question = session.current_question
        
        adaptive_context = self._trim_context_for_round(
            session.cached_content['content'],
            round_num=session.current_round + 1,
            phase="evaluation",
            max_rounds=session.max_rounds,
        )

        prompt = f"""作为专业的面试官，评估考生对以下问题的回答。

文档背景内容：
    {adaptive_context}

问题：
{question.question}

期望关键词：{', '.join(question.expected_keywords)}

考生回答：
{answer}

请从以下维度评分（总分100）：
1. 内容准确性（40分）：回答是否符合文档内容
2. 关键词覆盖（30分）：是否涵盖期望的关键词
3. 理解深度（20分）：是否真正理解了核心概念
4. 表达清晰度（10分）：表达是否清晰完整

要求：
- 给出0-100的总分
- 计算关键词覆盖率（0-1之间）
- 提供具体的评价反馈（50-150字）
- 给出改进建议（30-100字）"""

        structured_llm = self.lc_client.with_structured_output(
            InterviewEvaluation,
            method="function_calling"
        )
        
        messages = [
            {"role": "system", "content": "你是一个公正严谨的学术面试官。"},
            {"role": "user", "content": prompt}
        ]
        
        evaluation = await structured_llm.ainvoke(messages)
        
        if evaluation is None:
            raise Exception("AI未能生成有效的评价，请重试")
        
        return evaluation
    
    def _should_continue(self, session: InterviewSession) -> bool:
        """AI自动化决策：判断是否继续"""
        
        # 条件1：达到最大轮次
        if session.current_round >= session.max_rounds:
            logger.info(f"   ⏹️ 决策：已达到最大轮次 {session.max_rounds}")
            return False
        
        # 条件2：最少5轮
        if session.current_round < 5:
            return True
        
        # 条件3：所有知识点已覆盖
        if len(session.asked_point_ids) >= len(session.knowledge_points):
            logger.info(f"   ⏹️ 决策：所有知识点已覆盖")
            return False
        
        # 条件4：得分稳定且高（连续3轮波动<10% 且平均分>80）
        if len(session.scores) >= 5:
            recent_scores = session.scores[-3:]
            if max(recent_scores) - min(recent_scores) < 10:
                avg_score = sum(recent_scores) / len(recent_scores)
                if avg_score > 80:
                    logger.info(f"   ⏹️ 决策：得分稳定且优秀 (平均{avg_score:.1f}分)")
                    return False
        
        # 条件5：连续低分（连续3轮<60分）
        if len(session.scores) >= 3:
            recent_scores = session.scores[-3:]
            if all(score < 60 for score in recent_scores):
                logger.info(f"   ⏹️ 决策：连续低分，建议先复习")
                return False
        
        return True
    
    async def _generate_summary(
        self,
        session: InterviewSession
    ) -> InterviewSummary:
        """生成面试总结"""
        
        # 整理QA历史
        qa_text = "\n\n".join([
            f"第{round_num}轮：\n问题：{q}\n回答：{a}\n得分：{e.score}/100\n评价：{e.feedback}"
            for round_num, q, a, e in session.qa_history
        ])
        
        avg_score = sum(session.scores) / len(session.scores) if session.scores else 0
        
        prompt = f"""作为面试官，为考生生成一份面试总结报告。

面试轮次：{session.current_round}
平均得分：{avg_score:.1f}/100

问答记录：
{qa_text[:6000]}

要求：
1. 总结考生的3-5个优势领域（擅长的知识点）
2. 指出2-4个需要改进的领域
3. 给出100-200字的总体评价
4. 提供具体的学习建议（100-150字）"""

        structured_llm = self.lc_client.with_structured_output(
            InterviewSummary,
            method="function_calling"
        )
        
        messages = [
            {"role": "system", "content": "你是一个专业的学术导师，擅长给出建设性的反馈。"},
            {"role": "user", "content": prompt}
        ]
        
        summary = await structured_llm.ainvoke(messages)
        
        if summary is None:
            raise Exception("AI未能生成有效的总结，请重试")
        
        # 填充统计数据
        summary.total_rounds = session.current_round
        summary.average_score = avg_score
        
        return summary
    
    def get_session(self, session_id: str) -> Optional[InterviewSession]:
        """获取会话"""
        return self.active_sessions.get(session_id)
    
    async def abort_interview(
        self,
        session_id: str,
        db: Optional[AsyncSession] = None,
        expected_user_id: Optional[int] = None,
        expected_document_id: Optional[int] = None,
    ) -> None:
        """中止面试"""
        logger.info(f"🔍 尝试中止面试: session_id={session_id}")
        logger.info(f"🔍 当前活动会话: {list(self.active_sessions.keys())}")

        session = self.active_sessions.get(session_id)
        if session:
            if expected_document_id is not None and session.document_id != expected_document_id:
                raise ValueError("面试会话与当前文档不匹配")

            session.status = "aborted"
            logger.info(f"🛑 面试已中止: {session_id}, 已回答轮次: {len(session.qa_history)}")
            # 保存中止的面试记录
            if db:
                await self._save_interview_record(session, db)
            else:
                logger.warning(f"⚠️ 数据库会话为空，无法保存面试记录")
            return

        if not db:
            raise ValueError("数据库会话不可用，无法中止面试")

        from db_models import InterviewRecord

        # 先按 session_id 精确查找
        result = await db.execute(
            select(InterviewRecord).filter(InterviewRecord.session_id == session_id)
        )
        record = result.scalar_one_or_none()

        if record and expected_user_id is not None and record.user_id != expected_user_id:
            record = None
        if record and expected_document_id is not None and record.document_id != expected_document_id:
            record = None

        # session_id 失配时，回退到该文档最新进行中记录
        if not record and expected_user_id is not None and expected_document_id is not None:
            fallback_result = await db.execute(
                select(InterviewRecord)
                .filter(
                    InterviewRecord.user_id == expected_user_id,
                    InterviewRecord.document_id == expected_document_id,
                    InterviewRecord.status == "ongoing",
                )
                .order_by(InterviewRecord.created_at.desc())
            )
            record = fallback_result.scalar_one_or_none()

        if not record:
            raise ValueError("面试会话不存在或已过期")

        record.status = "aborted"
        record.completed_at = datetime.now(timezone.utc)
        await db.commit()
        logger.info(f"🛑 面试已中止(数据库回退): session_id={record.session_id}, record_id={record.id}")

        # 尝试同步清理内存会话（如果存在）
        if record.session_id in self.active_sessions:
            self.active_sessions.pop(record.session_id, None)
        else:
            self.active_sessions.pop(session_id, None)
    
    async def resume_interview(
        self,
        record_id: int,
        document,
        vector_store_manager,
        db: AsyncSession
    ) -> Tuple[str, Dict]:
        """
        恢复中止的面试
        
        Returns:
            (session_id, next_question_data)
        """
        try:
            from db_models import InterviewRecord
            
            logger.info(f"\n{'='*60}")
            logger.info(f"🔄 恢复面试...")
            logger.info(f"   记录ID: {record_id}")
            logger.info(f"{'='*60}")
            
            # 1. 从数据库加载面试记录
            result = await db.execute(
                select(InterviewRecord).filter(InterviewRecord.id == record_id)
            )
            record = result.scalar_one_or_none()
            if not record:
                raise ValueError("面试记录不存在")
            
            if record.status != "aborted":
                raise ValueError("只能恢复已中止的面试")
            
            logger.info(f"   ✅ 加载面试记录成功")
            logger.info(f"   - 已完成轮次: {record.total_rounds}")
            logger.info(f"   - 平均分: {record.average_score:.1f}")
            
            # 2. 重建会话
            # 重新进行向量检索
            key_content = await self._retrieve_innovation_content(document, vector_store_manager)
            
            # 从数据库恢复知识点
            knowledge_points = [
                KnowledgePoint(**kp) for kp in record.knowledge_points
            ]
            
            # 创建新的会话ID（继续使用新ID，但关联到同一个record_id）
            new_session_id = f"interview-{uuid.uuid4()}"
            session = InterviewSession(new_session_id, document.id, key_content)
            session.knowledge_points = knowledge_points
            
            # 3. 恢复进度
            session.current_round = record.total_rounds
            
            # 恢复问答历史和分数
            for qa in record.qa_history:
                evaluation = InterviewEvaluation(
                    score=qa['evaluation']['score'],
                    feedback=qa['evaluation']['feedback'],
                    suggestions=qa['evaluation']['suggestions'],
                    keyword_coverage=qa['evaluation']['keyword_coverage']
                )
                session.qa_history.append((
                    qa['round'],
                    qa['question'],
                    qa['answer'],
                    evaluation
                ))
                session.scores.append(qa['evaluation']['score'])
            
            # 恢复已问过的知识点
            for qa in record.qa_history:
                # 根据问题内容推断知识点（简化处理）
                for kp in knowledge_points:
                    if kp.content[:30] in qa['question']:
                        session.asked_point_ids.add(kp.id)
                        break
            
            session.status = "ongoing"
            session.original_record_id = record_id  # 记录原始记录ID，用于更新而非新建
            self.active_sessions[new_session_id] = session

            # 关键：恢复后立即持久化新的 session_id 与状态
            # 避免 API 进程在校验会话时因“只存在于 worker 内存”而 404
            record.session_id = new_session_id
            record.status = "ongoing"
            await db.commit()
            
            logger.info(f"   ✅ 会话恢复成功: {new_session_id}")
            logger.info(f"   - 原始记录ID: {record_id}")
            logger.info(f"   - 当前轮次: {session.current_round}")
            logger.info(f"   - 剩余轮次: {session.max_rounds - session.current_round}")
            
            # 4. 生成下一个问题
            logger.info(f"❓ 生成下一个问题...")
            next_question = await self._generate_question(session, session.current_round + 1)
            logger.info(f"{'='*60}\n")
            
            return new_session_id, {
                'round': session.current_round + 1,
                'total_rounds': session.max_rounds,
                'question': next_question.question,
                'expected_keywords': next_question.expected_keywords,
                'hints': next_question.hints,
                'resumed': True,
                'previous_rounds': session.current_round,
                'avg_score': sum(session.scores) / len(session.scores) if session.scores else 0
            }
            
        except Exception as e:
            logger.error(f"❌ 恢复面试失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
            
    async def recover_session(
        self,
        session_id: str,
        db: AsyncSession,
        expected_user_id: Optional[int] = None,
        expected_document_id: Optional[int] = None,
    ) -> Optional[InterviewSession]:
        """从数据库恢复活动会话（用于跨进程或重启后）"""
        try:
            from db_models import InterviewRecord, Document
            from document_service.vector_store import vector_store_manager
            
            # 1. 查询记录
            result = await db.execute(
                select(InterviewRecord).filter(InterviewRecord.session_id == session_id)
            )
            record = result.scalar_one_or_none()
            
            if not record:
                return None

            if expected_user_id is not None and record.user_id != expected_user_id:
                logger.warning(f"⚠️ 会话用户不匹配: session_id={session_id}, expected_user_id={expected_user_id}, actual_user_id={record.user_id}")
                return None

            if expected_document_id is not None and record.document_id != expected_document_id:
                logger.warning(f"⚠️ 会话文档不匹配: session_id={session_id}, expected_document_id={expected_document_id}, actual_document_id={record.document_id}")
                return None
                
            if record.status == "completed" or record.status == "aborted":
                # 已结束的会话不能隐式恢复，需要显式resume
                return None

            logger.info(f"🔄正在恢复会话: {session_id}, status={record.status}")

            # 2. 获取文档 (为了重建上下文)
            result = await db.execute(
                select(Document).filter(Document.id == record.document_id)
            )
            document = result.scalar_one_or_none()
            if not document:
                return None

            # 3. 重建上下文 (需要重新检索)
            # 注意：这里会重新触发向量检索，有一定开销，但保证了数据一致性
            key_content = await self._retrieve_innovation_content(document, vector_store_manager)
            
            # 4. 重建会话对象
            session = InterviewSession(session_id, document.id, key_content)
            
            # 恢复知识点
            session.knowledge_points = [
                KnowledgePoint(**kp) for kp in record.knowledge_points
            ]
            
            # 恢复进度
            session.current_round = record.total_rounds
            session.original_record_id = record.id
            session.status = record.status
            
            # 恢复QA历史和分数
            for qa in record.qa_history:
                # 兼容旧数据格式
                evaluation_data = qa.get('evaluation', {})
                evaluation = InterviewEvaluation(
                    score=evaluation_data.get('score', 0),
                    feedback=evaluation_data.get('feedback', ''),
                    suggestions=evaluation_data.get('suggestions', []),
                    keyword_coverage=evaluation_data.get('keyword_coverage', 0)
                )
                
                session.qa_history.append((
                    qa.get('round', 0),
                    qa.get('question', ''),
                    qa.get('answer', ''),
                    evaluation
                ))
                session.scores.append(evaluation.score)
                
                # 恢复已问过的知识点
                for kp in session.knowledge_points:
                     if kp.content[:10] in qa.get('question', ''):
                         session.asked_point_ids.add(kp.id)

            # 恢复当前问题 (重新生成)
            logger.info(f"❓ 恢复当前轮次 ({session.current_round + 1}) 的问题...")
            next_question = await self._generate_question(session, session.current_round + 1)
            
            # Add to active sessions
            self.active_sessions[session_id] = session
            return session
            
        except Exception as e:
            logger.error(f"❌ 恢复会话失败: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    async def _save_interview_record(self, session: InterviewSession, db: Optional[AsyncSession] = None) -> None:
        """保存面试记录到数据库"""
        try:
            from db_models import InterviewRecord
            from datetime import datetime
            used_existing_current = False
            
            if not db:
                logger.warning("⚠️ 数据库未连接，无法保存面试记录")
                return
            
            # 计算平均分
            avg_score = sum(session.scores) / len(session.scores) if session.scores else 0
            
            # 构建问答历史
            # qa_history 格式: List[Tuple[round, question, answer, evaluation]]
            qa_history = []
            for round_num, question, answer, evaluation in session.qa_history:
                qa_history.append({
                    "round": round_num,
                    "question": question,
                    "expected_keywords": [],  # 问题对象没有存储，这里为空
                    "answer": answer,
                    "evaluation": {
                        "score": evaluation.score,
                        "feedback": evaluation.feedback,
                        "suggestions": evaluation.suggestions,
                        "keyword_coverage": evaluation.keyword_coverage
                    }
                })
            
            # 从数据库获取文档的 user_id
            from db_models import Document
            result = await db.execute(
                select(Document).filter(Document.id == session.document_id)
            )
            document = result.scalar_one_or_none()
            if not document:
                logger.error(f"❌ 未找到文档: document_id={session.document_id}")
                return
            
            # 如果有原始记录ID，更新已有记录；否则创建新记录
            if session.original_record_id:
                logger.info(f"   ℹ️ 检测到恢复的面试，更新原记录 ID={session.original_record_id}")
                result = await db.execute(
                    select(InterviewRecord).filter(InterviewRecord.id == session.original_record_id)
                )
                record = result.scalar_one_or_none()
                if record:
                    # 更新已有记录
                    record.session_id = session.session_id
                    record.total_rounds = len(session.qa_history)
                    record.average_score = avg_score
                    record.status = session.status
                    record.qa_history = qa_history
                    record.summary = session.summary.model_dump() if (session.summary and hasattr(session.summary, 'model_dump')) else None
                    record.knowledge_points = [kp.model_dump() for kp in session.knowledge_points]
                    record.completed_at = datetime.now(timezone.utc)

                else:
                    session.original_record_id = None  # 重置，走创建逻辑
            
            if not session.original_record_id:
                # 初始化阶段（ongoing 且尚无问答）优先复用当前记录，避免 unique_current_interview 冲突
                is_bootstrap_ongoing = session.status == "ongoing" and len(session.qa_history) == 0
                if is_bootstrap_ongoing:
                    current_result = await db.execute(
                        select(InterviewRecord).filter(
                            InterviewRecord.document_id == session.document_id,
                            InterviewRecord.user_id == document.user_id,
                            InterviewRecord.is_current == True,
                        ).order_by(InterviewRecord.created_at.desc())
                    )
                    current_record = current_result.scalar_one_or_none()
                    if current_record:
                        current_record.session_id = session.session_id
                        current_record.total_rounds = len(session.qa_history)
                        current_record.average_score = avg_score
                        current_record.status = session.status
                        current_record.qa_history = qa_history
                        current_record.summary = session.summary.model_dump() if (session.summary and hasattr(session.summary, 'model_dump')) else None
                        current_record.knowledge_points = [kp.model_dump() for kp in session.knowledge_points]
                        current_record.completed_at = None
                        record = current_record
                        used_existing_current = True

                if not used_existing_current:
                    # 1. 将旧记录标记为非当前 (避免 unique_current_interview 冲突)
                    await db.execute(
                        update(InterviewRecord)
                        .where(
                            InterviewRecord.document_id == session.document_id,
                            InterviewRecord.user_id == document.user_id,
                            InterviewRecord.is_current == True
                        )
                        .values(is_current=False)
                    )

                    # 2. 计算下一个版本号
                    version_query = await db.execute(
                        select(func.coalesce(func.max(InterviewRecord.version), 0)).filter(
                            InterviewRecord.document_id == session.document_id,
                            InterviewRecord.user_id == document.user_id
                        )
                    )
                    next_version = (version_query.scalar() or 0) + 1

                    record = InterviewRecord(
                        document_id=session.document_id,
                        user_id=document.user_id,
                        session_id=session.session_id,
                        total_rounds=len(session.qa_history),
                        average_score=avg_score,
                        status=session.status,
                        qa_history=qa_history,
                        version=next_version,
                        is_current=True, # 标记为当前

                        summary=session.summary.model_dump() if (session.summary and hasattr(session.summary, 'model_dump')) else None,
                        knowledge_points=[kp.model_dump() for kp in session.knowledge_points],
                        completed_at=datetime.now(timezone.utc) if session.status in ['completed', 'aborted'] else None
                    )

                    db.add(record)
            
            await db.commit()
            await db.refresh(record)
            
            # 保存面试记录到笔记（完成或中断时都保存）
            if session.status in ['completed', 'aborted']:
                try:
                    from db_models import Note
                    import json
                    
                    
                    # 刷新 record 以获取生成的 ID（如果是新创建的）
                    if not session.original_record_id:
                        await db.refresh(record)
                    
                    # 创建笔记内容
                    note_content = {
                        "session_id": session.session_id,
                        "total_rounds": len(session.qa_history),
                        "average_score": avg_score,
                        "status": session.status,
                        "qa_history": qa_history[:5] if len(qa_history) > 5 else qa_history,  # 只保存前5轮的简要内容
                        "interview_record_id": record.id if hasattr(record, 'id') else None
                    }
                    

                    
                    # 按 interview_record_id 做 upsert，避免重复膨胀
                    note_payload = json.dumps(note_content, ensure_ascii=False, indent=2)
                    note = None

                    result = await db.execute(
                        select(Note).filter(
                            Note.user_id == document.user_id,
                            Note.document_id == session.document_id,
                            Note.type == 'interview'
                        ).order_by(Note.created_at.desc())
                    )
                    existing_notes = result.scalars().all()

                    for existing_note in existing_notes:
                        try:
                            parsed = json.loads(existing_note.content) if existing_note.content else {}
                        except Exception:
                            continue

                        if str(parsed.get('interview_record_id')) == str(record.id):
                            note = existing_note
                            break

                    if note:
                        note.title = '面试记录'
                        note.content = note_payload
                    else:
                        note = Note(
                            user_id=document.user_id,
                            document_id=session.document_id,
                            title='面试记录',
                            content=note_payload,
                            type='interview'
                        )
                        db.add(note)

                    await db.commit()
                    await db.refresh(note)
                    logger.info(f"✅ 面试笔记已保存: note_id={note.id}, status={session.status}, upsert_record_id={record.id}")
                except Exception as note_error:
                    logger.warning(f"⚠️ 保存面试笔记失败（不影响主流程）: {note_error}")
                    import traceback
                    traceback.print_exc()
                    # 不回滚，因为面试记录已经保存成功
            else:
                logger.warning(f"⚠️ 面试状态不是 completed 或 aborted，不保存笔记: status={session.status}")
            
            action = "更新" if (session.original_record_id or used_existing_current) else "保存"
            logger.info(f"✅ 面试记录已{action}: session_id={session.session_id}, total_rounds={len(session.qa_history)}, avg_score={avg_score:.1f}, status={session.status}")
            
        except Exception as e:
            logger.error(f"❌ 保存面试记录失败: {str(e)}")
            import traceback
            traceback.print_exc()
            if db:
                await db.rollback()
            raise


# 全局面试管理器实例（将在main.py中初始化）
interview_manager: Optional[InterviewManager] = None


def initialize_interview_manager(api_key: str, base_url: str, model: str = "qwen-max"):
    """初始化全局面试管理器"""
    global interview_manager
    interview_manager = InterviewManager(api_key, base_url, model)
    logger.info(f"✅ 面试管理器初始化成功")
    return interview_manager
