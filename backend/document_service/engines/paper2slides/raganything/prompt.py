"""
多模态内容处理的提示词模板

包含所有用于模态处理器分析不同类型内容（图像、表格、公式等）的提示词模板
"""

from __future__ import annotations
from typing import Any


PROMPTS: dict[str, Any] = {}

# 不同分析类型的系统提示词
PROMPTS["IMAGE_ANALYSIS_SYSTEM"] = (
    "你是一位专业的图像分析专家。请提供详细、准确的描述。"
)
PROMPTS["IMAGE_ANALYSIS_FALLBACK_SYSTEM"] = (
    "你是一位专业的图像分析专家。请基于可用信息提供详细分析。"
)
PROMPTS["TABLE_ANALYSIS_SYSTEM"] = (
    "你是一位专业的数据分析专家。请提供详细的表格分析和具体洞察。"
)
PROMPTS["EQUATION_ANALYSIS_SYSTEM"] = (
    "你是一位数学专家。请提供详细的数学分析。"
)
PROMPTS["GENERIC_ANALYSIS_SYSTEM"] = (
    "你是一位专业的内容分析专家，专门分析 {content_type} 类型的内容。"
)

# 图像分析提示词模板
PROMPTS[
    "vision_prompt"
] = """请详细分析这张图像，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对图像的全面详细视觉描述，请遵循以下指南：
    - 描述整体构图和布局
    - 识别所有对象、人物、文字和视觉元素
    - 解释元素之间的关系
    - 注明颜色、光线和视觉风格
    - 描述展示的任何动作或活动
    - 如相关，包含技术细节（图表、示意图等）
    - 始终使用具体名称而非代词",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "image",
        "summary": "图像内容及其重要性的简洁摘要（最多100字）"
    }}
}}

附加上下文：
- 图像路径：{image_path}
- 标题说明：{captions}
- 脚注：{footnotes}

请专注于提供准确、详细的视觉分析，以便用于知识检索。"""

# 带上下文支持的图像分析提示词
PROMPTS[
    "vision_prompt_with_context"
] = """请详细分析这张图像，并结合周围上下文。以以下 JSON 结构提供响应：

{{
    "detailed_description": "对图像的全面详细视觉描述，请遵循以下指南：
    - 描述整体构图和布局
    - 识别所有对象、人物、文字和视觉元素
    - 解释元素之间的关系以及它们与周围上下文的关联
    - 注明颜色、光线和视觉风格
    - 描述展示的任何动作或活动
    - 如相关，包含技术细节（图表、示意图等）
    - 在相关时引用与周围内容的联系
    - 始终使用具体名称而非代词",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "image",
        "summary": "图像内容、其重要性及与周围内容关系的简洁摘要（最多100字）"
    }}
}}

周围内容的上下文：
{context}

图像详情：
- 图像路径：{image_path}
- 标题说明：{captions}
- 脚注：{footnotes}

请专注于提供结合上下文的准确详细视觉分析，以便用于知识检索。"""

# 基于文本回退的图像分析提示词
PROMPTS["text_prompt"] = """请根据以下图像信息提供分析：

图像路径：{image_path}
标题说明：{captions}
脚注：{footnotes}

{vision_prompt}"""

# 表格分析提示词模板
PROMPTS[
    "table_prompt"
] = """请分析此表格内容，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对表格的综合分析，包括：
    - 表格结构和组织方式
    - 列标题及其含义
    - 关键数据点和模式
    - 统计洞察和趋势
    - 数据元素之间的关系
    - 所呈现数据的重要性
    始终使用具体的名称和数值，避免一般性引用。",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "table",
        "summary": "表格目的和关键发现的简洁摘要（最多100字）"
    }}
}}

表格信息：
图像路径：{table_img_path}
标题：{table_caption}
内容：{table_body}
脚注：{table_footnote}

请专注于从表格数据中提取有意义的洞察和关系。"""

# 带上下文支持的表格分析提示词
PROMPTS[
    "table_prompt_with_context"
] = """请结合周围上下文分析此表格内容，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对表格的综合分析，包括：
    - 表格结构和组织方式
    - 列标题及其含义
    - 关键数据点和模式
    - 统计洞察和趋势
    - 数据元素之间的关系
    - 所呈现数据与周围上下文的重要关联
    - 表格如何支持或说明周围内容中的概念
    始终使用具体的名称和数值，避免一般性引用。",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "table",
        "summary": "表格目的、关键发现及与周围内容关系的简洁摘要（最多100字）"
    }}
}}

周围内容的上下文：
{context}

表格信息：
图像路径：{table_img_path}
标题：{table_caption}
内容：{table_body}
脚注：{table_footnote}

请专注于在周围内容的背景下从表格数据中提取有意义的洞察和关系。"""

# 公式分析提示词模板
PROMPTS[
    "equation_prompt"
] = """请分析此数学公式，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对公式的综合分析，包括：
    - 数学含义和解释
    - 变量及其定义
    - 使用的数学运算和函数
    - 应用领域和上下文
    - 物理或理论意义
    - 与其他数学概念的关系
    - 实际应用或使用场景
    始终使用专业的数学术语。",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "equation",
        "summary": "公式目的和重要性的简洁摘要（最多100字）"
    }}
}}

公式信息：
公式：{equation_text}
格式：{equation_format}

请专注于提供数学洞察并解释公式的重要性。"""

# 带上下文支持的公式分析提示词
PROMPTS[
    "equation_prompt_with_context"
] = """请结合周围上下文分析此数学公式，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对公式的综合分析，包括：
    - 数学含义和解释
    - 变量及其在周围内容中的定义
    - 使用的数学运算和函数
    - 基于周围材料的应用领域和上下文
    - 物理或理论意义
    - 与上下文中提及的其他数学概念的关系
    - 实际应用或使用场景
    - 公式与更广泛讨论或框架的关联
    始终使用专业的数学术语。",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "equation",
        "summary": "公式的目的、重要性及其在周围上下文中作用的简洁摘要（最多100字）"
    }}
}}

周围内容的上下文：
{context}

公式信息：
公式：{equation_text}
格式：{equation_format}

请专注于在更广泛上下文中提供数学洞察并解释公式的重要性。"""

# 通用内容分析提示词模板
PROMPTS[
    "generic_prompt"
] = """请分析此 {content_type} 内容，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对内容的综合分析，包括：
    - 内容结构和组织方式
    - 关键信息和元素
    - 组件之间的关系
    - 上下文和重要性
    - 用于知识检索的相关细节
    始终使用适合 {content_type} 内容的专业术语。",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "{content_type}",
        "summary": "内容目的和要点的简洁摘要（最多100字）"
    }}
}}

内容：{content}

请专注于提取对知识检索有用的有意义信息。"""

# 带上下文支持的通用内容分析提示词
PROMPTS[
    "generic_prompt_with_context"
] = """请结合周围上下文分析此 {content_type} 内容，并以以下 JSON 结构提供响应：

{{
    "detailed_description": "对内容的综合分析，包括：
    - 内容结构和组织方式
    - 关键信息和元素
    - 组件之间的关系
    - 与周围内容相关的上下文和重要性
    - 此内容如何与更广泛的讨论连接或支持
    - 用于知识检索的相关细节
    始终使用适合 {content_type} 内容的专业术语。",
    "entity_info": {{
        "entity_name": "{entity_name}",
        "entity_type": "{content_type}",
        "summary": "内容目的、要点及与周围上下文关系的简洁摘要（最多100字）"
    }}
}}

周围内容的上下文：
{context}

内容：{content}

请专注于提取对知识检索有用的有意义信息，并理解内容在更广泛上下文中的作用。"""

# 模态块模板
PROMPTS["image_chunk"] = """
图像内容分析：
图像路径：{image_path}
标题说明：{captions}
脚注：{footnotes}

视觉分析：{enhanced_caption}"""

PROMPTS["table_chunk"] = """表格分析：
图像路径：{table_img_path}
标题：{table_caption}
结构：{table_body}
脚注：{table_footnote}

分析：{enhanced_caption}"""

PROMPTS["equation_chunk"] = """数学公式分析：
公式：{equation_text}
格式：{equation_format}

数学分析：{enhanced_caption}"""

PROMPTS["generic_chunk"] = """{content_type} 内容分析：
内容：{content}

分析：{enhanced_caption}"""

# 查询相关提示词
PROMPTS["QUERY_IMAGE_DESCRIPTION"] = (
    "请简要描述这张图像的主要内容、关键元素和重要信息。"
)

PROMPTS["QUERY_IMAGE_ANALYST_SYSTEM"] = (
    "你是一位专业的图像分析师，能够准确描述图像内容。"
)

PROMPTS[
    "QUERY_TABLE_ANALYSIS"
] = """请分析以下表格数据的主要内容、结构和关键信息：

表格数据：
{table_data}

表格标题：{table_caption}

请简要总结表格的主要内容、数据特征和重要发现。"""

PROMPTS["QUERY_TABLE_ANALYST_SYSTEM"] = (
    "你是一位专业的数据分析师，能够准确分析表格数据。"
)

PROMPTS[
    "QUERY_EQUATION_ANALYSIS"
] = """请解释以下数学公式的含义和用途：

LaTeX 公式：{latex}
公式标题：{equation_caption}

请简要说明这个公式的数学含义、应用场景和重要性。"""

PROMPTS["QUERY_EQUATION_ANALYST_SYSTEM"] = (
    "你是一位数学专家，能够清晰地解释数学公式。"
)

PROMPTS[
    "QUERY_GENERIC_ANALYSIS"
] = """请分析以下 {content_type} 类型的内容，提取其主要信息和关键特征：

内容：{content_str}

请简要总结这个内容的主要特征和重要信息。"""

PROMPTS["QUERY_GENERIC_ANALYST_SYSTEM"] = (
    "你是一位专业的内容分析师，能够准确分析 {content_type} 类型的内容。"
)

PROMPTS["QUERY_ENHANCEMENT_SUFFIX"] = (
    "\n\n请根据用户查询和提供的多模态内容信息，提供全面的回答。"
)

