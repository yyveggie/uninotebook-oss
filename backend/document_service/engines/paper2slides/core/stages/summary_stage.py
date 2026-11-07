"""
Summary Stage - Content extraction from RAG results
Optimized with parallel processing for faster execution
"""
import os
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor

from ...utils import load_json, save_json, save_text
from ..paths import get_rag_checkpoint, get_summary_checkpoint, get_summary_md

logger = logging.getLogger(__name__)


def _extract_tables_figures_sync(markdown_paths: List[str]) -> Tuple[list, list, str]:
    """
    同步提取表格和图片 (在线程池中运行)
    返回: (all_tables, all_figures, base_path)
    """
    from document_service.engines.paper2slides.summary import extract_tables_and_figures
    
    all_tables = []
    all_figures = []
    base_path = ""
    
    for i, md_path in enumerate(markdown_paths):
        origin = extract_tables_and_figures(md_path)
        
        # 多文档时添加前缀避免 ID 冲突
        if len(markdown_paths) > 1:
            doc_prefix = f"Doc{i+1}"
            for table in origin.tables:
                if not table.table_id.startswith(doc_prefix):
                    table.table_id = f"{doc_prefix}_{table.table_id}"
            for figure in origin.figures:
                if not figure.figure_id.startswith(doc_prefix):
                    figure.figure_id = f"{doc_prefix}_{figure.figure_id}"
        
        all_tables.extend(origin.tables)
        all_figures.extend(origin.figures)
        if not base_path and origin.base_path:
            base_path = origin.base_path
    
    return all_tables, all_figures, base_path


async def run_summary_stage(base_dir: Path, config: Dict) -> Dict:
    """Stage 2: Extract content from RAG results.
    
    优化: 表格/图片提取与 LLM 内容提取并行执行
    """
    from openai import OpenAI
    from document_service.engines.paper2slides.summary import extract_paper, extract_general, OriginalElements
    from document_service.engines.paper2slides.summary.paper import extract_paper_metadata_from_markdown
    
    rag_data = load_json(get_rag_checkpoint(base_dir, config))
    if not rag_data:
        raise ValueError("Missing RAG checkpoint.")
    
    rag_results = rag_data["rag_results"]
    markdown_paths = rag_data.get("markdown_paths", [])
    content_type = rag_data.get("content_type", "paper")
    
    api_key = config.get("api_key") or os.getenv("RAG_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    base_url = config.get("base_url") or os.getenv("RAG_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    model = config.get("model") or os.getenv("LLM_MODEL") or "qwen3-max"
    
    llm_client = OpenAI(api_key=api_key, base_url=base_url)
    
    logger.info(f"Extracting content from indexed documents ({content_type})...")
    
    # ========== 并行任务准备 ==========
    
    async def extract_content_task():
        """LLM 内容提取任务"""
        if content_type == "paper":
            # 先提取论文元数据
            if markdown_paths:
                num_files = len(markdown_paths)
                logger.info(f"  [Parallel] Extracting paper metadata from {num_files} markdown file(s)...")
                
                paper_metadata = await extract_paper_metadata_from_markdown(
                    markdown_paths=markdown_paths,
                    llm_client=llm_client,
                    model=model,
                    max_chars_per_file=3000
                )
                
                metadata_result = {
                    "query": "List the paper title, author names and their institutional affiliations.",
                    "answer": paper_metadata,
                    "mode": "direct_markdown_extraction",
                    "success": True,
                }
                rag_results["paper_info"] = [metadata_result]
                logger.info("  [Parallel] Paper metadata extracted successfully")
            
            content = await extract_paper(
                rag_results=rag_results,
                llm_client=llm_client,
                model=model,
                parallel=True,  # 并行提取 motivation/solution/results/contributions
                max_concurrency=4,
            )
            return content, content.to_summary()
        else:
            all_results = []
            for items in rag_results.values():
                all_results.extend(items)
            content = await extract_general(
                rag_results=all_results,
                llm_client=llm_client,
                model=model,
            )
            return content, content.content
    
    async def extract_tables_figures_task():
        """表格/图片提取任务 (I/O 密集型，在线程池中运行)"""
        logger.info("  [Parallel] Extracting tables and figures...")
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = await loop.run_in_executor(
                executor,
                _extract_tables_figures_sync,
                markdown_paths
            )
        logger.info(f"  [Parallel] Tables/figures extraction completed")
        return result
    
    # ========== 并行执行 ==========
    logger.info("  Starting parallel extraction (content + tables/figures)...")
    
    (content, summary_text), (all_tables, all_figures, base_path) = await asyncio.gather(
        extract_content_task(),
        extract_tables_figures_task(),
    )
    
    logger.info(f"  Summary: {len(summary_text)} chars")
    logger.info(f"  Tables: {len(all_tables)}, Figures: {len(all_figures)}")
    
    # ========== 构建结果 ==========
    from document_service.engines.paper2slides.summary import OriginalElements
    origin = OriginalElements(
        tables=all_tables,
        figures=all_figures,
        base_path=base_path
    )
    
    # Save to mode-specific directory
    save_text(get_summary_md(base_dir, config), summary_text)
    
    result = {
        "content_type": content_type,
        "content": content.__dict__,
        "origin": {
            "tables": origin.get_table_info(),
            "figures": origin.get_figure_info(),
            "base_path": origin.base_path,
        },
        "markdown_paths": markdown_paths,
    }
    
    checkpoint_path = get_summary_checkpoint(base_dir, config)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    
    save_json(checkpoint_path, result)
    logger.info(f"  Saved: {checkpoint_path}")
    return result
