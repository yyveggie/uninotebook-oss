"""
Image Generator

Generate poster/slides images from ContentPlan.
"""
import os
import json
import base64
import time
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
"""
Image Generator

Generate poster/slides images from ContentPlan.
"""
import os
import json
import base64
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
import requests
import dashscope
from dashscope import MultiModalConversation
from openai import OpenAI
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import settings  # Import main config


from .config import GenerationInput
from .content_planner import ContentPlan, Section
from ..prompts.image_generation import (
    STYLE_PROCESS_PROMPT,
    FORMAT_POSTER,
    FORMAT_SLIDE,
    POSTER_STYLE_HINTS,
    SLIDE_STYLE_HINTS,
    SLIDE_LAYOUTS_ACADEMIC,
    SLIDE_LAYOUTS_DORAEMON,
    SLIDE_LAYOUTS_DEFAULT,
    SLIDE_COMMON_STYLE_RULES,
    POSTER_COMMON_STYLE_RULES,
    VISUALIZATION_HINTS,
    CONSISTENCY_HINT,
    SLIDE_FIGURE_HINT,
    POSTER_FIGURE_HINT,
)


logger = logging.getLogger(__name__)


@dataclass
class GeneratedImage:
    """Generated image result."""
    section_id: str
    image_data: bytes
    mime_type: str


@dataclass
class ProcessedStyle:
    """Processed custom style from LLM."""
    style_name: str       # e.g., "Cyberpunk sci-fi style with high-tech aesthetic"
    color_tone: str       # e.g., "dark background with neon accents"
    special_elements: str # e.g., "Characters appear as guides" or ""
    decorations: str      # e.g., "subtle grid pattern" or ""
    valid: bool
    error: Optional[str] = None


def process_custom_style(client: OpenAI, user_style: str, model: str = None) -> ProcessedStyle:
    """Process user's custom style request with LLM."""
    from config import settings
    model = model or os.getenv("LLM_MODEL", settings.moonshot_model)
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": STYLE_PROCESS_PROMPT.format(user_style=user_style)}],
            response_format={"type": "json_object"},
        )
        result = json.loads(response.choices[0].message.content)
        return ProcessedStyle(
            style_name=result.get("style_name", ""),
            color_tone=result.get("color_tone", ""),
            special_elements=result.get("special_elements", ""),
            decorations=result.get("decorations", ""),
            valid=result.get("valid", False),
            error=result.get("error"),
        )
    except Exception as e:
        return ProcessedStyle(style_name="", color_tone="", special_elements="", decorations="", valid=False, error=str(e))


class ImageGenerator:
    """Generate poster/slides images from ContentPlan."""
    
    def __init__(
        self,
        api_key: str = None,
        base_url: str = None,
        model: str = None,
    ):
        from runtime.credentials import credential_store
        from llm_service import get_model_config

        self.api_key = api_key or credential_store.api_key("image_gen") or credential_store.api_key("dashscope_native")
        self.base_url = base_url or credential_store.base_url("image_gen")
        self.model = model or credential_store.model("image_gen")
        
        if not self.api_key:
             logging.warning("IMAGE_GEN_API_KEY is not set in config.")
        if self.base_url:
            dashscope.base_http_api_url = self.base_url
        
        # 独立的 LLM Client (用于处理 prompt 优化等文本任务)
        # 避免混用 Image Gen 的 API Key/Base URL (因为通常不互通)
        import httpx
        text_profile = get_model_config("moonshot")
        self.text_model = text_profile["model"] if settings.is_local_mode else None
        self.llm_client = OpenAI(
            api_key=text_profile["api_key"],
            base_url=text_profile["base_url"],
            timeout=httpx.Timeout(60.0, connect=10.0)
        )
    
    def generate(
        self,
        plan: ContentPlan,
        gen_input: GenerationInput,
        max_workers: int = 1,
        save_callback = None,
    ) -> List[GeneratedImage]:
        """
        Generate images from ContentPlan.
        
        Args:
            plan: ContentPlan from ContentPlanner
            gen_input: GenerationInput with config and origin
            max_workers: Maximum parallel workers for slides (3rd+ slides run in parallel)
            save_callback: Optional callback function(generated_image, index, total) called after each image
        
        Returns:
            List of GeneratedImage (1 for poster, N for slides)
        """
        figure_images = self._load_figure_images(plan, gen_input.origin.base_path)
        style_name = gen_input.config.style.value
        custom_style = gen_input.config.custom_style
        
        # Process custom style with LLM if needed
        processed_style = None
        if style_name == "custom" and custom_style:
            processed_style = process_custom_style(self.llm_client, custom_style, model=self.text_model)
            if not processed_style.valid:
                raise ValueError(f"Invalid custom style: {processed_style.error}")
        
        all_sections_md = self._format_sections_markdown(plan)
        all_images = self._filter_images(plan.sections, figure_images)
        
        if plan.output_type == "poster":
            result = self._generate_poster(style_name, processed_style, all_sections_md, all_images)
            if save_callback and result:
                save_callback(result[0], 0, 1)
            return result
        else:
            # qwen-image-max 在高并发下更容易触发 429，PPT 场景默认串行出图
            if str(self.model).lower() == "qwen-image-max" and max_workers > 1:
                logging.getLogger(__name__).info(
                    "qwen-image-max detected, reduce max_workers %s -> 1 to avoid rate limit",
                    max_workers,
                )
                max_workers = 1
            return self._generate_slides(plan, style_name, processed_style, all_sections_md, figure_images, max_workers, save_callback)
    
    def _generate_poster(self, style_name, processed_style: Optional[ProcessedStyle], sections_md, images) -> List[GeneratedImage]:
        """Generate 1 poster image."""
        prompt = self._build_poster_prompt(
            format_prefix=FORMAT_POSTER,
            style_name=style_name,
            processed_style=processed_style,
            sections_md=sections_md,
        )
        
        image_data, mime_type = self._call_model(prompt, images)
        return [GeneratedImage(section_id="poster", image_data=image_data, mime_type=mime_type)]
    
    def _generate_slides(self, plan, style_name, processed_style: Optional[ProcessedStyle], all_sections_md, figure_images, max_workers: int, save_callback=None) -> List[GeneratedImage]:
        """Generate N slide images (slides 1-2 sequential, 3+ parallel)."""
        results = []
        total = len(plan.sections)
        
        # Select layout rules based on style
        if style_name == "custom":
            layouts = SLIDE_LAYOUTS_DEFAULT
        elif style_name == "doraemon":
            layouts = SLIDE_LAYOUTS_DORAEMON
        else:
            layouts = SLIDE_LAYOUTS_ACADEMIC
        
        style_ref_image = None  # 第1页作为后续所有页的风格参考
        
        # 只生成第1页 (串行) - 建立风格参考
        if total >= 1:
            i = 0
            section = plan.sections[0]
            section_md = self._format_single_section_markdown(section, plan)
            layout_rule = layouts.get(section.section_type, layouts["content"])
            
            prompt = self._build_slide_prompt(
                style_name=style_name,
                processed_style=processed_style,
                sections_md=section_md,
                layout_rule=layout_rule,
                slide_info=f"Slide 1 of {total}",
                context_md=all_sections_md,
            )
            
            section_images = self._filter_images([section], figure_images)
            image_data, mime_type = self._call_model(prompt, section_images)
            
            # 保存第1页作为风格参考
            style_ref_image = {
                "figure_id": "Reference Slide",
                "caption": "STRICTLY MAINTAIN: same background color, same accent color, same font style, same chart/icon style. Keep visual consistency.",
                "base64": base64.b64encode(image_data).decode("utf-8"),
                "mime_type": mime_type,
            }
            
            generated_img = GeneratedImage(section_id=section.id, image_data=image_data, mime_type=mime_type)
            results.append(generated_img)
            
            if save_callback:
                save_callback(generated_img, 0, total)
        
        # 第2页起全部并行生成
        if total > 1:
            results_dict = {}
            
            def generate_single(i, section):
                section_md = self._format_single_section_markdown(section, plan)
                layout_rule = layouts.get(section.section_type, layouts["content"])
                
                prompt = self._build_slide_prompt(
                    style_name=style_name,
                    processed_style=processed_style,
                    sections_md=section_md,
                    layout_rule=layout_rule,
                    slide_info=f"Slide {i+1} of {total}",
                    context_md=all_sections_md,
                )
                
                section_images = self._filter_images([section], figure_images)
                reference_images = [style_ref_image] if style_ref_image else []
                reference_images.extend(section_images)
                
                image_data, mime_type = self._call_model(prompt, reference_images)
                return i, GeneratedImage(section_id=section.id, image_data=image_data, mime_type=mime_type)
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(generate_single, i, plan.sections[i]): i
                    for i in range(1, total)  # 从第2页开始 (index=1)
                }
                
                for future in as_completed(futures):
                    idx, generated_img = future.result()
                    results_dict[idx] = generated_img
                    
                    if save_callback:
                        save_callback(generated_img, idx, total)
            
            # 按顺序添加到结果
            for i in range(1, total):
                results.append(results_dict[i])
        
        return results
    
    def _format_custom_style_for_poster(self, ps: ProcessedStyle) -> str:
        """Format ProcessedStyle into style hints string for poster."""
        parts = [
            ps.style_name + ".",
            "English text only.",
            "Use ROUNDED sans-serif fonts for ALL text.",
            "Characters should react to or interact with the content, with appropriate poses/actions and sizes - not just decoration."
            f"LIMITED COLOR PALETTE (3-4 colors max): {ps.color_tone}.",
            POSTER_COMMON_STYLE_RULES,
        ]
        if ps.special_elements:
            parts.append(ps.special_elements + ".")
        return " ".join(parts)
    
    def _format_custom_style_for_slide(self, ps: ProcessedStyle) -> str:
        """Format ProcessedStyle into style hints string for slide."""
        parts = [
            ps.style_name + ".",
            "English text only.",
            "Use ROUNDED sans-serif fonts for ALL text.",
            "Characters should react to or interact with the content, with appropriate poses/actions and sizes - not just decoration.",
            f"LIMITED COLOR PALETTE (3-4 colors max): {ps.color_tone}.",
            SLIDE_COMMON_STYLE_RULES,
        ]
        if ps.special_elements:
            parts.append(ps.special_elements + ".")
        return " ".join(parts)
    
    def _build_poster_prompt(self, format_prefix, style_name, processed_style: Optional[ProcessedStyle], sections_md) -> str:
        """Build prompt for poster."""
        parts = [format_prefix]
        
        if style_name == "custom" and processed_style:
            parts.append(f"Style: {self._format_custom_style_for_poster(processed_style)}")
            if processed_style.decorations:
                parts.append(f"Decorations: {processed_style.decorations}")
        else:
            parts.append(POSTER_STYLE_HINTS.get(style_name, POSTER_STYLE_HINTS["academic"]))
        
        parts.append(VISUALIZATION_HINTS)
        parts.append(POSTER_FIGURE_HINT)
        parts.append(f"---\nContent:\n{sections_md}")
        
        return "\n\n".join(parts)
    
    def _build_slide_prompt(self, style_name, processed_style: Optional[ProcessedStyle], sections_md, layout_rule, slide_info, context_md) -> str:
        """Build prompt for slide with layout rules and consistency."""
        parts = [FORMAT_SLIDE]
        
        if style_name == "custom" and processed_style:
            parts.append(f"Style: {self._format_custom_style_for_slide(processed_style)}")
        else:
            parts.append(SLIDE_STYLE_HINTS.get(style_name, SLIDE_STYLE_HINTS["academic"]))
        
        # Add layout rule, then decorations if custom style
        parts.append(layout_rule)
        if style_name == "custom" and processed_style and processed_style.decorations:
            parts.append(f"Decorations: {processed_style.decorations}")
        
        parts.append(VISUALIZATION_HINTS)
        parts.append(CONSISTENCY_HINT)
        parts.append(SLIDE_FIGURE_HINT)
        
        parts.append(slide_info)
        parts.append(f"---\nFull presentation context:\n{context_md}")
        parts.append(f"---\nThis slide content:\n{sections_md}")
        
        return "\n\n".join(parts)
    
    def _format_sections_markdown(self, plan: ContentPlan) -> str:
        """Format all sections as markdown."""
        parts = []
        for section in plan.sections:
            parts.append(self._format_single_section_markdown(section, plan))
        return "\n\n---\n\n".join(parts)
    
    def _format_single_section_markdown(self, section: Section, plan: ContentPlan) -> str:
        """Format a single section as markdown."""
        lines = [f"## {section.title}", "", section.content]
        
        for ref in section.tables:
            table = plan.tables_index.get(ref.table_id)
            if table:
                focus_str = f" (focus: {ref.focus})" if ref.focus else ""
                lines.append("")
                lines.append(f"**{ref.table_id}**{focus_str}:")
                lines.append(ref.extract if ref.extract else table.html_content)
        
        for ref in section.figures:
            fig = plan.figures_index.get(ref.figure_id)
            if fig:
                focus_str = f" (focus: {ref.focus})" if ref.focus else ""
                caption = f": {fig.caption}" if fig.caption else ""
                lines.append("")
                lines.append(f"**{ref.figure_id}**{focus_str}{caption}")
                lines.append("[Image attached]")
        
        return "\n".join(lines)
    
    def _load_figure_images(self, plan: ContentPlan, base_path: str) -> List[dict]:
        """Load figure images as base64."""
        images = []
        mime_map = {
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"
        }
        
        for fig_id, fig in plan.figures_index.items():
            if base_path:
                img_path = Path(base_path) / fig.image_path
            else:
                img_path = Path(fig.image_path)
            
            if not img_path.exists():
                continue
            
            mime_type = mime_map.get(img_path.suffix.lower(), "image/jpeg")
            
            try:
                with open(img_path, "rb") as f:
                    img_data = base64.b64encode(f.read()).decode("utf-8")
                images.append({
                    "figure_id": fig_id,
                    "caption": fig.caption,
                    "base64": img_data,
                    "mime_type": mime_type,
                })
            except Exception:
                continue
        
        return images
    
    def _filter_images(self, sections: List[Section], figure_images: List[dict]) -> List[dict]:
        """Filter images used in given sections."""
        used_ids = set()
        for section in sections:
            for ref in section.figures:
                used_ids.add(ref.figure_id)
        return [img for img in figure_images if img.get("figure_id") in used_ids]
    
    def _call_model(self, prompt: str, reference_images: List[dict]) -> tuple:
        """Call qwen-image-max with retry logic."""
        logger = logging.getLogger(__name__)

        def _safe_get(container, key, default=None):
            if container is None:
                return default
            if isinstance(container, dict):
                return container.get(key, default)
            try:
                return getattr(container, key)
            except (AttributeError, KeyError, TypeError):
                return default

        if self.base_url:
            dashscope.base_http_api_url = self.base_url

        content = [{"text": prompt}]
        
        # Add each reference image with label
        for img in reference_images:
            if img.get("base64") and img.get("mime_type"):
                fig_id = img.get("figure_id", "Figure")
                caption = img.get("caption", "")
                label = f"[{fig_id}]: {caption}" if caption else f"[{fig_id}]"
                content.append({"text": label})
                content.append({"image": f"data:{img['mime_type']};base64,{img['base64']}"})
        
        # Retry logic for API calls
        max_retries = 5
        retry_delay = 2  # seconds
        last_error_detail = "unknown"

        def _is_rate_limited(text: str) -> bool:
            t = (text or "").lower()
            return (
                "throttling.ratequota" in t
                or "rate limit exceeded" in t
                or "status=429" in t
                or " 429" in t
            )

        def _sleep_before_retry(attempt_index: int, error_text: str) -> None:
            if _is_rate_limited(error_text):
                base = min(60, 8 * (2 ** attempt_index))
                jitter = random.uniform(0.0, 1.5)
                delay = base + jitter
                logger.warning(
                    "Rate limit detected, backoff %.1fs before retry (%s/%s)",
                    delay,
                    attempt_index + 1,
                    max_retries,
                )
                time.sleep(delay)
                return
            time.sleep(retry_delay * (attempt_index + 1))
        
        current_content = content
        
        for attempt in range(max_retries):
            try:
                text_len = sum(len(c.get("text", "")) for c in current_content)
                logger.info(f"Calling image generation API (attempt {attempt + 1}/{max_retries}). Prompt length: {text_len} chars")

                response = MultiModalConversation.call(
                    api_key=self.api_key,
                    model=self.model,
                    messages=[{"role": "user", "content": current_content}],
                    result_format="message",
                    stream=False,
                    watermark=False,
                    prompt_extend=True,
                )

                if response is None:
                    error_msg = "API returned None response - possible rate limit or API error"
                    last_error_detail = error_msg
                    logger.warning(f"{error_msg} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay * (attempt + 1))
                        continue
                    raise RuntimeError(error_msg)

                if getattr(response, "status_code", None) != 200:
                    error_msg = f"DashScope error: status={getattr(response, 'status_code', None)}, code={getattr(response, 'code', '')}, message={getattr(response, 'message', '')}"
                    last_error_detail = error_msg
                    logger.warning(f"{error_msg} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        # 退避并移除参考图重试
                        current_content = [{"text": prompt}]
                        _sleep_before_retry(attempt, error_msg)
                        continue
                    raise RuntimeError(error_msg)

                logger.info(f"✅ Image API call successful (attempt {attempt + 1})")

                output = _safe_get(response, "output", None)
                image_url = None

                # 1) 官方推荐结构：output.results[].url
                results = _safe_get(output, "results", None)
                if isinstance(results, list):
                    for item in results:
                        item_url = _safe_get(item, "url", None)
                        if item_url:
                            image_url = item_url
                            break

                # 2) 兼容结构：output.choices[].message.content[].image
                if not image_url:
                    choices = _safe_get(output, "choices", None)
                    if isinstance(choices, list) and choices:
                        first = choices[0]
                        message = _safe_get(first, "message", {})
                        message_content = _safe_get(message, "content", [])
                        if not isinstance(message_content, list):
                            message_content = []
                        for item in message_content:
                            item_image = _safe_get(item, "image", None)
                            if item_image:
                                image_url = item_image
                                break

                # 3) 最后兜底：若 output 本身可序列化，尝试正则提取 URL
                if not image_url and output is not None:
                    try:
                        import re
                        raw_output = json.dumps(output, ensure_ascii=False)
                        match = re.search(r'https?://[^\s"\\]+', raw_output)
                        if match:
                            image_url = match.group(0)
                    except Exception:
                        pass

                if image_url and image_url.startswith("http"):
                    logger.info(f"Downloading image from URL: {image_url}")
                    try:
                        img_resp = requests.get(image_url, timeout=60)
                        img_resp.raise_for_status()
                        content_type = img_resp.headers.get("content-type", "image/png")
                        return img_resp.content, content_type
                    except Exception as dl_err:
                        last_error_detail = f"Image download failed: {dl_err}"
                        logger.warning(f"{last_error_detail} (attempt {attempt + 1}/{max_retries})")
                        if attempt < max_retries - 1:
                            _sleep_before_retry(attempt, last_error_detail)
                            continue
                        raise RuntimeError(last_error_detail)

                error_msg = "Image generation failed - no image URL in response"
                last_error_detail = error_msg
                logger.warning(f"{error_msg} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    current_content = [{"text": prompt}]
                    _sleep_before_retry(attempt, error_msg)
                    continue
                raise RuntimeError(error_msg)
                
            except Exception as e:
                error_str = str(e)
                last_error_detail = error_str
                logger.error(f"Error in API call (attempt {attempt + 1}/{max_retries}): {error_str}")
                
                # Check for non-retryable errors (quota, auth, permissions)
                non_retryable_codes = ["insufficient_user_quota", "quota_exceeded", "invalid_api_key", "authentication_error"]
                is_non_retryable = any(code in error_str.lower() for code in non_retryable_codes) or "403" in error_str
                
                if is_non_retryable:
                    # Extract user-friendly message
                    if "insufficient_user_quota" in error_str or "quota" in error_str.lower():
                        user_msg = "API 余额不足，请充值后重试"
                    elif "invalid_api_key" in error_str or "authentication" in error_str.lower():
                        user_msg = "API 密钥无效，请检查配置"
                    else:
                        user_msg = f"API 权限错误: {error_str[:100]}"
                    logger.error(f"Non-retryable error detected, skipping retries: {user_msg}")
                    raise RuntimeError(user_msg)
                
                if "Empty Response" in error_str or "400" in error_str:
                    if attempt < max_retries - 1:
                        logger.info("Encountered Empty Response/400. Simplifying prompt for retry...")
                        current_content = [{"text": prompt}]
                
                if attempt < max_retries - 1:
                    _sleep_before_retry(attempt, error_str)
                    continue
                
                # If this was the last attempt, raise error
                if attempt == max_retries - 1:
                    raise RuntimeError(f"Image generation failed after all retry attempts. Last error: {last_error_detail}")
        
        raise RuntimeError(f"Image generation failed after all retry attempts. Last error: {last_error_detail}")


def save_images_as_pdf(images: List[GeneratedImage], output_path: str):
    """
    Save generated images as a single PDF file.
    
    Args:
        images: List of GeneratedImage from ImageGenerator.generate()
        output_path: Output PDF file path
    """
    from PIL import Image
    import io
    
    pdf_images = []
    
    for img in images:
        # Load image from bytes
        pil_img = Image.open(io.BytesIO(img.image_data))
        
        # Convert RGBA to RGB (PDF doesn't support alpha)
        if pil_img.mode == 'RGBA':
            pil_img = pil_img.convert('RGB')
        elif pil_img.mode != 'RGB':
            pil_img = pil_img.convert('RGB')
        
        pdf_images.append(pil_img)
    
    if pdf_images:
        # Save first image and append the rest
        pdf_images[0].save(
            output_path,
            save_all=True,
            append_images=pdf_images[1:] if len(pdf_images) > 1 else [],
            resolution=100.0,
        )
        logger.info(f"PDF saved: {output_path}")
