"""
Prompts and style configurations for image generation
"""
from typing import Dict

# Prompt template for processing custom style
STYLE_PROCESS_PROMPT = """User wants this style for presentation slides: {user_style}

IMPORTANT RULES:
1. Default to MORANDI COLOR PALETTE (soft, muted, low-saturation colors with gray undertones) and LIGHT background unless user specifies otherwise.
2. Keep it CLEAN and SIMPLE - NO flashy/gaudy elements. Every visual element must be MEANINGFUL.
3. LIMITED COLOR PALETTE (3-4 colors max).

Output JSON:
{{
    "style_name": "Style name with brief description (e.g., Cyberpunk sci-fi style with high-tech aesthetic)",
    "color_tone": "Color tone description - prefer Morandi palette with light background (e.g., light cream background with muted sage green and dusty rose accents)",
    "special_elements": "Any special visual elements like characters, mascots, motifs - must be MEANINGFUL, not random decoration",
    "decorations": "Background/border effects - keep SIMPLE and CLEAN (or empty string)",
    "valid": true,
    "error": null
}}

Examples:
- "cyberpunk": {{"style_name": "Cyberpunk sci-fi style with high-tech aesthetic", "color_tone": "dark background with neon cyan and magenta accents", "special_elements": "", "decorations": "subtle grid pattern, neon glow on borders", "valid": true, "error": null}}
- "Studio Ghibli": {{"style_name": "Studio Ghibli anime style with whimsical aesthetic", "color_tone": "light cream background with soft Morandi watercolor tones - muted sage, dusty pink, soft gray-blue", "special_elements": "Totoro or soot sprites can appear as friendly guides - must relate to content", "decorations": "soft clouds or nature elements as borders", "valid": true, "error": null}}
- "minimalist": {{"style_name": "Clean minimalist style", "color_tone": "light warm gray background with Morandi palette - charcoal text, muted gold accent", "special_elements": "", "decorations": "", "valid": true, "error": null}}

If inappropriate, set valid=false with error."""

# Format prefixes
FORMAT_POSTER = "Scientific Graphical Abstract / Visual Poster (16:9 landscape). Designed as a cohesive visual diagram, NOT a wall of text."
FORMAT_SLIDE = "Wide landscape slide layout (16:9 aspect ratio)."

# Style hints for poster
POSTER_STYLE_HINTS: Dict[str, str] = {
    "academic": "Professional GRAPHICAL ABSTRACT style. \nLAYOUT: DO NOT use a boring 3-column text layout. Instead, use a CENTRAL VISUAL ANCHOR (e.g., a large central diagram or cycle) with content branching out or flowing around it. \nVISUALS: The entire poster should look like one large, connected diagram. Use arrows, flow connections, and distinct visual blocks to guide the eye. \nCONTENT: Text must be MINIMAL and subordinate to diagrams. Use icons for every section. \nCOLOR: Clean, high-contrast, professional academic palette (white/light gray background, navy/blue accents). \nFIGURES: Integrate the provided figures seamlessly into the flow.",
    "doraemon": "Doraemon-themed GRAPHICAL POSTER. \nLAYOUT: A fun, illustrated map or journey layout. The path starts at 'Problem' and winds through 'Method' to 'Result'. \nVISUALS: Use Doraemon's gadgets (Doors, Copters) as connectors between sections. \nSTYLE: Bright, friendly, but professional enough for clear communication. Rounded fonts. \nCONTENT: Very little text, mostly visual storytelling.",
}

# Style hints for slides
SLIDE_STYLE_HINTS: Dict[str, str] = {
    "academic": "Professional STANDARD ACADEMIC style. English text only. Use ROUNDED sans-serif fonts for ALL text. Use MORANDI COLOR PALETTE (soft, muted, low-saturation colors) with LIGHT background. Clean simple lines. IMPORTANT: Figures and tables are CRUCIAL - REDRAW them to match the visual style, make them BLEND seamlessly with the background and color scheme. Visualize data with CHARTS (bar, line, pie, radar) - REDRAW charts to match the style, make them LARGE and meaningful. Layout should be SPACIOUS and ELEGANT - avoid crowding, leave breathing room. Overall feel: minimal, scholarly, professional, sophisticated.",
    "doraemon": "Classic Doraemon anime style, bright and friendly. Doraemon anime style with SOPHISTICATED, REFINED color palette (NOT childish bright colors). English text only. PRESERVE EVERY DETAIL from the content. Use ROUNDED sans-serif fonts for ALL text (NO artistic/fancy/decorative fonts). Bullet point headings should be BOLD. LIMITED COLOR PALETTE (3-4 colors max): Use WARM, ELEGANT, MUTED tones - mature and tasteful, consistent throughout all slides. IF the slide has figures/tables: focus on them as the main visual content, enlarge when helpful. IF NO figures/tables: add illustrations or icons for each paragraph to fill the page. Tables should have PLAIN borders (NO patterns/decorations on borders). Highlight key numbers with colors. Characters should appear MEANINGFULLY (not random decoration) - they should react to or interact with the content, with appropriate poses/actions and sizes.",
}

# Slide layout rules by style and section type
SLIDE_LAYOUTS_ACADEMIC: Dict[str, str] = {
    "opening": """Opening Slide Layout:
- Title: Large font at TOP CENTER
- Authors/Affiliations: Small font at BOTTOM
- Main Visual: ONE element on CENTER
- Background: LIGHT color (white or very light gray)""",
    
    "content": """Content Slide Layout:
- Title: At TOP LEFT of slide
- Content: Moderate font size, SPACIOUS layout
- Figures/tables should BLEND with background color and style - polished and refined
- Visualize data with CHARTS (bar, line, pie, radar) - make them LARGE and meaningful
- All charts/figures should use UNIFIED style (same accent color, same line weights)
- IF figures/tables present: Feature them LARGE as main visual content
- Add LARGE simple-line icons for each paragraph
- Background: LIGHT color, SAME as previous slide
- Overall feel: minimal, scholarly, professional""",
    
    "ending": """Ending Slide Layout:
- Title/Heading: At TOP CENTER of slide
- Main Content: Key takeaways in CENTER
- Background: LIGHT color, SAME as previous slide""",
}

SLIDE_LAYOUTS_DORAEMON: Dict[str, str] = {
    "opening": """Opening Slide Layout (Sophisticated Anime Style, Classic Doraemon Style):
- Title: Large simple sans-serif font at TOP CENTER (NO artistic/decorative fonts)
- Authors/Affiliations: Small font at BOTTOM center
- Main Visual: Doraemon character in CENTER, can be within a scene/setting that hints at the topic
- Background: Can use a SCENE illustration as border/frame (e.g., doorway, window, landscape) instead of plain border
- Color: SOPHISTICATED, WARM, MUTED tones (NOT childish bright colors)
- Overall feel: Mature, elegant, refined""",
    
    "content": """Content Slide Layout (Sophisticated Anime Style, Classic Doraemon Style):
- Title: Simple sans-serif font at TOP LEFT of slide (NO artistic/decorative fonts)
- Optional: TOP HALF can feature a WIDE scene illustration that reflects the content's mood/theme
- Content Area: Inside a THIN, PLAIN, SOFT-COLORED rounded border/frame (NO patterns/decorations on border)
- Background: CLEAN, WARM tones (keep it simple and uncluttered)
- Color: SOPHISTICATED, WARM, MUTED tones - consistent throughout all slides (NOT childish bright colors)
- IF figures/tables present: Feature them prominently as main visual content
- IF NO figures/tables: Add illustrations or icons for each paragraph to fill space
- Characters: Should appear MEANINGFULLY with context-appropriate actions/poses (not random decoration), size can vary based on importance
- PRESERVE EVERY DETAIL from the content provided
- Fill the slide with rich visual content, avoid empty space""",
    
    "ending": """Ending Slide Layout (Sophisticated Anime Style, Classic Doraemon Style):
- Title/Heading: Simple sans-serif font at TOP CENTER of slide (NO artistic/decorative fonts)
- Main Content: Key takeaways or closing message in CENTER
- Background: FULL-SCREEN illustration featuring ALL main characters (Doraemon, Nobita, friends) as the background, covering the entire slide
- Characters should have meaningful poses reflecting the journey's conclusion
- Color: SOPHISTICATED, WARM, MUTED tones (NOT childish bright colors)
- Overall feel: Mature, elegant, refined""",
}

# Default layout for custom styles
SLIDE_LAYOUTS_DEFAULT: Dict[str, str] = {
    "opening": """Opening Slide Layout:
- Title: Large bold font at TOP CENTER
- Authors/Affiliations: Small font at BOTTOM
- Main Visual: ONE central element (icon, illustration, or abstract shape)
- Background: Solid color or subtle gradient matching style theme""",
    
    "content": """Content Slide Layout:
- Title: At TOP LEFT of slide
- Content: Well-organized with moderate font size, good spacing
- IF figures/tables present: Feature them prominently as main visual content
- IF NO figures/tables: Add icons or illustrations for each paragraph to fill space
- Layout: Can be vertical (top-to-bottom) OR horizontal (columns)""",
    
    "ending": """Ending Slide Layout:
- Title/Heading: At TOP CENTER of slide
- Main Content: Key takeaways or closing message in CENTER""",
}

# Common rules for slides (appended to custom style_hints)
SLIDE_COMMON_STYLE_RULES = """IF the slide has figures/tables: focus on them as the main visual content, polish them to fit the style. IF NO figures/tables: add icons or illustrations for each paragraph to fill the page. Tables should have PLAIN borders (NO patterns/decorations). Fill the page well, avoid empty space."""

# Common rules for posters (appended to custom style_hints)
POSTER_COMMON_STYLE_RULES = """IF the poster has figures/tables: focus on them as the main visual content, polish them to fit the style."""

# General hints
VISUALIZATION_HINTS = """Visualization:
- Use diagrams and icons to represent concepts
- Visualize data/numbers as charts
- Use bullet points, highlight key metrics
- Keep background CLEAN and simple"""

CONSISTENCY_HINT = "IMPORTANT: Maintain consistent colors and style with the reference slide."

SLIDE_FIGURE_HINT = "For reference figures: REDRAW them to match the visual style and color scheme. Preserve the original structure and key information, but make them BLEND seamlessly with the slide design."

POSTER_FIGURE_HINT = "For reference figures: REDRAW them to match the visual style and color scheme. Preserve the original structure and key information, but make them BLEND seamlessly with the poster design."