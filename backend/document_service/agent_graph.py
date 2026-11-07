"""
LangGraph RAG Agent — Unified ReAct Architecture

Single graph: agent ↔ tools loop
No Plan/Simple split, no subgraph orchestration.
The agent decides on its own whether to search, plan, ask clarification, or respond.
"""

import re
import json
import uuid
import asyncio
import random
from typing import TypedDict, Annotated, List, Literal, Any, Optional
from langgraph.graph import StateGraph, END, START
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from utils.logger import get_logger
from langchain_core.messages import (
    SystemMessage, AIMessage, ToolMessage, HumanMessage, BaseMessage
)
from langchain_core.messages import message_chunk_to_message

from document_service.token_utils import budget_messages
from llm_service import get_qa_llm, get_model_config

logger = get_logger(__name__)


# ==================== State ====================

class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    iteration_count: int
    max_iterations: int
    evaluator_count: int
    evaluator_next: str
    evaluator_visible_status: str
    evaluator_visible_reason: str


# ==================== Constants ====================

MAX_ITERATIONS_FAST = 1
MAX_ITERATIONS_AGENT = 4


# ==================== Agent ====================

class RAGAgent:
    """
    Unified ReAct RAG Agent.

    Graph: START → agent → evaluator? → (agent | tools | END)
                    tools → agent

    The agent autonomously decides:
    - Whether to search (via search_document tool)
    - Whether to create a plan (via create_plan tool)
    - Whether to ask clarification (via ask_clarification tool)
    - Whether to generate visualizations
    - When to produce the final answer (no tool calls → END)
    """

    def __init__(
        self,
        llm,
        tools=None,
        max_iterations: Optional[int] = None,
        evaluator_llm=None,
        enable_evaluator: bool = True,
    ):
        self.llm = llm
        self.evaluator_llm = evaluator_llm or llm
        self.tools = tools or []
        self.enable_evaluator = enable_evaluator
        self._fallback_model_keys = ("qwen", "deepseek")
        self._fallback_clients = {}

        # Pre-bind tools once (avoid repeated bind_tools calls per iteration)
        self.llm_with_tools = self.llm.bind_tools(self.tools) if self.tools else self.llm

        # Use intelligent defaults if not specified
        if max_iterations is None:
            self.max_iterations = MAX_ITERATIONS_AGENT if enable_evaluator else MAX_ITERATIONS_FAST
        else:
            self.max_iterations = max_iterations
        self.graph = self._build_graph()

    def _build_graph(self):
        workflow = StateGraph(AgentState)

        workflow.add_node("agent", self._agent_node)
        # ★ ToolNode uses asyncio.gather() internally for concurrent tool execution.
        # When the LLM issues multiple tool_calls in one response (parallel_tool_calls),
        # all tools run concurrently. handle_tool_errors ensures one tool failure
        # doesn't crash the entire batch — the agent gets an error message and can recover.
        workflow.add_node("tools", ToolNode(
            self.tools,
            handle_tool_errors="⚠️ 工具执行出错，请换个方式重试或直接基于已有信息回答。",
        ))
        workflow.add_node("evaluator", self._evaluator_node)

        workflow.add_edge(START, "agent")
        workflow.add_conditional_edges(
            "agent",
            self._route_after_agent,
            {"tools": "tools", "evaluator": "evaluator", "__end__": END},
        )
        workflow.add_conditional_edges(
            "evaluator",
            self._route_after_evaluator,
            {"agent": "agent", "__end__": END},
        )
        workflow.add_edge("tools", "agent")

        return workflow.compile()

    # ==================== Logic ====================

    async def _agent_node(self, state: AgentState, config=None):
        messages = state["messages"]
        iteration = state.get("iteration_count", 0)
        limit = state.get("max_iterations", self.max_iterations)

        # Safety forcing: the last allowed agent call must be a final answer.
        # Iteration count starts at 0, so limit=4 means:
        # Iter 0-2: tools allowed, Iter 3: tools disabled and final answer.
        # Otherwise the graph can spend the last call on tool planning and then
        # route directly to END with answer_len=0.
        final_iteration = max(0, limit - 1)
        is_final = iteration >= final_iteration

        # ★ Message filtering: reduce context on subsequent iterations
        # 🔧 Fix: Only filter for single-turn (Fast Mode). 
        # For Agent Mode (limit > 1), we MUST preserve full context (reasoning + tool results)
        # otherwise the model forgets what it has done and repeats itself.
        if iteration > 0:
            if limit == 1:
                messages = self._filter_messages(messages)
            else:
                # In Agent mode, we still want to compress VERY old tool messages to save tokens
                # while keeping the most recent ones intact.
                messages = self._compress_agent_history(messages)

        # ★ Token budget: prevent context overflow
        messages = budget_messages(messages, max_tokens=28000)

        # ★ Sanitize: Ensure valid tool call sequences for OpenAI (fix for 400 error)
        messages = self._sanitize_messages(messages)

        # Log context size for debugging
        from document_service.token_utils import estimate_message_tokens
        ctx_tokens = sum(estimate_message_tokens(m) for m in messages)
        msg_counts = {}
        for m in messages:
            t = type(m).__name__
            msg_counts[t] = msg_counts.get(t, 0) + 1
        logger.info(f"📊 [Agent] Iter {iteration}/{limit}: ~{ctx_tokens} tokens, msgs={msg_counts}")

        # Inject urgency message when approaching limit
        # Only inject if limit > 2 (for multi-turn agents), otherwise it's spam for single-turn
        is_near_limit = (iteration >= limit - 2) and not is_final and limit > 2

        if is_near_limit:
            messages.append(SystemMessage(content=(
                "⚠️ 【系统提醒】你的工具调用次数即将耗尽（还剩最后 1 次机会）。"
                "请在下一轮直接给出完整的最终回答，不要再发起新的工具调用。"
                "基于你已有的检索信息，组织一个结构完整、内容详实的最终回复。"
            )))
            logger.warning(f"⚠️ [Agent] Iteration {iteration}/{limit}: Injected near-limit warning")

        if is_final:
            # Last iteration: no tools, forced answer
            messages.append(SystemMessage(content=(
                "🛑 【系统指令】这是你的最后一轮输出机会，工具已不可用。"
                "请基于已有的检索信息，生成一份详细、完整的最终回答。"
                "虽然是最后一次机会，但请务必把问题解释清楚，不要草率结束。"
            )))
            logger.info(f"🛑 [Agent] Iteration {iteration}/{limit}: Final, tools DISABLED")
            response = await self._ainvoke_with_transport_retry(self.llm, messages, "agent-final", config=config)
            if not (getattr(response, "content", "") or "").strip() and not getattr(response, "tool_calls", None):
                logger.warning("⚠️ [Agent] Final iteration produced empty content; returning visible fallback")
                response = AIMessage(content=(
                    "⚠️ 模型完成了思考，但没有返回可展示的正文。"
                    "请重新提问，或缩小问题范围后再试。"
                ))
        else:
            # Normal iteration: tools available
            response = await self._ainvoke_with_transport_retry(self.llm_with_tools, messages, "agent-tools", config=config)
            
            # 🛡️ Fallback: Check for leaked tool call (raw text)
            if hasattr(response, "content") and response.content and not response.tool_calls:
                # Extract user question as fallback search query
                user_question = ""
                for m in reversed(messages):
                    if isinstance(m, HumanMessage) and isinstance(m.content, str):
                        user_question = m.content
                        break
                fallback = self._parse_leaked_tool_call(response.content, user_question)
                if fallback:
                    logger.info(f"✅ [Agent] Recovered leaked tool call: {fallback['name']}({fallback['args']})")
                    response.tool_calls = [fallback]
                    # Clear content because StreamHandler suppressed it, and we want tool execution next.
                    response.content = ""

        return {"messages": [response], "iteration_count": iteration + 1}

    def _route_after_agent(self, state: AgentState) -> Literal["tools", "evaluator", "__end__"]:
        last_message = state["messages"][-1]

        limit = state.get("max_iterations") or self.max_iterations
        if state.get("iteration_count", 0) >= limit:
            logger.info(f"🧭 [Route] agent -> END (iteration limit reached: {state.get('iteration_count', 0)}/{limit})")
            return "__end__"

        if hasattr(last_message, "tool_calls") and last_message.tool_calls:
            logger.info("🧭 [Route] agent -> tools (tool_calls detected)")
            return "tools"

        if not self.enable_evaluator:
            logger.info("🧭 [Route] agent -> END (evaluator disabled for fast mode)")
            return "__end__"

        logger.info("🧭 [Route] agent -> evaluator (plain answer, no tool_calls)")
        return "evaluator"

    async def _evaluator_node(self, state: AgentState, config=None):
        """
        Self-evaluation node:
        - Produces structured pass/fail decision
        - PASS: route directly to END
        - FAIL: provide reason/suggestion to agent, then continue
        """
        limit = state.get("max_iterations", self.max_iterations)
        iteration = state.get("iteration_count", 0)
        evaluator_count = state.get("evaluator_count", 0)
        messages = state["messages"]
        last_message = messages[-1] if messages else None

        # Fast mode or near hard limit: skip self-evaluation loop
        if limit <= 1 or iteration >= limit:
            logger.info(f"🧪 [Evaluator] Skip -> END (limit={limit}, iteration={iteration})")
            return {
                "evaluator_next": "__end__",
                "evaluator_visible_status": "skip",
                "evaluator_visible_reason": "评估已跳过（快速模式或达到迭代上限）",
            }

        # Evaluate only plain answer messages (no tool_calls)
        if not isinstance(last_message, AIMessage):
            logger.info("🧪 [Evaluator] Skip -> END (last_message is not AIMessage)")
            return {
                "evaluator_next": "__end__",
                "evaluator_visible_status": "skip",
                "evaluator_visible_reason": "评估已跳过（最后消息不是 AI 回答）",
            }
        if getattr(last_message, "tool_calls", None):
            logger.info("🧪 [Evaluator] Skip -> END (last_message still has tool_calls)")
            return {
                "evaluator_next": "__end__",
                "evaluator_visible_status": "skip",
                "evaluator_visible_reason": "评估已跳过（当前轮仍有工具调用）",
            }

        # Avoid repeated self-review loops
        # Allow up to 4 evaluation rounds to give agent a chance to fix things
        if evaluator_count >= 4:
            logger.info(f"🧪 [Evaluator] Skip -> END (already evaluated: {evaluator_count})")
            return {
                "evaluator_next": "__end__",
                "evaluator_visible_status": "skip",
                "evaluator_visible_reason": "评估已跳过（本轮已评估过）",
            }

        tool_names = [getattr(t, "name", "") for t in self.tools if getattr(t, "name", "")]
        tool_hint = "、".join(tool_names) if tool_names else "可用工具"

        user_question = ""
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_question = self._message_to_text(msg.content)
                break

        last_answer_text = self._message_to_text(last_message.content)

        # ★ Context Meta-Info: Detect retrieval and viz tool usage
        # Scan backwards from end to HumanMessage boundary.
        # Must NOT break at intermediate AIMessages — tools may span multiple rounds.
        has_generated_chart = False
        has_generated_flowchart = False
        has_performed_retrieval = False
        retrieval_success = False
        
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                break  # Stop at user's question — only scan current turn
            if not isinstance(msg, ToolMessage):
                continue
            if msg.name == "generate_learning_path" and "mermaid_code" in str(msg.content):
                has_generated_flowchart = True
            elif msg.name == "generate_data_plot":
                has_generated_chart = True
            elif msg.name == "search_document":
                has_performed_retrieval = True
                content_str = str(msg.content)
                if "Retrieval Successful" in content_str or "[Passage 1]" in content_str:
                    retrieval_success = True

        # ★ Short-circuit PASS: skip evaluator LLM call when answer is obviously sufficient
        if self._should_short_circuit_evaluator(last_answer_text, has_performed_retrieval, retrieval_success):
            logger.info(f"⚡ [Evaluator] Short-circuit PASS -> END (answer={len(last_answer_text)}chars, retrieval={retrieval_success})")
            return {
                "evaluator_count": evaluator_count + 1,
                "evaluator_next": "__end__",
                "evaluator_visible_status": "pass",
                "evaluator_visible_reason": "回答详实充分（快速评估通过）",
            }

        # ★ Short-circuit FAIL: rule-based detection of unfulfilled tool promises
        # Catches the common "LLM says it will create a chart/flowchart but doesn't call the tool"
        unfulfilled = self._detect_unfulfilled_tool_promise(
            last_answer_text, has_generated_chart, has_generated_flowchart, has_performed_retrieval
        )
        if unfulfilled:
            guidance = SystemMessage(content=(
                f"🔎 【自我反思】\n"
                f'你在回答中提到了"{unfulfilled["mention"]}"，但没有实际调用 `{unfulfilled["tool"]}` 工具。\n'
                f"请立即调用该工具完成可视化，然后给出完整回答。"
            ))
            logger.info(f"⚡ [Evaluator] Rule-based FAIL: promised '{unfulfilled['tool']}' but didn't call it")
            return {
                "messages": [guidance],
                "evaluator_count": evaluator_count + 1,
                "evaluator_next": "agent",
                "evaluator_visible_status": "fail",
                "evaluator_visible_reason": f"回答中提到了{unfulfilled['mention']}但未生成",
            }

        context_notes = []
        if has_performed_retrieval:
            if retrieval_success:
                context_notes.append("【系统注】Agent 已经执行了文档检索并成功获得了参考片段，当前回答是基于检索到的文档内容生成的。请通过评估回答的深度和完整性来判断其质量。")
            else:
                context_notes.append("【系统注】Agent 尝试了文档检索，但未找到直接相关的片段。其回答可能是基于文档中已知背景或表明文档中确实无相关信息。")
        else:
            context_notes.append("【系统注】Agent 尚未执行检索。如果问题必须参考文档才能回答，这可能是一个缺口。")

        if has_generated_flowchart:
            context_notes.append("【系统注】上下文中已生成了流程图/思维导图。")
        if has_generated_chart:
            context_notes.append("【系统注】上下文中已生成了数据图表。")
            
        context_str = "\n".join(context_notes)

        evaluation_passed = False
        fail_reason = "当前回答仍有信息缺口，需要补充。"
        next_step_hint = (
            "请根据缺口自主选择下一步动作（可调用任意合适工具或直接补充解释），"
            "然后给出更完整回答。"
        )

        try:
            eval_messages = [
                SystemMessage(content=(
                    "我是一个专业的智能自省助手（Self-Reflection Assistant）。"
                    "我的任务是站在主智能体的视角，审视其回答是否真正完整、详细地解决了用户的所有疑问。"
                    "我必须只输出 JSON，不要输出任何额外文本。"
                )),
                HumanMessage(content=(
                    f"用户问题：{user_question}\n\n"
                    f"我目前的回答：{last_answer_text}\n\n"
                    f"📊 运行背景：\n{context_str}\n\n"
                    f"可用工具：{tool_hint}\n\n"
                    "请检查我的回答是否已满足用户问题，并只输出如下 JSON：\n"
                    "{\n"
                    "  \"pass\": true/false,\n"
                    "  \"reason\": \"一句话说明判断依据\",\n"
                    "  \"next_step\": \"若pass=false，以第一人称（'我'）给出后续改进建议；若pass=true写'END'\"\n"
                    "}\n"
                    "要求：\n"
                    "- 如果背景显示已执行检索，请相信我回答的真实性，重点评估“内容是否足够丰富、是否覆盖了问题的各个方面”。\n"
                    "- 不要因为我没看到原文而质疑回答的真实性。\n"
                    "- next_step 必须是第一人称，例如：“我意识到我还没有解释 X 的具体原理，我应该继续调用工具查找...”。\n"
                    "- pass=true 表示可直接结束，无需再补充。\n"
                    "- pass=false 表示存在缺口或回答太简略，需要继续。\n"
                    "- reason 与 next_step 必须简洁具体。"
                )),
            ]
            # ★ Streaming evaluator with early JSON termination
            candidate = await self._astream_evaluator_with_retry(eval_messages, config=config)
            decision = self._parse_evaluator_decision(candidate)
            if decision:
                evaluation_passed = bool(decision.get("pass", False))
                fail_reason = str(decision.get("reason") or fail_reason)
                parsed_next = str(decision.get("next_step") or "").strip()
                if parsed_next and parsed_next.upper() != "END":
                    next_step_hint = parsed_next
        except Exception as e:
            logger.warning(f"⚠️ [Evaluator] eval failed, fallback guidance: {e}")

        if evaluation_passed:
            logger.info(f"🧪 [Evaluator] PASS ({evaluator_count + 1}/4) -> END")
            return {
                "evaluator_count": evaluator_count + 1,
                "evaluator_next": "__end__",
                "evaluator_visible_status": "pass",
                "evaluator_visible_reason": fail_reason,
            }

        guidance = SystemMessage(content=(
            "🔎 【自我反思 / Self-Reflection】\n"
            f"不足之处：{fail_reason}\n"
            f"改进设想：{next_step_hint}\n\n"
            "请基于上述反思，继续完善回答。"
        ))

        logger.info(f"🧪 [Evaluator] FAIL ({evaluator_count + 1}/4) -> agent")
        return {
            "messages": [guidance],
            "evaluator_count": evaluator_count + 1,
            "evaluator_next": "agent",
            "evaluator_visible_status": "fail",
            "evaluator_visible_reason": fail_reason,
        }

    @staticmethod
    def _message_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
                else:
                    parts.append(str(item))
            return "\n".join(parts).strip()
        return str(content)

    @staticmethod
    def _parse_evaluator_decision(raw_text: str) -> dict:
        if not raw_text:
            return {}
        text = raw_text.strip()
        try:
            return json.loads(text)
        except Exception:
            pass

        # Try extracting the first JSON object block
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start:end + 1])
        except Exception:
            pass

        return {}

    async def _ainvoke_with_transport_retry(self, llm_client, messages: list, tag: str, config=None):
        """
        Invoke LLM with resilient retry/fallback strategy.
        
        Strategy:
        1) For 429/engine_overloaded: targeted exponential backoff retries
        2) If still failing: switch to backup API/model from config
        3) For transport errors: return graceful degraded message
        
        ⚠️ 重要：当 astream_events 调用此方法时，部分 streaming tokens 可能
        已经通过 on_chat_model_stream 事件发送到前端。如果此处抛出异常，
        前端会看到被截断的响应。因此 fallback message 的 content 必须包含
        足够的信息让用户知道发生了什么。
        """
        max_rate_limit_retries = 3
        need_tools = llm_client is self.llm_with_tools

        for attempt in range(max_rate_limit_retries + 1):
            try:
                response = await self._astream_collect_response(llm_client, messages, config=config)
                self._log_response_metadata(response, tag)
                return response
            except Exception as exc:
                # 429 / engine_overloaded: dedicated retry layer with backoff
                if self._is_rate_limit_or_overload_error(exc):
                    if attempt < max_rate_limit_retries:
                        delay = min(12.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.8)
                        logger.warning(
                            f"⏳ [{tag}] upstream 429/overload, retry {attempt + 1}/{max_rate_limit_retries} "
                            f"after {delay:.2f}s: {exc}"
                        )
                        await asyncio.sleep(delay)
                        continue

                    logger.error(f"❌ [{tag}] upstream 429/overload persisted after retries: {exc}")
                    fallback_response = await self._ainvoke_with_api_fallback(
                        messages=messages,
                        need_tools=need_tools,
                        tag=tag,
                        last_exc=exc,
                    )
                    if fallback_response is not None:
                        return fallback_response

                    return AIMessage(
                        content=(
                            "\n\n---\n⚠️ 当前模型服务繁忙（429），已自动重试并尝试切换备用模型但仍失败。"
                            "请稍后再试。"
                        )
                    )

                if not self._is_transient_stream_error(exc):
                    raise
                logger.error(f"❌ [{tag}] upstream transport error (SDK retries exhausted): {exc}")
                return AIMessage(
                    content=(
                        "\n\n---\n⚠️ 当前请求遇到上游网络抖动，已自动重试但仍失败。"
                        "以上回答可能不完整，请重新提问。"
                    )
                )

        # Should be unreachable because all branches above return.
        return AIMessage(content="⚠️ 请求未完成，请重试。")

    async def _astream_collect_response(self, llm_client, messages: list, config=None) -> AIMessage:
        """
        Stream tokens explicitly and collect the final AIMessage for graph routing.

        LangGraph can surface child chat-model stream events while this method
        iterates over `astream()`. Using `ainvoke()` here can make some providers
        aggregate the whole response before events reach the UI, which turns the
        QA page into one-shot output.
        """
        full_chunk = None

        try:
            async for chunk in llm_client.astream(messages, config=config):
                if full_chunk is None:
                    full_chunk = chunk
                else:
                    full_chunk = full_chunk + chunk
        except (NotImplementedError, AttributeError):
            response = await llm_client.ainvoke(messages, config=config)
            return response if isinstance(response, AIMessage) else AIMessage(content=str(response))

        if full_chunk is None:
            return AIMessage(content="")

        response = message_chunk_to_message(full_chunk)
        return response if isinstance(response, AIMessage) else AIMessage(content=str(response.content or ""))

    @staticmethod
    def _log_response_metadata(response, tag: str):
        """Log finish reason and token usage for diagnosis."""
        if not (hasattr(response, "response_metadata") and response.response_metadata):
            return
        finish = response.response_metadata.get("finish_reason", "?")
        usage = response.response_metadata.get("token_usage") or response.response_metadata.get("usage") or {}
        p_tok = usage.get("prompt_tokens", "?")
        c_tok = usage.get("completion_tokens", "?")
        logger.info(f"📋 [{tag}] finish_reason={finish}, tokens=({p_tok}/{c_tok}), content_len={len(response.content or '')}")
        if finish == "length":
            logger.info(
                f"⚠️ [{tag}] *** 模型输出被截断 (finish_reason=length) ***  "
                f"prompt_tokens={p_tok}. 输入可能过长，导致输出空间不足。"
            )

    async def _ainvoke_with_api_fallback(
        self,
        messages: list,
        need_tools: bool,
        tag: str,
        last_exc: Exception,
    ) -> Optional[AIMessage]:
        """Try backup models/APIs from config when primary provider is overloaded."""
        primary_model = getattr(self.llm, "model_name", "")

        for model_key in self._fallback_model_keys:
            try:
                config = get_model_config(model_key)
            except Exception as exc:
                logger.warning(f"⚠️ [{tag}] fallback model config load failed: {model_key}, err={exc}")
                continue

            # Skip unconfigured fallback providers.
            if not config.get("api_key"):
                logger.info(f"ℹ️ [{tag}] fallback skipped (missing api_key): {model_key}")
                continue

            if config.get("model") == primary_model:
                continue

            cache_key = (model_key, need_tools)
            fallback_client = self._fallback_clients.get(cache_key)
            if fallback_client is None:
                base_client = get_qa_llm(model_key, use_cache=True)
                fallback_client = base_client.bind_tools(self.tools) if need_tools and self.tools else base_client
                self._fallback_clients[cache_key] = fallback_client

            try:
                logger.warning(
                    f"🔁 [{tag}] primary overloaded ({last_exc}); switching to fallback model_key={model_key}, "
                    f"model={config.get('model')}"
                )
                response = await fallback_client.ainvoke(messages)
                self._log_response_metadata(response, f"{tag}-fallback-{model_key}")
                if isinstance(response, AIMessage):
                    notice = "\n\n---\n⚠️ 当前主模型繁忙，系统已自动切换备用模型继续完成回答。"
                    response.content = f"{response.content or ''}{notice}"
                return response
            except Exception as fallback_exc:
                logger.error(
                    f"❌ [{tag}] fallback failed: model_key={model_key}, "
                    f"model={config.get('model')}, err={fallback_exc}"
                )

        return None

    @staticmethod
    def _is_rate_limit_or_overload_error(exc: Exception) -> bool:
        """Detect 429/rate-limit/engine-overloaded errors across SDK variants."""
        text = str(exc).lower()
        name = exc.__class__.__name__.lower()

        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True

        response = getattr(exc, "response", None)
        if response is not None:
            try:
                if getattr(response, "status_code", None) == 429:
                    return True
            except Exception:
                pass

        return (
            "ratelimiterror" in name
            or "too many requests" in text
            or "error code: 429" in text
            or "engine_overloaded" in text
            or "engine is currently overloaded" in text
        )

    @staticmethod
    def _is_transient_stream_error(exc: Exception) -> bool:
        text = str(exc).lower()
        name = exc.__class__.__name__.lower()
        return (
            "remoteprotocolerror" in name
            or "apiconnectionerror" in name
            or "connecterror" in name
            or "readtimeout" in name
            or "connectionerror" in name
            or "incomplete chunked read" in text
            or "peer closed connection" in text
            or "server disconnected" in text
            or "connection error" in text
            or "connect error" in text
            or "temporary failure" in text
            or "temporarily unavailable" in text
            or "connection reset" in text
            or ("ssl" in text and "eof" in text)
        )

    async def _astream_evaluator_with_retry(self, messages: list, tag: str = "evaluator", config=None) -> str:
        """
        Stream evaluator LLM with early JSON termination.
        
        Benefits over ainvoke:
        1. Early termination: stops as soon as complete JSON is detected,
           saving ~30-50% of generation time for the evaluator.
        2. Streaming events propagate to astream_events, enabling
           real-time evaluator status in the frontend.
        
        Note: openai SDK already handles retries (max_retries=3),
        so we only catch the final failure gracefully.
        """
        try:
            full_content = ""
            async for chunk in self.evaluator_llm.astream(messages, config=config):
                token = getattr(chunk, 'content', '') or ''
                full_content += token
                # ★ Early termination: stop as soon as we get parseable JSON
                # This avoids waiting for the model to finish generating padding/whitespace
                if '}' in full_content and len(full_content) > 20:
                    decision = self._parse_evaluator_decision(full_content)
                    if decision and 'pass' in decision:
                        logger.info(f"⚡ [Evaluator] Early termination: parsed JSON at {len(full_content)} chars")
                        return full_content
            return full_content
        except Exception as exc:
            if not self._is_transient_stream_error(exc):
                raise
            logger.error(f"❌ [{tag}] upstream transport error (SDK retries exhausted): {exc}")
            return ""

    @staticmethod
    @staticmethod
    def _detect_unfulfilled_tool_promise(
        answer_text: str,
        has_generated_chart: bool,
        has_generated_flowchart: bool,
        has_performed_retrieval: bool,
    ) -> dict | None:
        """Rule-based check: did the agent promise a visualization but not call the tool?

        Returns dict with {mention, tool} if an unfulfilled promise is detected, else None.
        """
        if not answer_text:
            return None
        t = answer_text.lower()

        flowchart_keywords = ["流程图", "思维导图", "学习路径", "mermaid", "路线图"]
        chart_keywords = ["图表", "柱状图", "折线图", "饼图", "数据可视化", "统计图"]

        if not has_generated_flowchart:
            for kw in flowchart_keywords:
                if kw in t:
                    return {"mention": kw, "tool": "generate_learning_path"}

        if not has_generated_chart:
            for kw in chart_keywords:
                if kw in t:
                    return {"mention": kw, "tool": "generate_data_plot"}

        return None

    @staticmethod
    def _should_short_circuit_evaluator(
        answer_text: str,
        has_performed_retrieval: bool,
        retrieval_success: bool,
    ) -> bool:
        """
        Heuristic-based short-circuit: skip the evaluator LLM call entirely
        when the answer is obviously sufficient. Saves one full LLM API call
        (~0.5-2s latency + token cost).
        
        Conditions for short-circuit (all must be met):
        - Retrieval was performed and succeeded
        - Answer is long enough to indicate thorough coverage
        - Answer has structural richness (headers, bullets, paragraphs)
        """
        if not answer_text or not has_performed_retrieval or not retrieval_success:
            return False
        
        answer = answer_text.strip()
        answer_len = len(answer)
        
        # Very long answer with successful retrieval = almost certainly complete
        if answer_len >= 1200:
            return True
        
        # Check structural richness indicators
        # Strip code blocks first to avoid counting # comments or ** operators as structure
        stripped = re.sub(r'```[\s\S]*?```', '', answer)
        has_headers = len(re.findall(r'(?:^|\n)#{1,3}\s', stripped)) >= 2 or stripped.count('**') >= 4
        has_bullets = answer.count('- ') >= 3 or answer.count('• ') >= 2
        has_paragraphs = answer.count('\n\n') >= 2
        structural_score = sum([has_headers, has_bullets, has_paragraphs])
        
        # Well-structured + moderately long = sufficient
        if answer_len >= 800 and structural_score >= 2:
            return True
        if answer_len >= 500 and structural_score >= 1:
            return True
        
        return False

    @staticmethod
    def _route_after_evaluator(state: AgentState) -> Literal["agent", "__end__"]:
        next_step = state.get("evaluator_next", "__end__")
        return "agent" if next_step == "agent" else "__end__"

    def _should_continue(self, state: AgentState) -> Literal["tools", "__end__"]:
        """Backward-compatible helper (kept for external references)."""
        next_step = self._route_after_agent(state)
        return "tools" if next_step == "tools" else "__end__"

    # ==================== Message Filtering ====================

    @staticmethod
    def _filter_messages(messages: list) -> list:
        """
        Filter message history to reduce context on subsequent iterations.
        """
        # Find the last tool round
        last_tool_msgs = []
        last_tool_ai_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], ToolMessage):
                last_tool_msgs.insert(0, messages[i])
            elif isinstance(messages[i], AIMessage) and messages[i].tool_calls and last_tool_msgs:
                last_tool_ai_idx = i
                break
            else:
                break

        last_answer_msg = None
        filtered = []
        for i, msg in enumerate(messages):
            if isinstance(msg, (SystemMessage, HumanMessage)):
                filtered.append(msg)
            elif isinstance(msg, AIMessage):
                if i == last_tool_ai_idx:
                    filtered.append(AIMessage(
                        content="",
                        additional_kwargs=msg.additional_kwargs,
                        tool_calls=msg.tool_calls,
                        id=msg.id,
                    ))
                elif not msg.tool_calls and msg.content and msg.content.strip():
                    last_answer_msg = msg

        # Reconstruct: System/Human -> AI(tools) -> Tool -> AI(answer)
        # Note: In _filter_messages logic, we appended AI(tools) in the loop if found.
        # But ToolMessages were not appended in the loop. 
        # We append them now.
        if last_tool_msgs:
            # If AI(tools) was appended, it's already in 'filtered'.
            # We just need to ensure ToolMessages follow it immediately? 
            # The previous logic had filtered.extend(last_tool_msgs) at the end.
            filtered.extend(last_tool_msgs)
        
        if last_answer_msg:
            filtered.append(last_answer_msg)
            
        return filtered

    @staticmethod
    def _compress_agent_history(messages: list) -> list:
        """
        Compress history for Agent Mode (order-preserving).
        
        Strategy: Keep message order intact. For old tool rounds (all except
        the last 1), replace ToolMessage content with a structured digest:
        - What query was used
        - How many passages were found
        - One-line summary of each passage (first sentence only)
        
        This lets the agent remember WHAT it searched and WHAT it found,
        without carrying the full payload.
        
        Additionally: truncate intermediate AI answers (non-tool-call AI messages
        that aren't the final answer) to save context space.
        """
        if len(messages) <= 4:
            return messages

        # 1. Identify tool round boundaries (by index)
        #    A "round" = one AIMessage(tool_calls) + subsequent ToolMessages
        tool_rounds: list[list[int]] = []  # each entry is list of indices
        i = 0
        while i < len(messages):
            msg = messages[i]
            if isinstance(msg, AIMessage) and getattr(msg, 'tool_calls', None):
                round_indices = [i]
                j = i + 1
                while j < len(messages) and isinstance(messages[j], ToolMessage):
                    round_indices.append(j)
                    j += 1
                if len(round_indices) > 1:  # has at least one ToolMessage
                    tool_rounds.append(round_indices)
                i = j
            else:
                i += 1

        if len(tool_rounds) <= 1:
            return messages  # nothing to compress

        # 2. Mark which indices need compression (all rounds except last 1)
        compress_indices: dict[int, str] = {}  # index -> compressed content
        for round_indices in tool_rounds[:-1]:
            for idx in round_indices:
                msg = messages[idx]
                if isinstance(msg, ToolMessage):
                    compress_indices[idx] = RAGAgent._digest_tool_message(msg)

        # 3. Also truncate intermediate AI answer messages (non-tool-call, non-final)
        #    These are the agent's earlier attempts that got evaluated as FAIL.
        last_ai_idx = -1
        for i in range(len(messages) - 1, -1, -1):
            if isinstance(messages[i], AIMessage) and not getattr(messages[i], 'tool_calls', None):
                last_ai_idx = i
                break

        # 4. Rebuild messages list preserving order
        compressed = []
        for i, msg in enumerate(messages):
            if i in compress_indices:
                compressed.append(ToolMessage(
                    content=compress_indices[i],
                    name=msg.name,
                    tool_call_id=msg.tool_call_id,
                    id=msg.id,
                ))
            elif (isinstance(msg, AIMessage) 
                  and not getattr(msg, 'tool_calls', None)
                  and msg.content 
                  and i != last_ai_idx
                  and len(str(msg.content)) > 300):
                # Truncate earlier AI answers (evaluator rejected these)
                truncated = str(msg.content)[:200] + "\n[... 早期回答已折叠 ...]"
                compressed.append(AIMessage(
                    content=truncated,
                    additional_kwargs=msg.additional_kwargs,
                    id=msg.id,
                ))
            else:
                compressed.append(msg)

        return compressed

    @staticmethod
    def _digest_tool_message(msg: ToolMessage) -> str:
        """
        Create a short structured digest of a ToolMessage.
        Preserves key facts (query, result count, score) while
        reducing a ~3000 char payload to ~200 chars.
        """
        content = str(msg.content)
        
        # If already short, keep as-is
        if len(content) <= 400:
            return content
        
        # For non-search tool messages, simple truncation
        if msg.name != "search_document":
            return content[:300] + f"\n[... 已折叠 {len(content)-300} 字符 ...]" 

        # For search_document: extract structured info
        lines = content.split('\n')
        digest_parts = []
        
        # Detect success/failure header
        if "Retrieval Successful" in content:
            # Extract passage count
            count_match = re.search(r'Found (\d+) relevant passages', content)
            passage_count = count_match.group(1) if count_match else '?'
            digest_parts.append(f"✅ 早期检索成功，找到 {passage_count} 条相关片段。")
            
            # Extract each [Passage N] header + first sentence (score + source)
            passages = re.findall(
                r'\[Passage \d+\]\s*\(([^)]+)\)\n(.+?)(?=\n\n|\[Passage|$)',
                content, re.DOTALL
            )
            for meta, text in passages[:4]:
                first_sentence = text.strip().split('。')[0].split('.')[0][:80]
                digest_parts.append(f"  - ({meta}) {first_sentence}...")
        elif "No relevant information" in content:
            digest_parts.append("❌ 早期检索未找到相关信息。")
        elif "SEARCH LIMIT" in content:
            digest_parts.append("🛑 早期检索已达上限。")
        elif "similar to a previous search" in content:
            digest_parts.append("⚡ 早期检索被去重跳过。")
        else:
            # Unknown format, fallback to truncation
            digest_parts.append(content[:200] + "...")
        
        return "\n".join(digest_parts)

    @staticmethod
    def _sanitize_messages(messages: list) -> list:
        """
        Ensure message history is valid for OpenAI API.
        Rules:
        1. AIMessage with tool_calls MUST be followed by ToolMessage(s).
        2. ToolMessage MUST be preceded by AIMessage with corresponding tool_calls.
        """
        sanitized = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            
            if isinstance(msg, AIMessage) and msg.tool_calls:
                # Look ahead for ToolMessages
                tool_msgs = []
                j = i + 1
                while j < len(messages) and isinstance(messages[j], ToolMessage):
                    tool_msgs.append(messages[j])
                    j += 1
                
                expected_ids = {c.get("id") for c in msg.tool_calls if c.get("id")}
                answered_ids = {getattr(tm, "tool_call_id", None) for tm in tool_msgs if getattr(tm, "tool_call_id", None)}
                
                # If no tool messages followed at all
                if not answered_ids:
                    logger.warning(f"⚠️ [Sanitize] Dropped orphaned AIMessage(tool_calls) id={getattr(msg, 'id', None)}")
                    if msg.content:
                        sanitized.append(AIMessage(
                            content=msg.content,
                            additional_kwargs=getattr(msg, "additional_kwargs", {}),
                            id=getattr(msg, "id", None),
                        ))
                    i = j
                    continue

                # Ensure exact 1:1 match for OpenAI API requirements
                valid_tool_msgs = [tm for tm in tool_msgs if getattr(tm, "tool_call_id", None) in expected_ids]
                for call in msg.tool_calls:
                    call_id = call.get("id")
                    if call_id and call_id not in answered_ids:
                        logger.warning(f"⚠️ [Sanitize] Synthesizing missing ToolMessage for call_id={call_id}")
                        valid_tool_msgs.append(ToolMessage(
                            tool_call_id=call_id,
                            content="⚠️ 工具执行未返回结果",
                            name=call.get("name", "tool"),
                        ))

                sanitized.append(msg)
                sanitized.extend(valid_tool_msgs)
                i = j
                continue

            elif isinstance(msg, ToolMessage):
                # Orphaned ToolMessage (preceding AI message was lost/filtered)
                # We can't keep it because we don't know the tool_call_id context
                logger.warning(f"⚠️ [Sanitize] Dropped orphaned ToolMessage id={msg.id}")
                i += 1
                continue
                
            else:
                sanitized.append(msg)
                i += 1
        
        return sanitized

    def _parse_leaked_tool_call(self, text: str, user_question: str = "") -> dict:
        """Parse leaked tool call from raw text if possible."""
        try:
            text = text.lstrip()
            valid_names = {t.name for t in self.tools}
            
            for name in valid_names:
                # Look for name followed by ( or {
                pattern = rf'({name})(?:[:\s\d]*)([\{{\(])'
                match = re.search(pattern, text)
                if match:
                    start_char = match.group(2)
                    end_char = '}' if start_char == '{' else ')'
                    
                    # Find the matching closing character
                    start_idx = match.end(2) - 1
                    open_count = 0
                    end_idx = -1
                    
                    for i in range(start_idx, len(text)):
                        if text[i] == start_char:
                            open_count += 1
                        elif text[i] == end_char:
                            open_count -= 1
                            if open_count == 0:
                                end_idx = i
                                break
                                
                    if end_idx == -1:
                        continue
                        
                    args_str = text[start_idx:end_idx+1]
                    
                    # 2. Parse arguments (JSON)
                    args = {}
                    if args_str.startswith("{"):
                        try:
                            args = json.loads(args_str)
                        except json.JSONDecodeError:
                            logger.warning(f"⚠️ [Agent] Failed to parse JSON args: {args_str[:50]}...")
                            continue
                    elif args_str.startswith("("):
                        try:
                            import ast
                            # Try to parse as literal tuple/string
                            try:
                                parsed_args = ast.literal_eval(args_str)
                                if name == "search_document":
                                    if isinstance(parsed_args, tuple):
                                        args["query"] = parsed_args[0] if len(parsed_args) > 0 else ""
                                        if len(parsed_args) > 1:
                                            args["keywords"] = parsed_args[1]
                                    elif isinstance(parsed_args, str):
                                        args["query"] = parsed_args
                            except (SyntaxError, ValueError):
                                # Fallback for kwargs like (query="...", keywords=[...])
                                if name == "search_document":
                                    q_match = re.search(r'query\s*=\s*(["\'])(.*?)\1', args_str)
                                    if q_match:
                                        args["query"] = q_match.group(2)
                                    else:
                                        # Just extract the first string literal
                                        s_match = re.search(r'(["\'])(.*?)\1', args_str)
                                        if s_match:
                                            args["query"] = s_match.group(2)
                                            
                                    k_match = re.search(r'keywords\s*=\s*(\[.*?\])', args_str)
                                    if k_match:
                                        try:
                                            args["keywords"] = ast.literal_eval(k_match.group(1))
                                        except:
                                            pass
                        except Exception as e:
                            logger.warning(f"⚠️ [Agent] Failed to parse Python args: {args_str[:50]}... Error: {e}")
                            continue
                    
                    return {
                        "name": name, 
                        "args": args, 
                        "id": f"call_{uuid.uuid4().hex[:8]}"
                    }

            # ★ Fallback 2: bare tool name without argument block
            # e.g. "search_document:0", "search_document", "search_document: 0"
            escaped_names = "|".join(re.escape(n) for n in valid_names)
            bare_match = re.match(rf'^\s*({escaped_names})\s*(?::\s*\d+)?\s*$', text)
            if bare_match:
                name = bare_match.group(1)
                args = {}
                if name == "search_document" and user_question:
                    args = {"query": user_question}
                logger.warning(f"⚠️ [Agent] Recovered bare tool call: {name}")
                return {
                    "name": name,
                    "args": args,
                    "id": f"call_{uuid.uuid4().hex[:8]}"
                }

            return None
            
        except Exception as e:
            logger.warning(f"⚠️ [Agent] Fallback parsing failed: {e}")
            return None

    # ==================== Public API ====================

    async def ainvoke(self, inputs: dict) -> dict:
        """
        Non-streaming invocation.
        Returns the final state dict with 'messages'.
        """
        initial_state = {
            "messages": inputs.get("messages", []),
            "iteration_count": 0,
            "max_iterations": inputs.get("max_iterations", self.max_iterations),
            "evaluator_count": 0,
            "evaluator_next": "__end__",
            "evaluator_visible_status": "",
            "evaluator_visible_reason": "",
        }

        config = {"recursion_limit": 50}
        final_state = await self.graph.ainvoke(initial_state, config=config)

        return {
            "messages": final_state["messages"],
            "iteration_count": final_state.get("iteration_count", 0),
        }
