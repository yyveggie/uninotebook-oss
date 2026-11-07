"""
Token 估算与消息裁剪工具

提供基于字符数的快速 Token 估算和消息列表裁剪功能。
使用字符估算而非 tiktoken，因为项目使用多种模型（qwen/kimi/deepseek）。
"""

import re
from typing import List
from utils.logger import get_logger
from langchain_core.messages import (
    BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
)

logger = get_logger(__name__)

_CJK_PATTERN = re.compile(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]')

# ==================== Token 估算 ====================

def estimate_tokens(text: str) -> int:
    """
    基于字符数的 Token 估算（区分中英文比例）。
    
    经验法则:
    - 中文: 1 token ≈ 1.5 字符  (比例 0.667)
    - 英文/代码: 1 token ≈ 4 字符  (比例 0.25)
    - 混合: 加权平均
    """
    if not text:
        return 0
    total = len(text)
    cjk_count = len(_CJK_PATTERN.findall(text))
    non_cjk = total - cjk_count
    # 中文按 1.5 字符/token，英文按 4 字符/token
    tokens = (cjk_count / 1.5) + (non_cjk / 4) if total > 0 else 0
    return max(1, int(tokens))


def estimate_message_tokens(msg: BaseMessage) -> int:
    """估算单条消息的 Token 数（含开销）"""
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    base = estimate_tokens(content)
    # tool_calls 结构也消耗 token
    if hasattr(msg, 'tool_calls') and msg.tool_calls:
        for tc in msg.tool_calls:
            base += estimate_tokens(str(tc.get('args', {})))
            base += 20  # function name + structure overhead
    return base + 4  # per-message overhead


# ==================== 消息裁剪 ====================

def budget_messages(
    messages: List[BaseMessage],
    max_tokens: int = 28000
) -> List[BaseMessage]:
    """
    在 Token 预算内裁剪消息列表。
    
    优先级（从高到低）：
    1. SystemMessage（首条） — 永远保留（但可截断内容）
    2. 最后一条 HumanMessage — 永远保留
    3. 最新的 ToolMessage 组 + 对应 AIMessage — 保留
    4. 其他消息 — 按删除优先级逐条删除：
       a. 评估器/系统提醒类 SystemMessage（中途注入的反思/警告）
       b. 旧的 ToolMessage（已被压缩过的价值更低）
       c. 旧的 AI/Human 消息
    
    Args:
        messages: 消息列表
        max_tokens: Token 预算（默认 28000）
    
    Returns:
        裁剪后的消息列表
    """
    if not messages:
        return messages
    
    total = sum(estimate_message_tokens(m) for m in messages)
    if total <= max_tokens:
        return messages
    
    logger.warning(f"⚠️ [TokenBudget] {total} tokens > {max_tokens} budget, truncating...")
    
    # 1. 分类消息
    # 只保留首条 SystemMessage（系统 prompt），后续的 SystemMessage 是评估器反思/警告，可删除
    first_system_idx = -1
    for i, m in enumerate(messages):
        if isinstance(m, SystemMessage):
            first_system_idx = i
            break
    
    # 找最后一条 HumanMessage
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            last_human_idx = i
            break
    
    # 找最新的 ToolMessage 组及其对应的 AIMessage
    latest_tool_indices = set()
    latest_tool_ai_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], ToolMessage):
            latest_tool_indices.add(i)
        elif isinstance(messages[i], AIMessage) and messages[i].tool_calls and latest_tool_indices:
            latest_tool_ai_idx = i
            break
        elif latest_tool_indices:
            break
    
    # 2. 标记必须保留的索引
    keep_indices = set()
    if first_system_idx >= 0:
        keep_indices.add(first_system_idx)
    if last_human_idx >= 0:
        keep_indices.add(last_human_idx)
    keep_indices.update(latest_tool_indices)
    if latest_tool_ai_idx >= 0:
        keep_indices.add(latest_tool_ai_idx)
    
    # 3. 可删除索引按优先级排序：
    #    - 先删评估器注入的 SystemMessage（反思/警告，不影响答案质量）
    #    - 再删旧消息（从旧到新）
    removable_system = []
    removable_other = []
    for i in range(len(messages)):
        if i in keep_indices:
            continue
        if isinstance(messages[i], SystemMessage):
            removable_system.append(i)  # 评估器反思、near-limit 警告等
        else:
            removable_other.append(i)
    
    removable = removable_system + sorted(removable_other)
    
    # 4. 逐条删除直到预算内
    removed = set()
    current_tokens = total
    for idx in removable:
        if current_tokens <= max_tokens:
            break
        current_tokens -= estimate_message_tokens(messages[idx])
        removed.add(idx)
    
    # 5. 如果删完可删的还超，截断 ToolMessage 内容
    if current_tokens > max_tokens:
        for idx in sorted(latest_tool_indices):
            msg = messages[idx]
            if isinstance(msg, ToolMessage) and isinstance(msg.content, str):
                excess = current_tokens - max_tokens
                chars_to_cut = int(excess * 1.5)  # token → char（保守估算）
                if len(msg.content) > chars_to_cut + 100:
                    truncated = msg.content[:len(msg.content) - chars_to_cut]
                    truncated += "\n\n[... 内容因长度限制被截断 ...]"
                    messages[idx] = ToolMessage(
                        content=truncated,
                        name=getattr(msg, 'name', None),
                        tool_call_id=msg.tool_call_id,
                        id=msg.id
                    )
                    current_tokens -= excess
                    break
    
    # 6. 如果还超，截断 SystemMessage
    if current_tokens > max_tokens and first_system_idx >= 0:
        msg = messages[first_system_idx]
        if isinstance(msg.content, str) and len(msg.content) > 2000:
            excess = current_tokens - max_tokens
            chars_to_cut = int(excess * 1.5)
            truncated = msg.content[:len(msg.content) - chars_to_cut]
            messages[first_system_idx] = SystemMessage(content=truncated)
            current_tokens -= excess
    
    result = [m for i, m in enumerate(messages) if i not in removed]
    logger.info(f"   ✅ [TokenBudget] {total} → ~{current_tokens} tokens (removed {len(removed)} messages)")
    return result
