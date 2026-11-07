"""
状态提示模版池

设计：
1. 工具状态 — 多样化随机文案，避免每次都是同一句
2. 带上下文的状态 — 根据检索详情、步骤进度等动态渲染
"""

import random
from typing import Optional, Dict, Any


# ==================== 工具状态模版池 ====================

TOOL_STATUS = {
    "search_start": [
        "正在检索",
        "正在查阅文档",
        "搜索相关段落中",
        "检索文档内容",
    ],
    "search_end": [
        "正在提取关键信息",
        "正在整理检索结果",
        "分析检索内容中",
    ],
    "flowchart_start": [
        "正在生成流程图",
        "绘制逻辑关系图中",
        "构建可视化流程",
    ],
    "chart_start": [
        "正在绘制数据图表",
        "生成数据可视化中",
        "构建图表中",
    ],
    "flowchart_done": [
        "生成：流程图",
    ],
    "chart_done": [
        "生成：数据图表",
    ],
}


def get_tool_status(tool_name: str, phase: str = "start") -> Dict[str, Any]:
    """
    获取工具状态提示
    
    Args:
        tool_name: 工具名 (search_document, generate_learning_path, generate_data_plot)
        phase: "start" 或 "end"
    
    Returns:
        完整的状态对象 {"text": ..., "icon": ..., "phase": ...}
    """
    tool_map = {
        "search_document": {
            "start": ("search_start", "search", "search"),
            "end": ("search_end", "analyze", "analyze"),
        },
        "generate_learning_path": {
            "start": ("flowchart_start", "drawing", "visualize"),
            "end": ("flowchart_done", "check", "visualize"),
        },
        "generate_data_plot": {
            "start": ("chart_start", "drawing", "chart"),
            "end": ("chart_done", "check", "chart"),
        },
    }
    
    config = tool_map.get(tool_name, {}).get(phase)
    if config:
        template_key, icon, status_phase = config
        texts = TOOL_STATUS.get(template_key, ["处理中..."])
        return {"text": random.choice(texts), "icon": icon, "phase": status_phase}
    
    return {"text": f"执行 {tool_name}", "icon": "tools", "phase": "tool"}


def get_search_detail_status(queries: list, keywords: list) -> Dict[str, Any]:
    """
    生成带检索详情的状态
    """
    return {
        "text": "正在检索",
        "icon": "analyze",
        "phase": "analyze",
        "details": {
            "queries": queries,
            "keywords": keywords,
        }
    }
