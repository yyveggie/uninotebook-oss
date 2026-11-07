"""
文档服务的工具函数
包含文件哈希、文本清理、LaTeX格式化等功能
"""
import re
import hashlib
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from utils.latex import fix_latex_format
from utils.logger import get_logger
logger = get_logger(__name__)


def calculate_file_hash(text: str) -> str:
    """
    计算文本内容的SHA256哈希值，用于识别相同/相似文档
    
    Args:
        text: 文档文本内容
        
    Returns:
        hash_value: SHA256哈希值（十六进制字符串）
    """
    # 标准化文本：移除多余空白、统一换行符
    normalized_text = ' '.join(text.split())
    # 计算SHA256哈希
    hash_object = hashlib.sha256(normalized_text.encode('utf-8'))
    return hash_object.hexdigest()


def clean_text_newlines(text: str) -> str:
    """
    清理文本中的非法字符，保留段落结构
    
    Args:
        text: 原始文本
        
    Returns:
        cleaned_text: 清理后的文本
    """
    if not text:
        return text
    
    # 1. 清理NULL字符和控制字符（PostgreSQL不支持）
    text = text.replace('\x00', '')  # 移除NULL字符
    text = text.replace('\x0b', '\n')  # 垂直制表符 → 换行
    text = text.replace('\x0c', '\n')  # 换页符 → 换行
    text = text.replace('\r\n', '\n')  # Windows换行 → Unix换行
    text = text.replace('\r', '\n')    # Mac换行 → Unix换行
    
    # 2. 保留段落结构，只清理每行内部的多余空格
    lines = text.split('\n')
    cleaned_lines = []
    
    for line in lines:
        # 清理每行内部的多余空格，但保留换行符
        cleaned_line = ' '.join(line.split())
        cleaned_lines.append(cleaned_line)
    
    # 3. 重新拼接，保留换行符
    text = '\n'.join(cleaned_lines)
    
    # 4. 合并3个以上连续换行为2个（保持段落分隔清晰）
    text = re.sub(r'\n{3,}', '\n\n', text)
    
    return text


def repair_json_latex(json_str: str) -> str:
    """
    修复 JSON 字符串中常见的 LaTeX 转义错误
    用于在 json.loads 之前预处理 LLM 返回的字符串
    
    Args:
        json_str: 原始 JSON 字符串
        
    Returns:
        修复后的 JSON 字符串
    """
    # 移除 markdown 代码块标记
    if "```json" in json_str:
        json_str = json_str.replace("```json", "").replace("```", "")
    elif "```" in json_str:
        json_str = json_str.replace("```", "")
        
    # 修复常见的 LaTeX 括号转义丢失问题
    # JSON 标准要求反斜杠必须转义 (例如 "\\(")，但 LLM 常输出 "\("
    
    # 替换 \( 为 \\(
    json_str = json_str.replace(r'\(', r'\\(')
    # 替换 \) 为 \\)
    json_str = json_str.replace(r'\)', r'\\)')
    # 替换 \[ 为 \\[
    json_str = json_str.replace(r'\[', r'\\[')
    # 替换 \] 为 \\]
    json_str = json_str.replace(r'\]', r'\\]')
    
    return json_str


def remove_control_characters(s: str) -> str:
    """
    移除字符串中的非法 ASCII 控制字符，但保留换行符和制表符
    """
    # ASCII 0-31 是控制字符。
    # 我们要保留: 9 (TAB), 10 (LF), 13 (CR)
    # 所以我们要移除: 0-8, 11-12, 14-31
    return re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)


def parse_json_safely(json_str: str) -> Any:
    """
    安全解析 JSON 字符串，包含错误修复机制
    """
    # 1. 尝试修复 JSON 字符串
    cleaned_json_str = repair_json_latex(json_str)
    
    # 2. 移除非法控制字符
    cleaned_json_str = remove_control_characters(cleaned_json_str)
    
    data = None
    try:
        # strict=False 允许字符串中包含未转义的控制字符（如换行符）
        data = json.loads(cleaned_json_str, strict=False)
    except json.JSONDecodeError as e:
        logger.warning(f"⚠️ JSON解析失败，尝试激进修复: {e}")
        
        try:
            # 激进修复策略 1: 将所有未转义的 \ 替换为 \\ (排除已经转义的 \\)
            # 这通常用于修复 LaTeX 公式
            temp_str = cleaned_json_str.replace('\\', '\\\\').replace('\\\\\\\\', '\\\\')
            data = json.loads(temp_str, strict=False)
        except:
            try:
                # 激进修复策略 2: 处理未转义的换行符
                # 有些 LLM 会在 JSON 字符串值中直接换行，这是非法的
                # 我们尝试把这些换行符替换为 \n
                # 但要注意不要破坏 JSON 结构本身的格式化换行
                # 这是一个简单的尝试：把所有换行替换为空格（可能会破坏格式，但能救活解析）
                # 或者，更好的做法是依赖 strict=False，但如果还不行，可能是其他字符问题
                pass
                raise e
            except:
                logger.error(f"❌ 最终解析失败。原始内容片段: {json_str[:200]}...")
                raise e
    return data


def parse_and_fix_quiz_json(response_content: str) -> List[Dict[str, Any]]:
    """
    解析并修复 LLM 生成的测验 JSON 内容
    包含: 字符串清洗 -> JSON解析 -> LaTeX格式修复
    
    Args:
        response_content: LLM 返回的原始字符串
        
    Returns:
        处理好的测验题目列表
    """
    data = parse_json_safely(response_content)

    # 2. 验证结构
    if "quiz" not in data or not isinstance(data["quiz"], list):
            raise Exception("JSON结构不正确，缺少 'quiz' 列表")
    
    quiz_list = data["quiz"]
    
    # 3. 应用 fix_latex_format 进行内容美化
    for q in quiz_list:
        if "question" in q:
            q["question"] = fix_latex_format(q["question"])
        if "explanation" in q:
            q["explanation"] = fix_latex_format(q["explanation"])
        if "options" in q and isinstance(q["options"], list):
            q["options"] = [fix_latex_format(opt) for opt in q["options"]]
            
    return quiz_list


async def update_document_last_accessed(db: AsyncSession, document_id: int):
    """更新文档的最后访问时间"""
    try:
        from db_models import Document
        result = await db.execute(
            select(Document).filter(Document.id == document_id)
        )
        document = result.scalar_one_or_none()
        if document:
            document.last_accessed_at = datetime.now(timezone.utc)
            await db.commit()
    except Exception as e:
        logger.warning(f"⚠️ 更新 last_accessed_at 失败: {e}")


# ==================== 🆕 生成器上下文构建工具 ====================

def build_context_from_chunks(
    chunks: List[Dict[str, Any]],
    max_total_chars: int = 10000,
    max_chunk_chars: int = 1000,
    format_template: str = "[片段 {index}]\n{text}\n\n"
) -> tuple:
    """
    🆕 从检索 chunks 构建格式化的上下文字符串
    
    统一所有生成器（flashcards, quiz, highlights）的上下文构建逻辑，
    避免代码重复。
    
    Args:
        chunks: 检索到的 chunk 列表，每个 chunk 至少包含 'text' 字段
        max_total_chars: 总上下文最大字符数（默认 10000）
        max_chunk_chars: 单个 chunk 最大字符数（默认 1000，超过则截断）
        format_template: 格式化模板，支持 {index} 和 {text} 占位符
    
    Returns:
        tuple: (context_text, included_count, total_chars)
            - context_text: 格式化后的上下文字符串
            - included_count: 实际包含的 chunk 数量
            - total_chars: 实际总字符数
    
    Example:
        context, count, chars = build_context_from_chunks(
            chunks, 
            max_total_chars=12000, 
            max_chunk_chars=1200
        )
        logger.info(f"构建上下文: {count} 个片段, {chars} 字符")
    """
    if not chunks:
        return "", 0, 0
    
    context_parts = []
    current_chars = 0
    included_count = 0
    
    for i, chunk in enumerate(chunks):
        chunk_text = chunk.get('text', '')
        if not chunk_text:
            continue
        
        # 截断过长的单个 chunk
        if len(chunk_text) > max_chunk_chars:
            chunk_text = chunk_text[:max_chunk_chars] + "..."
        
        # 格式化
        entry = format_template.format(index=i + 1, text=chunk_text)
        
        # 检查是否超过总限制
        if current_chars + len(entry) > max_total_chars:
            break
        
        context_parts.append(entry)
        current_chars += len(entry)
        included_count += 1
    
    context_text = "".join(context_parts)
    return context_text, included_count, current_chars


def build_context_with_fallback(
    chunks: List[Dict[str, Any]],
    document_text: Optional[str],
    max_total_chars: int = 10000,
    short_doc_threshold: int = 5000
) -> tuple:
    """
    🆕 带回退逻辑的上下文构建
    
    如果文档较短（< short_doc_threshold），直接使用全文；
    否则使用检索到的 chunks。
    
    Args:
        chunks: 检索到的 chunk 列表
        document_text: 文档全文（用于短文档回退）
        max_total_chars: 总上下文最大字符数
        short_doc_threshold: 短文档阈值（低于此值使用全文）
    
    Returns:
        tuple: (context_text, source_info)
            - context_text: 上下文字符串
            - source_info: 来源说明（用于日志）
    """
    doc_length = len(document_text) if document_text else 0
    
    # 短文档：直接使用全文
    if document_text and doc_length < short_doc_threshold:
        logger.info(f"📄 文档较短 (<{short_doc_threshold}字)，直接使用全文作为上下文")
        return document_text[:max_total_chars], "文档完整内容"
    
    # 长文档：使用检索 chunks
    context_text, count, chars = build_context_from_chunks(
        chunks, 
        max_total_chars=max_total_chars,
        max_chunk_chars=1200
    )
    
    logger.info(f"📄 使用多路检索内容（{count}个片段，{chars}字符）")
    return context_text, f"文档核心内容（基于{count}个关键检索片段）"
