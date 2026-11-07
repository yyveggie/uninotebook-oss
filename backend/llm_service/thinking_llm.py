from __future__ import annotations

"""
ThinkingChatOpenAI — ChatOpenAI 的子类，支持 reasoning_content 字段。

解决的问题：langchain-openai 0.1.x 的 ChatOpenAI 在流式解析时
会丢弃 Kimi/DeepSeek 思考模型的 reasoning_content 字段，导致
前端无法展示思考过程。

方案：重写 _astream 方法，使用原生 openai.AsyncClient 进行流式调用，
将 reasoning_content 注入到 AIMessageChunk.additional_kwargs 中，
从而与现有 StreamHandler 的检测逻辑 (chunk.additional_kwargs.get("reasoning_content"))
无缝衔接。

注意：不要在 _astream 中调用 run_manager.on_llm_new_token()，
因为 BaseChatModel.astream() 已经会为每个 yield 的 chunk 调用它，
调用两次会导致字符重复。
"""

from typing import Any, AsyncIterator, Iterator, List, Optional

import openai
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI


class ThinkingChatOpenAI(ChatOpenAI):
    """
    支持 reasoning_content (思考内容) 的 ChatOpenAI 子类。

    用法与 ChatOpenAI 完全相同，但在流式输出时会额外提取
    reasoning_content 字段并注入 additional_kwargs。
    
    优化：复用 openai.AsyncClient 实例，避免每次 _astream 都新建
    连接（省去 TLS 握手 ~200-500ms）。
    """

    # 持久化 openai 客户端（惰性创建，按实例复用）
    _openai_client: Optional[openai.AsyncClient] = None

    def _get_openai_client(self) -> openai.AsyncClient:
        """
        惰性获取 openai.AsyncClient 实例。
        同一个 ThinkingChatOpenAI 实例共享一个连接池，
        后续调用复用已建立的 TCP/TLS 连接。
        """
        if self._openai_client is None:
            self._openai_client = openai.AsyncClient(
                api_key=(
                    self.openai_api_key.get_secret_value()
                    if hasattr(self.openai_api_key, 'get_secret_value')
                    else str(self.openai_api_key)
                ),
                base_url=str(self.openai_api_base) if self.openai_api_base else None,
                timeout=self.request_timeout or 300,
                max_retries=3,
            )
        return self._openai_client

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        """
        重写异步流式方法，直接使用 openai.AsyncClient 以保留 reasoning_content。

        重要：不调用 run_manager.on_llm_new_token()，
        因为 BaseChatModel.astream() 会为每个 yield 的 chunk 自动调用，
        避免双重触发导致字符重复。
        """
        # 构建 openai 格式的消息
        openai_messages = self._convert_messages(messages)

        # 构建请求参数
        request_kwargs = {
            "model": self.model_name,
            "messages": openai_messages,
            "temperature": self.temperature,
            "stream": True,
        }
        if self.max_tokens:
            request_kwargs["max_tokens"] = self.max_tokens

        model_kwargs = getattr(self, "model_kwargs", None) or {}
        # extra_body 以显式参数传入（见 llm_config._apply_model_kwargs）；兼容仍放在 model_kwargs 里的旧写法
        extra_body = getattr(self, "extra_body", None) or model_kwargs.get("extra_body")
        if extra_body:
            request_kwargs["extra_body"] = extra_body
        for key, value in model_kwargs.items():
            if key != "extra_body":
                request_kwargs[key] = value

        # 处理工具绑定 (bind_tools 之后 kwargs 中会有 tools)
        if "tools" in kwargs:
            request_kwargs["tools"] = kwargs["tools"]
        if "tool_choice" in kwargs:
            request_kwargs["tool_choice"] = kwargs["tool_choice"]

        # 复用持久化 client（连接池 + TLS 会话复用）
        client = self._get_openai_client()

        stream = await client.chat.completions.create(**request_kwargs)

        async for chunk in stream:
            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            reasoning = self._get_extra_field(delta, "reasoning_content")
            content = delta.content or ""

            # 跳过完全空的 chunk
            if not reasoning and not content and not delta.tool_calls:
                continue

            # 提取 tool_calls
            tool_call_chunks = []
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    tool_call_chunks.append({
                        "index": tc.index,
                        "id": tc.id,
                        "name": tc.function.name if tc.function else None,
                        "args": tc.function.arguments if tc.function else "",
                    })

            # 构建 additional_kwargs
            additional_kwargs = {}
            if reasoning:
                additional_kwargs["reasoning_content"] = reasoning

            # 构建 AIMessageChunk
            chunk_msg = AIMessageChunk(
                content=content,
                additional_kwargs=additional_kwargs,
                tool_call_chunks=tool_call_chunks if tool_call_chunks else [],
            )

            # 封装为 ChatGenerationChunk 并 yield
            # 注意：不调用 run_manager.on_llm_new_token()
            # BaseChatModel.astream() 会自动为每个 yield 的 chunk 调用
            yield ChatGenerationChunk(message=chunk_msg)

    @staticmethod
    def _get_extra_field(obj: Any, field: str):
        if hasattr(obj, field):
            return getattr(obj, field)
        model_extra = getattr(obj, "model_extra", None)
        if isinstance(model_extra, dict) and field in model_extra:
            return model_extra.get(field)
        data = getattr(obj, "__dict__", {})
        if isinstance(data, dict):
            return data.get(field)
        return None

    @staticmethod
    def _convert_messages(messages) -> list:
        """
        将 LangChain 消息转换为 OpenAI 格式。
        
        特别处理：保留 AIMessage 的 reasoning_content（存储在 additional_kwargs 中），
        这是 Kimi 官方要求的多步工具调用必要条件。
        """
        import json
        from langchain_core.messages import (
            AIMessage,
            HumanMessage,
            SystemMessage,
            ToolMessage,
        )

        openai_messages = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                openai_messages.append({"role": "system", "content": msg.content})
            elif isinstance(msg, HumanMessage):
                openai_messages.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                entry = {"role": "assistant", "content": msg.content or ""}
                # 🆕 保留 reasoning_content（Kimi 官方要求）
                reasoning = msg.additional_kwargs.get("reasoning_content", "")
                if msg.tool_calls:
                    # Kimi thinking + tool calls 要求 assistant 工具调用消息必须带
                    # reasoning_content。正常情况下上游流式 chunk 会保留该字段；
                    # 这里兜底补齐，避免历史/聚合消息缺字段导致 400。
                    entry["reasoning_content"] = reasoning or "工具调用前的思考内容未在历史消息中保留。"
                    entry["tool_calls"] = [
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("name", ""),
                                "arguments": (
                                    tc.get("args", "")
                                    if isinstance(tc.get("args"), str)
                                    else json.dumps(tc.get("args", {}))
                                ),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                elif reasoning:
                    entry["reasoning_content"] = reasoning
                openai_messages.append(entry)
            elif isinstance(msg, ToolMessage):
                openai_messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id,
                    "content": msg.content,
                })
            else:
                # Fallback
                openai_messages.append({"role": "user", "content": str(msg.content)})

        return openai_messages
