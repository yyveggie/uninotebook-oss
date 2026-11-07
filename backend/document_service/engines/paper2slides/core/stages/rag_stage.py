"""
RAG Stage - Document indexing and querying
"""
import os
import re
import base64
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Tuple

from ...utils import save_json
from ..paths import get_rag_checkpoint

logger = logging.getLogger(__name__)


def _get_image_mime_type(image_path: str) -> str:
    """Get MIME type for image file based on extension"""
    ext = Path(image_path).suffix.lower()
    mime_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.bmp': 'image/bmp',
        '.webp': 'image/webp',
        '.tiff': 'image/tiff',
        '.tif': 'image/tiff',
    }
    return mime_types.get(ext, 'image/jpeg')  # Default to jpeg


def _encode_image_to_base64(image_path: str) -> str:
    """Encode image file to base64 string"""
    try:
        with open(image_path, "rb") as image_file:
            encoded_string = base64.b64encode(image_file.read()).decode("utf-8")
        return encoded_string
    except Exception as e:
        logger.error(f"Failed to encode image {image_path}: {e}")
        return ""


def _replace_images_with_base64(markdown_content: str, markdown_base_path: str) -> Tuple[List, int]:
    """
    Replace image references in markdown with base64 encoded images, preserving position
    
    Args:
        markdown_content: Markdown text content
        markdown_base_path: Directory where markdown file is located
    """
    content_parts = []
    last_pos = 0
    image_count = 0
    
    # Match both markdown image syntax and MinerU format
    # Pattern captures the full match and the image path
    pattern = r'(!\[.*?\]\((.*?\.(?:jpg|jpeg|png|gif|bmp|webp|tiff|tif))\)|Image Path:\s*([^\r\n]*?\.(?:jpg|jpeg|png|gif|bmp|webp|tiff|tif)))'
    
    for match in re.finditer(pattern, markdown_content, re.IGNORECASE | re.DOTALL):
        # Add text before this image
        if match.start() > last_pos:
            text_part = markdown_content[last_pos:match.start()]
            if text_part.strip():
                content_parts.append({
                    "type": "text",
                    "text": text_part
                })
        
        # Extract image path (from either group 2 or 3)
        image_path = match.group(2) if match.group(2) else match.group(3)
        image_path = image_path.strip()
        
        # Handle relative paths
        if not Path(image_path).is_absolute():
            image_path = str(Path(markdown_base_path) / image_path)
        
        # Try to encode image
        if Path(image_path).exists():
            # Optimization: Skip base64 embedding in RAG stage to avoid exceeding API payload limits (4MB)
            # Text extraction usually relies on captions/body text anyway.
            # base64_str = _encode_image_to_base64(image_path)
            base64_str = None 
            
            if base64_str:
                mime_type = _get_image_mime_type(image_path)
                content_parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_str}"
                    }
                })
                image_count += 1
                logger.debug(f"Embedded image at position {match.start()}: {image_path}")
            else:
                # Use placeholder
                content_parts.append({
                    "type": "text",
                    "text": f"\n[Image: {Path(image_path).name}]\n" 
                })
        else:
            logger.warning(f"Image not found: {image_path}")
            # Keep original text reference
            content_parts.append({
                "type": "text",
                "text": match.group(0)
            })
        
        last_pos = match.end()
    
    # Add remaining text after last image
    if last_pos < len(markdown_content):
        remaining_text = markdown_content[last_pos:]
        if remaining_text.strip():
            content_parts.append({
                "type": "text",
                "text": remaining_text
            })
    
    return content_parts, image_count


def _build_batch_prompt(categories: list, queries_by_category: dict) -> tuple:
    """
    Build a structured prompt that asks all questions for multiple categories at once.
    
    Returns:
        (prompt_text, question_index) where question_index maps IDs to (category, query_idx, query_text)
    """
    question_index = {}  # Maps "Q{batch_cat_idx}.{q_idx}" -> (category, q_idx, query_text)
    prompt_parts = []
    prompt_parts.append("Please answer each question below based on the document content above.")
    prompt_parts.append("Answer each question separately, using the exact ID format shown.")
    prompt_parts.append("If a question cannot be answered from the document, write 'N/A'.")
    prompt_parts.append("")
    prompt_parts.append("# Questions\n")
    
    cat_counter = 0
    for category in categories:
        queries = queries_by_category.get(category, [])
        if not queries:
            continue
        cat_counter += 1
        prompt_parts.append(f"## Category: {category}\n")
        for q_idx, query in enumerate(queries):
            qid = f"Q{cat_counter}.{q_idx + 1}"
            question_index[qid] = (category, q_idx, query)
            prompt_parts.append(f"{qid}: {query}")
        prompt_parts.append("")
    
    prompt_parts.append("")
    prompt_parts.append("# Answer Format")
    prompt_parts.append("")
    prompt_parts.append("For each question, use the following format:")
    prompt_parts.append("")
    # Show example IDs from actual questions
    example_ids = list(question_index.keys())[:2]
    for eid in example_ids:
        prompt_parts.append(f"### {eid}")
        prompt_parts.append("[Your detailed answer here]")
        prompt_parts.append("")
    if len(question_index) > 2:
        prompt_parts.append("... (continue for all questions)")
    
    return "\n".join(prompt_parts), question_index


def _parse_batch_response(response_text: str, question_index: dict) -> dict:
    """
    Parse a batched LLM response into per-category, per-query results.
    
    Returns:
        Dict[category, List[{query, answer, mode, success}]]
    """
    import re
    
    # Build results structure
    results_by_category = {}
    for qid, (category, q_idx, query) in question_index.items():
        if category not in results_by_category:
            results_by_category[category] = []
    
    # Parse response: split by ### Q{x}.{y} headers
    # Pattern matches "### Q1.1" or "### Q2.3" etc.
    pattern = r'###\s*(Q\d+\.\d+)'
    parts = re.split(pattern, response_text)
    
    # parts[0] is preamble (before first match), then alternating: [id, content, id, content, ...]
    parsed_answers = {}
    for i in range(1, len(parts) - 1, 2):
        qid = parts[i].strip()
        answer = parts[i + 1].strip()
        parsed_answers[qid] = answer
    
    # Map parsed answers back to categories
    for qid, (category, q_idx, query) in question_index.items():
        answer = parsed_answers.get(qid, "")
        results_by_category[category].append({
            "query": query,
            "answer": answer if answer and answer != "N/A" else None,
            "mode": "batched_fast",
            "success": bool(answer and answer != "N/A"),
        })
    
    return results_by_category


async def _run_batched_queries(
    client,
    markdown_paths: list,
    queries_by_category: dict,
    batches: list,
    model: str = "gpt-4o",
    max_concurrency: int = 3,
) -> dict:
    """
    Optimized batch mode: consolidate multiple categories into fewer LLM calls.
    Instead of ~26 separate calls each with full document, sends ~3 calls.
    
    Args:
        client: OpenAI client
        markdown_paths: List of markdown file paths
        queries_by_category: All queries organized by category
        batches: List of category groups, e.g. [["paper_info", "figures"], ["motivation", "solution"], ...]
        model: Model to use
        max_concurrency: Max concurrent batch calls
    """
    logger.info(f"Using optimized batched queries: {len(batches)} batches")
    
    # ---- Step 1: Prepare document content (same as original) ----
    all_content_parts = []
    total_images = 0
    
    for md_path in markdown_paths:
        base_path = str(Path(md_path).parent)
        if len(markdown_paths) > 1:
            all_content_parts.append({"type": "text", "text": f"\n\n=== {Path(md_path).name} ===\n\n"})
        
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        content_parts, img_count = _replace_images_with_base64(content, base_path)
        all_content_parts.extend(content_parts)
        total_images += img_count
        logger.info(f"  {Path(md_path).name}: embedded {img_count} images")
    
    logger.info(f"Total embedded images: {total_images}")
    
    # ---- Step 2: Truncate if needed (same as original) ----
    MAX_CHARS = 500000
    current_chars = 0
    truncated_parts = []
    
    for part in all_content_parts:
        if part["type"] == "text":
            text_len = len(part["text"])
            if current_chars + text_len > MAX_CHARS:
                remaining = MAX_CHARS - current_chars
                if remaining > 0:
                    truncated_parts.append({
                        "type": "text",
                        "text": part["text"][:remaining] + "\n\n[...Content Truncated due to size limit...]"
                    })
                break
            else:
                truncated_parts.append(part)
                current_chars += text_len
        else:
            truncated_parts.append(part)
    
    if len(truncated_parts) < len(all_content_parts):
        total_chars = sum(len(p.get("text", "")) for p in all_content_parts if p.get("type") == "text")
        logger.warning(f"⚠️ Content too large (Total: {total_chars} chars). Truncated to {MAX_CHARS} chars.")
    
    all_content_parts = truncated_parts
    
    # ---- Step 3: Run batched queries concurrently ----
    semaphore = asyncio.Semaphore(max_concurrency)
    
    async def run_one_batch(batch_idx: int, categories: list):
        """Run a single batch containing multiple categories of questions."""
        async with semaphore:
            prompt_text, question_index = _build_batch_prompt(categories, queries_by_category)
            
            if not question_index:
                return {}
            
            num_questions = len(question_index)
            logger.info(f"  Batch {batch_idx + 1}: {categories} ({num_questions} questions)")
            
            system_prompt = "You are an expert at analyzing academic papers. Answer based on the provided content."
            
            user_content = [{"type": "text", "text": "# Document Content\n\n"}]
            user_content.extend(all_content_parts)
            user_content.append({"type": "text", "text": f"\n\n{prompt_text}"})
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            try:
                response = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.3,
                        timeout=600,  # Longer timeout since answering multiple questions
                    )
                )
                answer_text = response.choices[0].message.content
                batch_results = _parse_batch_response(answer_text, question_index)
                
                success_count = sum(
                    1 for cat_results in batch_results.values()
                    for r in cat_results if r.get("success")
                )
                logger.info(f"  Batch {batch_idx + 1} completed: {success_count}/{num_questions} answers")
                
                return batch_results
            except Exception as e:
                logger.error(f"  Batch {batch_idx + 1} failed: {e}")
                # Return empty results for all categories in this batch
                empty_results = {}
                for qid, (category, q_idx, query) in question_index.items():
                    if category not in empty_results:
                        empty_results[category] = []
                    empty_results[category].append({
                        "query": query,
                        "answer": None,
                        "mode": "batched_fast",
                        "success": False,
                        "error": str(e),
                    })
                return empty_results
    
    # Execute all batches concurrently
    tasks = [
        run_one_batch(idx, categories)
        for idx, categories in enumerate(batches)
    ]
    batch_results_list = await asyncio.gather(*tasks)
    
    # Merge all batch results into single dict
    merged_results = {}
    for batch_results in batch_results_list:
        for category, results in batch_results.items():
            if category in merged_results:
                merged_results[category].extend(results)
            else:
                merged_results[category] = results
    
    total = sum(len(r) for r in merged_results.values())
    logger.info(f"Batched queries completed: {total} answers from {len(batches)} batches")
    
    return merged_results


async def _run_fast_queries_by_category(
    client,
    markdown_content: str,
    markdown_paths: List[str],
    queries_by_category: Dict[str, List[str]],
    model: str = "gpt-4o",
    max_concurrency: int = 3,  # Reduced concurrency to avoid connection errors
) -> Dict[str, List[Dict]]:
    """
    Fast mode: Direct GPT-4o queries with markdown content and images in original positions
    
    Args:
        client: OpenAI client
        markdown_content: Complete markdown text
        markdown_paths: List of markdown file paths
        queries_by_category: Queries organized by category
        model: Model to use (default to gpt-4o if not specified, but caller should provide)
        max_concurrency: Max concurrent queries
    """
    # Process all markdown files and embed images at original positions
    logger.info(f"Using Model: {model}") # Add log to confirm
    logger.info("Processing markdown files and embedding images...")
    
    all_content_parts = []
    total_images = 0
    
    for md_path in markdown_paths:
        base_path = str(Path(md_path).parent)
        
        # Add document separator if multiple files
        if len(markdown_paths) > 1:
            all_content_parts.append({
                "type": "text",
                "text": f"\n\n=== {Path(md_path).name} ===\n\n"
            })
        
        with open(md_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Replace images with base64 at original positions
        content_parts, img_count = _replace_images_with_base64(content, base_path)
        all_content_parts.extend(content_parts)
        total_images += img_count
        
        logger.info(f"  {Path(md_path).name}: embedded {img_count} images")
    
    logger.info(f"Total embedded images: {total_images}")

    # OPTIMIZATION: Truncate content if too large (Fix for 4MB payload limit)
    # 1 char ~= 1 byte. 4MB limit ~= 4M chars. 
    # We set a safe limit of 500k chars (~100k tokens) to be safe and avoid costs/errors.
    MAX_CHARS = 500000 
    current_chars = 0
    truncated_parts = []
    
    for part in all_content_parts:
        if part["type"] == "text":
            text_len = len(part["text"])
            if current_chars + text_len > MAX_CHARS:
                # Truncate this part and stop
                remaining = MAX_CHARS - current_chars
                if remaining > 0:
                    truncated_parts.append({
                        "type": "text",
                        "text": part["text"][:remaining] + "\n\n[...Content Truncated due to size limit...]"
                    })
                break
            else:
                truncated_parts.append(part)
                current_chars += text_len
        else:
            # Keep non-text parts (e.g. images if any, though we disabled them)
            # But assume they don't add much size if base64 disabled
            truncated_parts.append(part)
    
    if len(truncated_parts) < len(all_content_parts) or (truncated_parts and len(truncated_parts[-1].get("text","")) < len(all_content_parts[len(truncated_parts)-1].get("text",""))):
        total_chars = sum(len(p.get("text", "")) for p in all_content_parts if p.get("type") == "text")
        logger.warning(f"⚠️ Content too large (Total: {total_chars} chars). Truncated to {MAX_CHARS} chars.")
    
    all_content_parts = truncated_parts
    
    semaphore = asyncio.Semaphore(max_concurrency)
    
    async def query_one(category: str, idx: int, query: str):
        async with semaphore:
            try:
                system_prompt = "You are an expert at analyzing academic papers. Answer based on the provided content."
                
                # Build message content
                messages = [
                    {"role": "system", "content": system_prompt}
                ]
                
                # Build user message: document first, then query at the end
                user_content = [
                    {
                        "type": "text",
                        "text": "# Document Content\n\n"
                    }
                ]
                
                # Add all content parts (text and images in original order)
                user_content.extend(all_content_parts)
                
                # Add query at the end
                user_content.append({
                    "type": "text",
                    "text": f"""

# Question

{query}

Please provide a detailed answer based on the content and images above."""
                })
                
                messages.append({
                    "role": "user",
                    "content": user_content
                })
                
                # Call OpenAI API
                response = await asyncio.to_thread(
                    lambda: client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.3,
                        timeout=300,  # Explicit 5-minute timeout
                    )
                )
                
                answer = response.choices[0].message.content
                
                return (category, idx, {
                    "query": query,
                    "answer": answer,
                    "mode": "fast_direct_with_vision",
                    "success": True,
                })
            except Exception as e:
                logger.error(f"Query failed: {query[:50]}... Error: {e}")
                return (category, idx, {
                    "query": query,
                    "answer": None,
                    "mode": "fast_direct_with_vision",
                    "success": False,
                    "error": str(e),
                })
    
    # Create all tasks
    tasks = []
    for category, queries in queries_by_category.items():
        for idx, query in enumerate(queries):
            tasks.append(query_one(category, idx, query))
    
    # Execute concurrently
    all_results = await asyncio.gather(*tasks)
    
    # Group by category
    results_by_category = {cat: [] for cat in queries_by_category.keys()}
    for category, idx, result in all_results:
        results_by_category[category].append((idx, result))
    
    # Restore order
    for category in results_by_category:
        results_by_category[category].sort(key=lambda x: x[0])
        results_by_category[category] = [r for _, r in results_by_category[category]]
    
    return results_by_category


async def run_rag_stage(base_dir: Path, config: Dict) -> Dict:
    """Stage 1: Index document and run RAG queries.
    
    Args:
        base_dir: Base directory for this document/project
        config: Pipeline configuration with input_path (file or directory)
    
    Note:
        RAGClient handles both single files and directories automatically.
        Multiple files will share the same RAG storage for unified content extraction.
    """
    # Get input path from config
    input_path = config.get("input_path")
    if not input_path:
        raise ValueError("Missing input_path in config")
    
    content_type = config.get("content_type", "paper")
    fast_mode = config.get("fast_mode", True)
    path = Path(input_path)
    
    # Determine storage directory
    output_dir = base_dir / "rag_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # ========== FAST MODE: Parse only, no indexing ==========
    if fast_mode:
        logger.info("Running in FAST mode (parse only, no indexing)")
        
        from document_service.engines.paper2slides.raganything.batch_parser import BatchParser
        from document_service.engines.paper2slides.rag import RAG_PAPER_QUERIES
        
        # Parse documents to generate markdown
        # Update progress
        progress_file = output_dir / "progress.json"
        try:
             save_json(progress_file, {"status": "parsing", "percent": 10})
        except Exception:
             pass

        batch_parser = BatchParser(
            parser_type="unstructured", # Changed from mineru to unstructured
            max_workers=4,
            show_progress=True,
            skip_installation_check=True,
        )
        
        if path.is_file():
            logger.info(f"Parsing file: {path.name}")
        else:
            logger.info(f"Parsing directory: {path.name}")
        
        parse_result = batch_parser.process_batch(
            file_paths=[input_path],
            output_dir=str(output_dir),
            parse_method="auto",
            recursive=True,
        )
        
        try:
             save_json(progress_file, {"status": "analyzing", "percent": 40})
        except Exception:
             pass
        
        logger.info(f"  Parsing completed: {len(parse_result.successful_files)} successful")
        
        # Collect markdown files
        md_files = list(output_dir.rglob("*.md"))
        markdown_paths = [str(f) for f in md_files]
        
        if not markdown_paths:
            raise ValueError("No markdown files generated")
        
        logger.info(f"  Found {len(markdown_paths)} markdown file(s)")
        
        # Use OpenAI to query markdown content directly
        logger.info("")
        logger.info(f"Running queries with LLM and images ({content_type})...")
        
        from openai import OpenAI
        
        api_key = config.get("api_key") or os.getenv("RAG_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = config.get("base_url") or os.getenv("RAG_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        
        logger.info(f"RAG Stage Client Init: Base URL={base_url}, API Key set? {bool(api_key)}")
        model = config.get("model") or os.getenv("LLM_MODEL", "gpt-4o")
        
        # 判断是否是海报生成 - 使用独立的分块 RAG 流程
        output_type = config.get("output_type", "slides")
        if output_type == "poster":
            logger.info("Using chunked RAG for poster generation...")
            from document_service.generators.poster.poster_rag import run_poster_rag
            queries_map = config.get("rag_queries") or RAG_PAPER_QUERIES
            rag_results = await run_poster_rag(
                markdown_paths=markdown_paths,
                queries_by_category=queries_map,
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        elif content_type == "paper":
            from document_service.engines.paper2slides.rag import RAG_PAPER_BATCHES
            client = OpenAI(api_key=api_key, base_url=base_url)
            queries_map = config.get("rag_queries") or RAG_PAPER_QUERIES
            rag_results = await _run_batched_queries(
                client=client,
                markdown_paths=markdown_paths,
                queries_by_category=queries_map,
                batches=RAG_PAPER_BATCHES,
                model=model,
            )
        else:
            # General mode fast queries
            client = OpenAI(api_key=api_key, base_url=base_url)
            general_queries = {
                "general_analysis": [
                    "Please provide a comprehensive summary of this document.",
                    "What are the key concepts, main arguments, and important details?",
                    "Describe the structure and outline of the document content."
                ]
            }
            rag_results = await _run_fast_queries_by_category(
                client=client,
                markdown_content="",
                markdown_paths=markdown_paths,
                queries_by_category=general_queries,
                model=model,
            )
        
        total = sum(len(r) for r in rag_results.values())
        logger.info(f"  Completed {total} queries")
    
    # ========== NORMAL MODE: Full RAG pipeline ==========
    else:
        from document_service.engines.paper2slides.rag import RAGClient, RAG_PAPER_QUERIES, RAG_QUERY_MODES
        from document_service.engines.paper2slides.rag.query import get_general_overview, generate_general_queries
        from document_service.engines.paper2slides.rag.config import RAGConfig, APIConfig, StorageConfig
        
        storage_dir = base_dir / "rag_storage"
        storage_dir.mkdir(parents=True, exist_ok=True)
        
        # 从 config 获取 API 配置
        api_key = config.get("api_key") or os.getenv("RAG_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = config.get("base_url") or os.getenv("RAG_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
        model = config.get("model") or os.getenv("LLM_MODEL", "gpt-4o")
        
        # 创建 API 和 Storage 配置
        api_config = APIConfig(
            llm_api_key=api_key,
            llm_base_url=base_url,
            llm_model=model
        )
        storage_config = StorageConfig(
            storage_dir=str(storage_dir),
            output_dir=str(output_dir)
        )
        
        # Create RAGClient with unified storage and API config
        rag_config = RAGConfig(
            api=api_config,
            storage=storage_config
        )
        
        async with RAGClient(config=rag_config) as rag:
            # Index files. RAGClient handles both files and directories
            if path.is_file():
                logger.info(f"Indexing file: {path.name}")
            else:
                logger.info(f"Indexing directory: {path.name}")
            
            batch_result = await rag.index_batch(
                file_paths=[input_path],
                output_dir=str(output_dir),
                recursive=True,
                show_progress=True
            )
            
            logger.info(f"  Indexing completed: {batch_result.get('successful_rag_files', 0)} successful, {batch_result.get('failed_rag_files', 0)} failed")
            
            # Collect markdown paths from parser output
            md_files = list(output_dir.rglob("*.md"))
            markdown_paths = [str(f) for f in md_files]
            
            if markdown_paths:
                logger.info(f"  Found {len(markdown_paths)} markdown file(s)")
            
            logger.info("")
            logger.info(f"Running RAG queries ({content_type})...")
            
            if content_type == "paper":
                # 使用 config 中的 rag_queries（如果提供），否则使用默认的 RAG_PAPER_QUERIES
                queries_to_use = config.get("rag_queries") or RAG_PAPER_QUERIES
                rag_results = await rag.batch_query_by_category(
                    queries_by_category=queries_to_use,
                    modes_by_category=RAG_QUERY_MODES,
                )
            else:
                logger.info("  Getting document overview...")
                overview = await get_general_overview(rag, mode="mix")
                logger.info("  Generating queries from overview...")
                queries = generate_general_queries(rag, overview, count=12)
                logger.info(f"  Executing {len(queries)} queries...")
                query_results = await rag.batch_query(queries, mode="mix")
                rag_results = {"content": query_results}
            
            total = sum(len(r) for r in rag_results.values())
            logger.info(f"  Completed {total} queries")
    
    # Save result to mode-specific directory
    result = {
        "rag_results": rag_results,
        "markdown_paths": markdown_paths,
        "input_path": input_path,
        "content_type": content_type,
        "mode": "fast" if fast_mode else "normal",
    }
    
    # Ensure mode directory exists
    checkpoint_path = get_rag_checkpoint(base_dir, config)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    
    save_json(checkpoint_path, result)
    logger.info(f"  Saved: {checkpoint_path}")
    return result
