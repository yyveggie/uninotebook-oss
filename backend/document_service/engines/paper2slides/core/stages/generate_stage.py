"""
Generate Stage - Image generation
"""
import logging
from pathlib import Path
from typing import Dict

from ...utils import load_json
from ..paths import get_summary_checkpoint, get_plan_checkpoint, get_output_dir

logger = logging.getLogger(__name__)


async def run_generate_stage(base_dir: Path, config_dir: Path, config: Dict, session_id: str = None) -> Dict:
    """Stage 4: Generate images."""
    from document_service.engines.paper2slides.summary import PaperContent, GeneralContent, TableInfo, FigureInfo, OriginalElements
    from document_service.engines.paper2slides.generator import GenerationConfig, GenerationInput
    from document_service.engines.paper2slides.generator.config import OutputType, PosterDensity, SlidesLength, StyleType
    from document_service.engines.paper2slides.generator.content_planner import ContentPlan, Section, TableRef, FigureRef
    from document_service.engines.paper2slides.generator.image_generator import ImageGenerator, save_images_as_pdf
    
    plan_data = load_json(get_plan_checkpoint(config_dir))
    summary_data = load_json(get_summary_checkpoint(base_dir, config))
    if not plan_data or not summary_data:
        raise ValueError("Missing checkpoints.")
    
    content_type = plan_data.get("content_type", "paper")
    
    origin_data = plan_data["origin"]
    origin = OriginalElements(
        tables=[TableInfo(
            table_id=t["id"],
            caption=t.get("caption", ""),
            html_content=t.get("html", ""),
        ) for t in origin_data.get("tables", [])],
        figures=[FigureInfo(
            figure_id=f["id"],
            caption=f.get("caption"),
            image_path=f.get("path", ""),
        ) for f in origin_data.get("figures", [])],
        base_path=origin_data.get("base_path", ""),
    )
    
    plan_dict = plan_data["plan"]
    tables_index = {t.table_id: t for t in origin.tables}
    figures_index = {f.figure_id: f for f in origin.figures}
    
    sections = []
    for s in plan_dict.get("sections", []):
        sections.append(Section(
            id=s.get("id", ""),
            title=s.get("title", ""),
            section_type=s.get("type", "content"),
            content=s.get("content", ""),
            tables=[TableRef(**t) for t in s.get("tables", [])],
            figures=[FigureRef(**f) for f in s.get("figures", [])],
        ))
    
    plan = ContentPlan(
        output_type=plan_dict.get("output_type", "slides"),
        sections=sections,
        tables_index=tables_index,
        figures_index=figures_index,
        metadata=plan_dict.get("metadata", {}),
    )
    
    if content_type == "paper":
        content = PaperContent(**summary_data["content"])
    else:
        content = GeneralContent(**summary_data["content"])
    
    gen_config = GenerationConfig(
        output_type=OutputType(config.get("output_type", "slides")),
        poster_density=PosterDensity(config.get("poster_density", "medium")),
        slides_length=SlidesLength(config.get("slides_length", "medium")),
        style=StyleType(config.get("style", "academic")),
        custom_style=config.get("custom_style"),
    )
    gen_input = GenerationInput(config=gen_config, content=content, origin=origin)
    
    logger.info("Generating images...")
    
    is_poster = (plan.output_type == "poster")
    if is_poster:
        total_images = 1
    else:
        total_images = len(plan.sections)
        
    logger.info(f"Total images to generate: {total_images}")
    logger.info(f"Total images to generate: {total_images}") # Force print to terminal
    
    # Prepare output directory
    output_subdir = get_output_dir(config_dir)
    output_subdir.mkdir(parents=True, exist_ok=True)
    ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
    
    # Prepare thread-safe counter
    import threading
    progress_lock = threading.Lock()
    completed_count = 0
    
    # Initialize COS Service for real-time upload
    cos_service = None
    session_id_for_cos = session_id  # Use the passed session_id (UUID from DB)
    
    # Fallback: If session_id is not provided, try to load from state or use config_dir name
    if not session_id_for_cos:
        try:
            from document_service.engines.paper2slides.core.state import load_state
            state = load_state(config_dir)
            session_id_for_cos = state.get("session_id") if state else None
        except Exception:
            pass
        
        # Last resort fallback
        if not session_id_for_cos:
            session_id_for_cos = config_dir.name
            logger.warning(f"No session_id provided, using config_dir name: {session_id_for_cos}")
    
    try:
        # Direct module loading to avoid import path issues
        import importlib.util
        import sys
        from pathlib import Path
        
        backend_dir = Path(__file__).resolve().parents[5]
        cos_file = backend_dir / "utils" / "cos_service.py"
        
        if cos_file.exists():
            spec = importlib.util.spec_from_file_location("cos_service", cos_file)
            cos_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cos_module)
            COSService = cos_module.COSService
            
            cos_service = COSService()
            logger.info(f"Real-time COS upload enabled with session_id: {session_id_for_cos[:8]}...")
        else:
            logger.warning(f"COS service file not found at {cos_file}")
    except Exception as e:
        logger.warning(f"Failed to init real-time COS upload: {e}", exc_info=True)
        cos_service = None

    # Pre-scan COS for existing images (batch mode - single API call)
    existing_images = {}  # {index: cos_url}
    if cos_service and session_id_for_cos:
        logger.info("Scanning COS for existing images (batch optimization)...")
        # 根据 output_type 选择不同的 COS 路径
        output_type = config.get("output_type", "slides")
        cos_prefix = "poster_output" if output_type == "poster" else "ppt_output"
        prefix = f"{cos_prefix}/{session_id_for_cos}/"
        try:
            # Fetch all files in this session directory at once
            files = cos_service.list_files_with_prefix(prefix, max_keys=1000)
            
            for key in files:
                # key format example: ppt_output/session_id/003_section_title.png or poster_output/...
                filename = key.split('/')[-1]
                parts = filename.split('_')
                # Check if file starts with 3-digit number (our naming convention)
                if len(parts) > 1 and parts[0].isdigit():
                    try:
                        idx = int(parts[0])
                        existing_url = cos_service.get_file_url(key)
                        existing_images[idx] = existing_url
                        # logger.debug(f"  Found existing image {idx}: {filename}")
                    except ValueError:
                        continue
        except Exception as e:
            logger.warning(f"Failed to scan COS directory: {e}")
        
        if existing_images:
            logger.info(f"Found {len(existing_images)}/{len(plan.sections)} existing images in COS, will skip uploading these")
        else:
            msg_count = 1 if is_poster else len(plan.sections)
            logger.info(f"No existing images found, will generate {msg_count} images")

    # Filter sections based on what exists in COS
    sections_to_generate = []
    section_index_map = {}  # Maps filtered index to original index
    
    if is_poster:
        # Poster Mode: All-or-Nothing
        # If we have index 0 (the poster), we skip everything.
        # Otherwise we generate everything.
        if 0 in existing_images:
             logger.info("Found existing poster image, skipping generation.")
        else:
             sections_to_generate = plan.sections
             # For poster, we map all filtered indices to themselves? 
             # Actually save_callback for poster only gets called once with index 0.
             # So we just need section_index_map[0] = 0
             section_index_map = {i: i for i in range(len(plan.sections))}
    else:
        # Slides Mode: Per-section generation
        for i, section in enumerate(plan.sections):
            if i not in existing_images:
                # This section needs to be generated
                filtered_idx = len(sections_to_generate)
                section_index_map[filtered_idx] = i
                sections_to_generate.append(section)
    
    if not sections_to_generate:
        # All images already exist, skip generation entirely
        logger.info(f"✅ All {len(plan.sections)} images already exist in COS, skipping all API calls")
        logger.info(f"✅ Skipped {len(plan.sections)} API calls - all images exist in COS (saved money!)")
        return {
            "output_dir": str(output_subdir),
            "num_images": len(plan.sections),
            "skipped": True,
            "reason": "All images exist in COS"
        }
    
    # Create filtered plan with only sections that need generation
    from copy import deepcopy
    filtered_plan = deepcopy(plan)
    filtered_plan.sections = sections_to_generate
    
    gen_count = 1 if is_poster else len(sections_to_generate)
    total_count = 1 if is_poster else len(plan.sections)
    logger.info(f"Generating {gen_count}/{total_count} images (skipping {len(existing_images)} existing)")
    
    # Modified callback to use correct original indices
    def save_image_callback(img, filtered_index, total_filtered):
        nonlocal completed_count
        # Get original index
        original_index = section_index_map[filtered_index]
        total_original = 1 if is_poster else len(plan.sections)
        
        ext = ext_map.get(img.mime_type, ".png")
        filename = f"{original_index:03d}_{img.section_id}{ext}"
        filepath = output_subdir / filename
        
        # Save locally (temporary)
        with open(filepath, "wb") as f:
            f.write(img.image_data)
        logger.info(f"  [{original_index+1}/{total_original}] Saved local: {filepath.name}")
        
        # Upload to COS immediately
        cos_uploaded = False
        if cos_service:
            try:
                cos_key = f"{cos_prefix}/{session_id_for_cos}/{filename}"
                url = cos_service.upload_file(str(filepath), cos_key)
                if url:
                    logger.info(f"  [{original_index+1}/{total_original}] Uploaded COS: {url}")
                    cos_uploaded = True

                    # Persist uploaded URL to state for downstream tasks (poster sync/status)
                    try:
                        from document_service.engines.paper2slides.core.state import load_state, save_state
                        with progress_lock:
                            state_for_url = load_state(config_dir) or {}
                            generated_urls = state_for_url.get("generated_image_urls")
                            if not isinstance(generated_urls, dict):
                                generated_urls = {}
                            generated_urls[str(original_index)] = url
                            state_for_url["generated_image_urls"] = generated_urls
                            save_state(config_dir, state_for_url)
                    except Exception as persist_err:
                        logger.warning(f"  [{original_index+1}/{total_original}] Failed to persist COS URL to state: {persist_err}")
                    
                    # Delete local file after successful COS upload
                    try:
                        filepath.unlink()
                        logger.info(f"  [{original_index+1}/{total_original}] Deleted local file (saved to COS)")
                    except Exception as del_err:
                        logger.warning(f"  [{original_index+1}/{total_original}] Failed to delete local file: {del_err}")
            except Exception as e:
                logger.error(f"  [{original_index+1}/{total_original}] COS Upload failed: {e}")

        # Update state with granular progress
        try:
             with progress_lock:
                 completed_count += 1
                 current_count = completed_count
             
             from document_service.engines.paper2slides.core.state import load_state, save_state
             current_state = load_state(config_dir)
             if current_state:
                 current_state["generate_progress"] = {
                     "current": current_count + len(existing_images),  # Add existing count
                     "total": total_original,
                     "message": f"Generated {current_count + len(existing_images)}/{total_original} slides"
                 }
                 save_state(config_dir, current_state)
        except Exception as e:
             logger.warning(f"Failed to update progress state: {e}")
    
    generator = ImageGenerator()
    max_workers = config.get("max_workers", 1)
    images = generator.generate(filtered_plan, gen_input, max_workers=max_workers, save_callback=save_image_callback)
    
    # 使用正确的总数（海报为1，PPT为section数量）
    actual_total = 1 if is_poster else len(plan.sections)
    logger.info(f"  Generated {len(images)} new images (total {len(images) + len(existing_images)}/{actual_total})")

    
    logger.info("")
    logger.info(f"Output: {output_subdir}")
    
    return {"output_dir": str(output_subdir), "num_images": len(images) + len(existing_images)}
