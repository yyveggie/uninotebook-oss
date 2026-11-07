"""
Mermaid Code Sanitizer (Simplified Version)

Purpose:
A lightweight cleaner for LLM-generated Mermaid code.
Due to the improved LLM prompt in `qa.py`, we now trust the model to generate correct syntax (e.g. quoted labels).
This module only handles basic character normalization and safety replacements.
"""

import re
from utils.logger import get_logger
logger = get_logger(__name__)

# Common problematic characters map
UNICODE_REPLACEMENTS = {
    '（': '(', '）': ')',
    '【': '[', '】': ']',
    '：': ':', '；': ';',
    '，': ',', '。': '.',
    '“': '"', '”': '"',
    '‘': "'", '’': "'",
    '—': '-', '–': '-',
    '|': ' ', # Pipe breaks graph edges sometimes if unquoted
}

def sanitize_mermaid_code(code: str) -> str:
    """
    Clean up Mermaid code by normalizing characters and removing basic markdown.
    Does NOT attempt to parse or reconstruct the graph logic.
    """
    if not code:
        return code

    # 1. Normalize unicode punctuation to ASCII
    for old, new in UNICODE_REPLACEMENTS.items():
        code = code.replace(old, new)

    # 2. Remove Markdown formatting (bold/italic) from labels
    # Global replace is safe enough for standard mermaid usage
    # **text** -> text
    code = re.sub(r'\*\*([^*]+)\*\*', r'\1', code)
    # *text* -> text
    code = re.sub(r'\*([^*]+)\*', r'\1', code)
    
    # 3. Remove backticks (code blocks inside labels break rendering)
    code = code.replace('`', '')

    logger.info(f"🧹 [Mermaid Sanitizer] Simple clean: {len(code)} chars processed")
    return code
