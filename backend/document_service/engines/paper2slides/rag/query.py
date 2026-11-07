from typing import List, Dict, Union, Any, TYPE_CHECKING, TypedDict
from utils.logger import get_logger
logger = get_logger(__name__)

if TYPE_CHECKING:
    from .client import RAGClient


class RAGQueryResult(TypedDict, total=False):
    """Single RAG query result from batch_query."""
    query: str
    answer: str
    mode: str  # query mode: local, global, hybrid, naive, mix
    success: bool
    error: str  # only present when success=False


RAG_PAPER_QUERIES: Dict[str, List[str]] = {
    "paper_info": [
        "列出论文标题、作者姓名及其所属机构。",
    ],
    
    "figures": [
        "描述论文的架构图或框架图。图中展示了哪些主要组件？"
    ],
    
    "tables": [
        "展示主要的性能对比表格，包含所有方法和评估指标。请给出具体数值。",
        "展示消融实验表格，包含所有变体及其实验结果。",
        "展示数据集统计表格，包含数据量、划分方式及其他细节。"
    ],
    
    "equations": [
        "核心模型公式或主要方程是什么？请展示确切的公式和符号说明。"
    ],
    
    "motivation": [
        "本文旨在解决什么问题或任务？请描述具体的挑战。",
        "引言或相关工作中提到了现有方法哪些局限性或缺陷？",
        "是什么需求或研究空白激发了这项研究？与先前工作有何不同？",
    ],
    
    "solution": [
        "本文提出了什么方法、途径或框架？请提供概述。",
        "提出方法的主要组件、模块或步骤是什么？",
        "描述系统设计、模型结构或理论框架。如有架构图或表格请一并说明。",
        "关键方程、公式或数学表达式是什么？请展示符号和数学表达式。",
        "使用了什么目标函数、优化目标或理论推导？",
        "算法、流程或工作流是什么？请描述关键步骤。",
        "提到了哪些关键参数、设置或实现细节？",
    ],
    
    "results": [
        "评估使用了哪些数据集、基准测试或实验设置？",
        "使用了哪些评估指标或标准来衡量性能？",
        "主结果表中展示了哪些主要结果？",
        "提出的方法与基线方法相比表现如何？请展示对比结果。",
        "方法达到了什么性能？请报告实验中的具体数值。",
        "进行了哪些消融实验或敏感性分析？有什么发现？",
        "提供了哪些结果分析、案例研究或讨论？",
    ],
    
    "contributions": [
        "引言或结论中列出的主要贡献是什么？",
        "与现有方法相比，这项工作有什么创新或新颖之处？",
        "论文承认了哪些局限性？提出了哪些未来研究方向？",
    ]
}

SKIP_LLM_SECTIONS = {"paper_info", "figures", "tables", "equations"}

# Batch groups for consolidated RAG queries (reduces ~26 LLM calls to 3)
# Each batch is sent as a single LLM call containing all queries for those categories
RAG_PAPER_BATCHES = [
    ["paper_info", "figures", "tables", "equations"],   # Batch 1: Factual extraction
    ["motivation", "solution"],                          # Batch 2: Method understanding
    ["results", "contributions"],                        # Batch 3: Evaluation analysis
]

RAG_QUERY_MODES: Dict[str, str] = {
    "paper_info": "local",
    "figures": "local",
    "tables": "local",
    "equations": "local",
    "motivation": "local",
    "solution": "local",
    "results": "local",
    "contributions": "local",
}


GENERAL_OVERVIEW_QUERIES = {
    "Document Type": "这是什么类型的文档？（如：学术论文、技术报告、教程、书籍章节、演示幻灯片、技术文档等）",
    "Title": "这篇文档的标题是什么？",
    "Content Overview": "这篇文档讲的是什么？请提供全面概述，包括主题、核心要点和主要内容。",
    "Purpose": "这篇文档的主要目的或目标是什么？它试图达成或解释什么？",
    "Structure": "这篇文档的结构是怎样的？主要章节或部分有哪些？",
    "Important Elements": "最重要或最值得注意的元素是什么？（如：核心发现、主要论点、重要概念、关键数据、核心方法等）",
    "Special Features": "有哪些特殊内容？（如：图表、表格、公式、方程式、代码示例、算法、案例研究等）",
}

_GENERATE_GENERAL_QUERIES_PROMPT = """你是一位专业的文档分析专家。请根据以下文档概述，生成 {count} 个查询问题，以全面深入理解这篇文档。

=== 文档概述 ===
{overview}

=== 你的任务 ===
生成 {count} 个查询问题，用于深度提取文档内容。你的问题应涵盖：

1. **文档主题**：核心思想、主要概念、解决的问题、关键论点
2. **具体内容与细节**：方法、技术、算法、公式、数据、结果、示例
3. **领域专业信息**：与本文档所属领域相关的技术细节、参数、实现方式
4. **不同章节/方面**：确保问题覆盖文档的各个部分

要求：
- 每个问题必须能够从文档中直接找到答案
- 询问具体细节（数字、方法、结果、示例）
- 不要重复概述中已有的信息
- 兼顾概念理解和技术细节

输出格式（仅 JSON）：
[
  {{"id": 1, "query": "这篇文档的主要研究重点或目标是什么？"}},
  {{"id": 2, "query": "文中描述了什么具体方法或技术？请解释其关键组成部分。"}},
  {{"id": 3, "query": "文中呈现了哪些具体结果、数据或发现？"}}
]

请严格按此 JSON 格式生成 {count} 个问题。"""


def _truncate_overview(overview: str, max_length: int = 6000) -> str:
    if len(overview) <= max_length:
        return overview
    return overview[:max_length] + "\n\n[Note: Overview truncated due to length]"

def _parse_queries_from_response(text: str) -> List[str]:
    """Parse LLM response to extract query strings."""
    import re
    import json
    
    try:
        json_match = re.search(r'```json\s*(\[.*?\])\s*```', text, re.DOTALL)
        if not json_match:
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
        
        if json_match:
            json_str = json_match.group(1) if json_match.lastindex else json_match.group(0)
            query_objects = json.loads(json_str)
            return [obj['query'] for obj in query_objects if isinstance(obj, dict) and 'query' in obj]
    except (json.JSONDecodeError, ValueError, KeyError):
        pass
    
    # Fallback: parse line by line
    queries = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        q = re.sub(r'^[\d]+[\.\)\-\s]+', '', line).strip()
        if q and ('?' in q or '？' in q or len(q) > 20):
            queries.append(q)
    
    return queries

def generate_general_queries(
    rag_client: "RAGClient",
    overview: str,
    count: int = 20,
) -> List[str]:
    """
    Generate queries for general document analysis using LLM.
    
    Args:
        rag_client: RAG client for API config
        overview: Document overview text
        count: Number of queries to generate
    """
    from openai import OpenAI
    
    prompt = _GENERATE_GENERAL_QUERIES_PROMPT.format(
        overview=_truncate_overview(overview),
        count=count,
    )
    
    try:
        config = rag_client.config.api
        
        client = OpenAI(
            api_key=config.llm_api_key,
            base_url=config.llm_base_url,
        )
        
        response = client.chat.completions.create(
            model=config.llm_model,
            messages=[{
                "role": "user",
                "content": prompt
            }]
        )
        
        result = response.choices[0].message.content
        if not result:
            return []
        
        return _parse_queries_from_response(result)
        
    except Exception as e:
        logger.info(f"[Error] Query generation failed: {e}")
        return []

async def get_general_overview(
    rag_client: "RAGClient",
    mode: str = "mix",
    max_section_length: int = 0,  # 0 = no truncation
) -> str:
    from document_service.engines.paper2slides.summary.clean import clean_references
    
    overview_parts = []
    
    for label, query in GENERAL_OVERVIEW_QUERIES.items():
        try:
            response = await rag_client.query(query, mode=mode)
            if response:
                # Clean references first
                response_cleaned = clean_references(response.strip())
                if max_section_length > 0 and len(response_cleaned) > max_section_length:
                    response_cleaned = response_cleaned[:max_section_length] + "..."
                overview_parts.append(f"[{label}]\n{response_cleaned}")
        except Exception as e:
            logger.info(f"[Warning] Overview query '{label}' failed: {e}")
            continue
    
    if not overview_parts:
        raise ValueError("Failed to get any document overview information.")
    
    return "\n\n".join(overview_parts)

async def get_queries(
    rag_client: "RAGClient" = None,
    use_predefined_paper_queries: bool = True,
    count: int = 8,
) -> Union[Dict[str, List[Dict[str, Any]]], List[str]]:
    """
    Get queries for document analysis.
    
    Returns:
        If use_predefined_paper_queries=True: Dict with query configs
        Otherwise: List of query strings
    """
    if use_predefined_paper_queries:
        return RAG_PAPER_QUERIES.copy()
    
    if rag_client is None:
        raise ValueError("RAG client is required for dynamic query generation.")
    
    overview = await get_general_overview(rag_client, mode="mix")
    return await generate_general_queries(rag_client, overview, count)
