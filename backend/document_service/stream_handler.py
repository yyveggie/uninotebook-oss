"""
Stream Handler — Converts LangGraph astream_events into SSE messages.

Responsibilities:
- Converting LangGraph events to project-specific SSE format
- Tool output parsing (flowchart, chart, search, plan, clarification)
- SSE message formatting
"""

import json
from typing import Any, Dict, List, Optional, AsyncIterator

from pydantic import BaseModel
from utils.logger import get_logger

from document_service.status_templates import (
    get_tool_status, get_search_detail_status
)
from utils.mermaid_sanitizer import sanitize_mermaid_code
from utils.latex import fix_latex_format

logger = get_logger(__name__)


class StreamHandler:
    """
    Converts LangGraph astream_events into SSE messages.

    Simple design:
    - on_chat_model_stream → yield SSE token events (real-time streaming)
    - on_tool_start/end → yield SSE status/flowchart/chart/plan/clarification events
    
    Usage:
        handler = StreamHandler(retrieval_ctx, "document.pdf")
        async for sse_msg in handler.process_events(event_stream):
            yield sse_msg
    """

    def __init__(self, retrieval_ctx, document_filename: str):
        self.retrieval_ctx = retrieval_ctx
        self.document_filename = document_filename

        # Public state (accessed by qa.py after processing)
        self.full_answer = ""
        self.flowchart_data = None
        self.chart_data = None
        self.execution_logs: List[Dict] = []
        self.is_clarification = False

        # Search counter for unique status messages
        self._search_count = 0

        # Internal
        self._content_started = False
        self._in_content = False
        self._buffer = ""
        self._suppress_output = False
        self._evaluator_running = False
        self._evaluator_tokens_started = False  # Track evaluator LLM streaming liveness
        self._truncated = False  # 标记模型输出是否被截断 (finish_reason=length)

    # ==================== Public API ====================

    @staticmethod
    def sse(event_type: str, data: Any) -> str:
        """Generate an SSE-formatted string."""
        # 一律用 json.dumps：手工转义会漏掉制表符等控制字符，生成前端无法解析的帧
        payload = {"event": event_type, "data": data}
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def process_events(self, event_stream) -> AsyncIterator[str]:
        """Process astream_events and yield SSE messages."""
        async for event in event_stream:
            async for msg in self._dispatch(event):
                yield msg

    # ==================== Event Dispatch ====================

    async def _dispatch(self, event):
        kind = event["event"]

        if kind == "on_chat_model_start":
            self._on_model_start(event)
        elif kind == "on_chat_model_stream":
            async for msg in self._on_model_stream(event):
                yield msg
        elif kind == "on_chat_model_end":
            async for msg in self._on_model_end(event):
                yield msg
        elif kind == "on_tool_start":
            async for msg in self._on_tool_start(event):
                yield msg
        elif kind == "on_tool_end":
            async for msg in self._on_tool_end(event):
                yield msg
        elif kind in ("on_chain_start", "on_chain_end"):
            async for msg in self._on_chain_event(event):
                yield msg

    # ==================== LLM Streaming ====================

    def _on_model_start(self, event):
        """Reset streaming state at the start of each agent LLM call.

        Prevents stale _content_started=True from a previous iteration
        (e.g. after evaluator FAIL → agent retry) from bypassing the
        buffer-based leak detection on the new iteration's output.
        """
        metadata = event.get("metadata", {})
        if metadata.get("langgraph_node") == "agent":
            self._content_started = False
            self._buffer = ""
            self._suppress_output = False

    async def _on_model_stream(self, event):
        """Stream LLM tokens directly to frontend as SSE events."""
        metadata = event.get("metadata", {})
        node = metadata.get("langgraph_node")

        # ★ Evaluator streaming: show liveness indicator when evaluator LLM starts generating
        if node == "evaluator":
            if not self._evaluator_tokens_started:
                self._evaluator_tokens_started = True
                log_item = {
                    "text": "评估推理中...",
                    "icon": "evaluate",
                    "phase": "evaluate",
                }
                self._append_log(log_item)
                yield self.sse("status", log_item)
            return

        if node != "agent":
            return

        chunk = event["data"]["chunk"]

        # 🧠 Kimi Thinking Model Support
        # Check if chunk contains reasoning_content (LangChain often hides it in extra fields)
        reasoning = None
        if hasattr(chunk, "reasoning_content") and chunk.reasoning_content:
            reasoning = chunk.reasoning_content
        elif isinstance(chunk.content, str) and chunk.content.startswith("Reasoning:"): 
            # Fallback (unlikely)
            pass
        elif chunk.additional_kwargs.get("reasoning_content"):
            reasoning = chunk.additional_kwargs.get("reasoning_content")
        
        if not reasoning and not chunk.content:
             # Empty chunk or unknown structure?
             pass
        
        if reasoning:
            # Emit as status event with special type
            yield self.sse("status", {"text": reasoning, "type": "thinking_stream"})
            # Reasoning chunks usually don't have content, but if they do, let it flow?
            # Usually strict separation.
            if not chunk.content:
                return

        # Tool call chunks have no user-facing content — skip.
        # Any buffered text preceding a tool call is a preface ("让我搜索一下")
        # and should always be discarded, regardless of pattern matching.
        if chunk.tool_call_chunks:
            if not self._content_started and self._buffer:
                logger.info(f"🧽 [Stream] Discarded pre-tool buffer ({len(self._buffer)} chars)")
                self._buffer = ""
            return

        content = chunk.content or ""
        if not content:
            return

        # 🛡️ Output Safety Check: Suppress leaked tool calls (raw text format)
        if self._suppress_output:
            # 如果后续内容不再像工具调用，自动恢复输出，避免“快速模式无回答”
            candidate = content.lstrip()
            if self._is_leaked_tool_call(candidate):
                return
            self._suppress_output = False
            # 作为首段正文重新走缓冲检测，避免把半截工具调用拼进正文
            self._content_started = False
            self._buffer = ""

        if not self._content_started:
            self._buffer += content
            # Wait for enough context to detect leaked tool patterns.
            # 35 chars ≈ 15-20 tokens, enough to capture "search_document({..." etc.
            if len(self._buffer) < 35:
                return

            if self._is_leaked_tool_call(self._buffer):
                self._suppress_output = True
                self._buffer = ""
                return

            # Hold buffer longer if it looks like a pre-tool preface;
            # it will be discarded when tool_call_chunks arrive.
            if self._should_absorb_tool_preface(self._buffer) and len(self._buffer) < 200:
                return
            
            # Safe content, flush buffer
            content = self._buffer
            self._buffer = ""
            self._mark_content()
        
        # Stream content
        if content:
            content = fix_latex_format(content)
            self.full_answer += content
            yield self.sse("token", content)

    async def _on_model_end(self, event):
        """Flush buffered content at model end if still pending."""
        metadata = event.get("metadata", {})
        if metadata.get("langgraph_node") != "agent":
            return

        # 🆕 诊断日志：记录模型的停止原因和 token 用量
        try:
            output = (event.get("data") or {}).get("output")
            if output and hasattr(output, "response_metadata"):
                meta = output.response_metadata or {}
                finish = meta.get("finish_reason") or meta.get("stop_reason") or "unknown"
                usage = meta.get("token_usage") or meta.get("usage") or {}
                prompt_tok = usage.get("prompt_tokens", "?")
                completion_tok = usage.get("completion_tokens", "?")
                logger.info(
                    f"📋 [Stream] Model end: finish_reason={finish}, "
                    f"tokens=({prompt_tok}/{completion_tok}), "
                    f"answer_len={len(self.full_answer)}"
                )
                # ⚠️ 关键检测：finish_reason=length 意味着输出被 max_tokens 或上下文窗口截断
                if finish == "length":
                    logger.info(
                        f"⚠️ [Stream] *** 回答被截断 (finish_reason=length) ***  "
                        f"prompt_tokens={prompt_tok}, completion_tokens={completion_tok}. "
                        f"可能原因：输入过长导致输出空间不足，或 max_tokens 过低。"
                    )
                    self._truncated = True
        except Exception as diag_err:
            logger.warning(f"⚠️ [Stream] Diagnostic logging failed: {diag_err}")

        if self._buffer and not self._suppress_output:
            # Final safety check: don't flush if buffer is a leaked tool call
            if self._is_leaked_tool_call(self._buffer):
                logger.info(f"🧽 [Stream] Suppressed leaked tool call in buffer flush: {self._buffer.strip()[:50]}")
                self._buffer = ""
                return
            content = fix_latex_format(self._buffer)
            self._buffer = ""
            self._mark_content()
            self.full_answer += content
            yield self.sse("token", content)

    async def _on_chain_event(self, event):
        """Handle evaluator node visibility events."""
        metadata = event.get("metadata", {})
        if metadata.get("langgraph_node") != "evaluator":
            return

        kind = event.get("event")
        if kind == "on_chain_start":
            if self._evaluator_running:
                return
            self._evaluator_running = True
            self._evaluator_tokens_started = False  # Reset for new evaluator round
            log_item = {
                "text": "正在评估回答完整性...",
                "icon": "evaluate",
                "phase": "evaluate",
            }
            self._append_log(log_item)
            yield self.sse("status", log_item)
            return

        if kind == "on_chain_end":
            self._evaluator_running = False
            output = (event.get("data") or {}).get("output") or {}
            if not isinstance(output, dict):
                return

            status = output.get("evaluator_visible_status", "")
            reason = str(output.get("evaluator_visible_reason", "") or "").strip()

            if status == "pass":
                log_item = {
                    "text": "评估通过，正在收束答案",
                    "icon": "evaluate",
                    "phase": "evaluate",
                }
            elif status == "fail":
                brief = f"（{reason[:40]}...）" if len(reason) > 40 else (f"（{reason}）" if reason else "")
                log_item = {
                    "text": f"评估未通过，继续补充{brief}",
                    "icon": "evaluate",
                    "phase": "evaluate",
                }
            else:
                return

            self._append_log(log_item)
            yield self.sse("status", log_item)

            # Reset streaming state so the next agent iteration enters
            # the buffer-based leak detection path instead of bypassing it.
            self._content_started = False
            self._buffer = ""
            self._suppress_output = False

    # ==================== Tool Handlers ====================

    async def _on_tool_start(self, event):
        name = event["data"].get("name", "tool")

        if name == "search_document":
            log_item = {
                "text": "我正在查找相关的内容...",
                "icon": "search",
                "phase": "search",
            }
            self._append_log(log_item)
            yield self.sse("status", log_item)
        elif name in ("generate_learning_path", "generate_data_plot"):
            log_item = get_tool_status(name, "start")
            self._append_log(log_item)
            yield self.sse("status", log_item)
        elif name == "create_plan":
            log_item = {"text": "正在制定计划...", "icon": "plan", "phase": "planning"}
            self._append_log(log_item)
            yield self.sse("status", log_item)

    async def _on_tool_end(self, event):
        try:
            name = event["name"]
            output = event["data"].get("output")
            content = self._extract_tool_content(output)

            if name == "generate_learning_path":
                async for msg in self._handle_flowchart(content):
                    yield msg
            elif name == "generate_data_plot":
                async for msg in self._handle_chart(content):
                    yield msg
            elif name == "create_plan":
                async for msg in self._handle_plan(content):
                    yield msg
            elif name == "ask_clarification":
                async for msg in self._handle_clarification(content):
                    yield msg
            else:
                # search_document
                self._search_count += 1
                log_item = {
                    "text": "我正在查找相关的内容...",
                    "icon": "analyze",
                    "phase": "analyze",
                }
                self._append_log(log_item)
                yield self.sse("status", log_item)
        except Exception as e:
            logger.error(f"❌ [StreamHandler] Tool end error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            # 🆕 工具执行完毕，重置输出抑制状态
            # 防止 iter 0 的泄漏检测永久抑制后续轮次的正常输出
            self._suppress_output = False
            self._content_started = False
            self._buffer = ""

    # ==================== Tool Output Parsers ====================

    async def _handle_flowchart(self, content: str):
        try:
            json_data = self._parse_tool_json(content)
            if "mermaid_code" in json_data:
                json_data["mermaid_code"] = sanitize_mermaid_code(json_data["mermaid_code"])

            self.flowchart_data = json_data
            yield self.sse("flowchart", json_data)

            placeholder = "\n\n<<<FLOWCHART_PLACEHOLDER>>>\n\n"
            self.full_answer += placeholder
            yield self.sse("token", placeholder)
        except Exception as e:
            logger.warning(f"⚠️ Error parsing flowchart: {e}")

    async def _handle_chart(self, content: str):
        try:
            json_data = self._parse_tool_json(content)
            self.chart_data = json_data
            yield self.sse("chart", json_data)

            log_item = {"text": "生成：数据图表", "icon": "check", "phase": "chart"}
            self._append_log(log_item)
            yield self.sse("status", log_item)

            placeholder = "\n\n<<<DATA_CHART_PLACEHOLDER>>>\n\n"
            self.full_answer += placeholder
            yield self.sse("token", placeholder)
        except Exception as e:
            logger.warning(f"⚠️ Error parsing chart: {e}")

    async def _handle_plan(self, content: str):
        try:
            json_data = self._parse_tool_json(content)
            steps = json_data.get("steps", [])
            if steps:
                yield self.sse("plan", steps)
                logger.info(f"📋 [Plan] Sent {len(steps)} steps to frontend")
        except Exception as e:
            logger.warning(f"⚠️ Error parsing plan: {e}")

    async def _handle_clarification(self, content: str):
        try:
            json_data = self._parse_tool_json(content)
            self.is_clarification = True
            yield self.sse("clarification", {
                "question": json_data.get("question", ""),
                "type": "choice" if json_data.get("options") else "input",
                "options": json_data.get("options", [])
            })
        except Exception as e:
            logger.warning(f"⚠️ Error parsing clarification: {e}")

    # ==================== Helpers ====================

    @staticmethod
    def _extract_tool_content(output) -> str:
        """Extract string content from various tool output formats."""
        if isinstance(output, BaseModel):
            return output.json()
        if hasattr(output, "content"):
            return output.content
        if isinstance(output, dict) and "content" in output:
            return output["content"]
        if isinstance(output, str):
            return output
        return str(output)

    @staticmethod
    def _parse_tool_json(content: str) -> dict:
        """Parse JSON from tool output, handling nested ToolMessage structures."""
        cleaned = content.replace("```json", "").replace("```", "").strip()
        json_data = json.loads(cleaned)

        # Unwrap nested ToolMessage if present
        if (isinstance(json_data, dict)
            and "content" in json_data
            and json_data.get("type") == "tool"):
            inner = json_data["content"]
            json_data = json.loads(inner) if isinstance(inner, str) else inner

        return json_data

    def _append_log(self, log_item: dict):
        if self._in_content:
            self.execution_logs.append(
                {"type": "content_boundary", "offset": len(self.full_answer)}
            )
            self._in_content = False
        self.execution_logs.append(log_item)

    def _mark_content(self):
        self._content_started = True
        self._in_content = True

    @staticmethod
    def _is_leaked_tool_call(text: str) -> bool:
        """Detect raw tool call content that the model emitted as plain text
        instead of proper tool_call_chunks.

        Covers: ``search_document(...)``, ``{"name":"search_document",...}``,
        ``{"query":"...",...}`` (bare argument JSON), and similar variants.
        """
        _TOOL_NAMES = (
            "search_document",
            "generate_learning_path",
            "generate_data_plot",
            "create_plan",
            "ask_clarification",
        )

        stripped = text.lstrip()

        # 1. Tool name followed by delimiter: search_document: / ( / {
        for p in _TOOL_NAMES:
            if (stripped.startswith(f"{p}:") or
                stripped.startswith(f"{p}(") or
                stripped.startswith(f"{p}{{")):
                return True

        # 2. JSON object that looks like a function-call payload
        #    e.g. {"name": "search_document", ...}
        #    e.g. {"query": "xxx", "keywords": [...]}
        if stripped.startswith("{"):
            lower = stripped[:200].lower()
            json_sigs = (
                '"name":', '"arguments":', '"function":',
                '"query":', '"keywords":',
                '"mermaid_code":', '"chart_type":',
                '"steps":', '"title":',
            )
            if any(sig in lower for sig in json_sigs):
                return True

        return False

    @staticmethod
    def _should_absorb_tool_preface(text: str) -> bool:
        """Heuristic: detect pre-tool narration that should stay in thinking/status area."""
        if not text:
            return False
        t = text.strip().lower()
        if len(t) > 250:
            return False

        markers = [
            "我来", "我将", "让我", "我会先", "接下来", "首先",
            "我需要", "我先", "需要先", "先来", "好的，",
            "开始检索", "继续检索", "先检索", "先搜索", "再总结", "再给出",
            "进一步", "补充", "再检索", "再搜索", "再查",
            "i will", "let me", "first,", "next,", "search",
            "i need", "i'll", "let's",
        ]
        action_words = [
            "检索", "搜索", "查找", "分析", "梳理", "总结",
            "工具", "调用", "查询", "获取", "提取",
        ]

        has_marker = any(m in t for m in markers)
        has_action = any(w in t for w in action_words)
        return has_marker and has_action
