"""
独立的海报 RAG 流程
分三批请求，每批两个类别，组成 summary 给绘图模型
"""
import asyncio
import logging
from pathlib import Path
from typing import Dict, List
from openai import OpenAI

from .rag_queries import POSTER_RAG_BATCHES

logger = logging.getLogger(__name__)


async def run_poster_rag(
    markdown_paths: List[str],
    queries_by_category: Dict[str, List[str]],  # 保持接口兼容，但实际用 POSTER_RAG_BATCHES
    api_key: str,
    base_url: str,
    model: str,
    max_concurrency: int = 3,
) -> Dict[str, List[Dict]]:
    """
    海报专用 RAG 流程
    
    流程:
    1. 读取并合并所有 markdown 文件
    2. 分三批请求：每批包含 2 个类别的 queries
    3. 每批请求发送：文档内容 + 该批次所有 queries
    4. 收集所有回答，返回结构化结果
    """
    logger.info(f"[Poster RAG] Using Model: {model}")
    logger.info("[Poster RAG] Processing markdown files...")
    
    client = OpenAI(api_key=api_key, base_url=base_url)
    
    # 1. 合并所有 markdown 内容
    full_content = ""
    for md_path in markdown_paths:
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        if len(markdown_paths) > 1:
            full_content += f"\n\n=== {Path(md_path).name} ===\n\n"
        full_content += content
        logger.info(f"  {Path(md_path).name}: {len(content)} chars")
    
    total_chars = len(full_content)
    logger.info(f"[Poster RAG] Total content: {total_chars} chars")
    
    # 2. 截取内容以适应模型上下文（约 80k 字符 ≈ 20k tokens）
    MAX_CONTENT_CHARS = 80000
    if total_chars > MAX_CONTENT_CHARS:
        # 从开头 + 结尾各取一半
        half = MAX_CONTENT_CHARS // 2
        full_content = full_content[:half] + "\n\n[...中间内容省略...]\n\n" + full_content[-half:]
        logger.warning(f"[Poster RAG] Content truncated to {MAX_CONTENT_CHARS} chars (head + tail)")
    
    # 3. 分批请求
    all_results = {}
    
    for batch_idx, batch in enumerate(POSTER_RAG_BATCHES):
        logger.info(f"[Poster RAG] Processing batch {batch_idx + 1}/{len(POSTER_RAG_BATCHES)}: {list(batch.keys())}")
        
        # 构建该批次的所有 queries
        queries_text = ""
        query_mapping = []  # 记录 category 和 query 的对应关系
        
        for category, queries in batch.items():
            for query in queries:
                query_mapping.append((category, query))
                queries_text += f"\n{len(query_mapping)}. [{category}] {query}"
        
        # 构建请求
        prompt = f"""# 文档内容

{full_content}

# 问题列表

请根据上述文档内容，回答以下问题。每个问题单独回答，格式为：
[问题编号] 回答内容

{queries_text}

请逐一回答上述问题："""
        
        try:
            response = await asyncio.to_thread(
                lambda: client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": "你是学术论文分析专家。根据提供的文档内容详细回答每个问题。"},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.3,
                    timeout=180,
                )
            )
            
            answer_text = response.choices[0].message.content
            
            # 解析回答，尝试按编号分割
            for idx, (category, query) in enumerate(query_mapping):
                if category not in all_results:
                    all_results[category] = []
                
                # 简单提取：查找 [idx+1] 或 idx+1. 开头的段落
                # 由于 LLM 回复格式可能不一致，这里简单处理
                all_results[category].append({
                    "query": query,
                    "answer": answer_text,  # 整个批次的回答（后续可优化解析）
                    "mode": "poster_batch",
                    "success": True,
                    "batch": batch_idx + 1
                })
            
            logger.info(f"[Poster RAG] Batch {batch_idx + 1} completed")
            
        except Exception as e:
            logger.error(f"[Poster RAG] Batch {batch_idx + 1} failed: {e}")
            # 记录失败
            for category, queries in batch.items():
                if category not in all_results:
                    all_results[category] = []
                for query in queries:
                    all_results[category].append({
                        "query": query,
                        "answer": None,
                        "mode": "poster_batch",
                        "success": False,
                        "error": str(e)
                    })
    
    successful = sum(1 for cat_results in all_results.values() for r in cat_results if r.get("success"))
    total = sum(len(cat_results) for cat_results in all_results.values())
    logger.info(f"[Poster RAG] Completed: {successful}/{total} queries successful")
    
    return all_results
