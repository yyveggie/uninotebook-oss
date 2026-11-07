"""
文章润色路由

仅支持学术模式。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

import auth
import db_models
from credit_service import CreditCost, CreditService, InsufficientCreditsError
from config import settings
from database import get_db
from llm_service import get_model_config
from routes._errors import raise_http_error
from routes.error_codes import ErrorCode

router = APIRouter()
PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
HUMANIZER_PROMPT_FILE = PROMPTS_DIR / "humanizer.md"
MIMIC_POLISH_PROMPT_FILE = PROMPTS_DIR / "polish_mimic.md"
HUMANIZE_MAX_TOKENS = 16000


class PolishRequest(BaseModel):
    source_text: str = Field(..., min_length=10, description="待润色文本")
    mode: Literal["academic"] = Field(default="academic", description="润色模式")
    task_type: Literal["mimic", "humanize"] = Field(default="humanize", description="任务类型：仿写或去 AI 味")
    reference_text: str = Field(default="", description="可选参考文本")


class PolishResult(BaseModel):
    rewritten_text: str
    mode: Literal["academic"]


def _detect_language(text: str) -> str:
    if not text:
        return "zh"
    chinese_chars = len([char for char in text if '\u4e00' <= char <= '\u9fff'])
    english_words = len([word for word in text.split() if any('a' <= ch.lower() <= 'z' for ch in word)])
    total_chars = len(text.replace(' ', '').replace('\n', ''))
    if total_chars == 0:
        return "zh"
    if chinese_chars / total_chars > 0.25:
        return "zh"
    if english_words > 10:
        return "en"
    return "zh"


def _estimate_token_count(text: str) -> int:
    if not text:
        return 0

    chinese_chars = len([char for char in text if '\u4e00' <= char <= '\u9fff'])
    english_words = len([word for word in text.split() if any('a' <= ch.lower() <= 'z' for ch in word)])
    total_chars = len(text)

    estimated = chinese_chars + english_words + max((total_chars - chinese_chars) // 4, 0)
    return max(estimated, 1)


def _language_instruction(detected_language: str) -> str:
    if detected_language == "en":
        return (
            "Output language rule: All output fields must be written in English. "
            "Do NOT output any Chinese."
        )
    return "输出语言规则：所有字段都必须与输入文本同语种输出，禁止跨语种翻译。"


def _mimic_polish_prompt(mode: str, detected_language: str) -> str:
    lang_instruction = _language_instruction(detected_language)
    mode_rules = ""
    template = _load_mimic_polish_prompt_template()
    return _render_prompt_template(
        template,
        mode_rules=mode_rules,
        lang_instruction=lang_instruction,
    )


def _render_prompt_template(template: str, *, mode_rules: str, lang_instruction: str) -> str:
    return (
        template
        .replace("__SCENARIO_TYPE__", "")
        .replace("__MODE_RULES__", mode_rules.strip())
        .replace("__LANG_INSTRUCTION__", lang_instruction)
        .strip()
    )


def _humanize_prompt(mode: str, detected_language: str) -> str:
    lang_instruction = _language_instruction(detected_language)
    # scene = "学术写作" # Removed per user request
    template = _load_humanizer_prompt_template()

    return f"""
{template}

---

## Runtime Constraints

- 语言约束：{lang_instruction}
- 严格保持原文事实、立场和结论，不得新增或虚构信息。
- 输出必须是严格 JSON（仅 rewritten_text），不得附加解释。
""".strip()


@lru_cache(maxsize=1)
def _load_humanizer_prompt_template() -> str:
    if HUMANIZER_PROMPT_FILE.exists():
        return HUMANIZER_PROMPT_FILE.read_text(encoding="utf-8").strip()

    return """
You are a writing editor that removes signs of AI-generated text and rewrites content to sound natural and human.

Rules:
1) Preserve original meaning and facts.
2) Remove template-like, promotional, and vague wording.
3) Keep coherent structure and natural rhythm.

Return strict JSON only:
{
  "rewritten_text": "..."
}
""".strip()


@lru_cache(maxsize=1)
def _load_mimic_polish_prompt_template() -> str:
    if MIMIC_POLISH_PROMPT_FILE.exists():
        return MIMIC_POLISH_PROMPT_FILE.read_text(encoding="utf-8").strip()

    return """
你是一位经验丰富的__SCENARIO_TYPE__润色大师。当前任务是【模仿润色】。

__MODE_RULES__

硬性约束：
1) 不新增事实、数据、引用或结论。
2) 语言约束：__LANG_INSTRUCTION__

仅返回严格 JSON：
{
    "rewritten_text": "..."
}
""".strip()


def _build_system_prompt(task_type: str, mode: str, detected_language: str) -> str:
    if task_type == "mimic":
        return _mimic_polish_prompt(mode, detected_language)
    return _humanize_prompt(mode, detected_language)


def _extract_text(parsed: dict[str, Any]) -> str:
    rewritten_text = (
        parsed.get("rewritten_text")
        or parsed.get("优化后的内容")
        or parsed.get("改写后的文本")
        or parsed.get("content")
        or parsed.get("text")
        or ""
    )
    return str(rewritten_text).strip()


async def _call_model(request: PolishRequest) -> PolishResult:
    detected_language = _detect_language(request.source_text)
    use_reference = request.task_type == "mimic"

    if use_reference and not (request.reference_text and request.reference_text.strip()):
        raise_http_error(status_code=400, code=ErrorCode.POLISH_400_MESSAGE, message="仿写模式需要参考文本")

    if request.task_type == "humanize":
        input_tokens = _estimate_token_count(request.source_text)
        if input_tokens > HUMANIZE_MAX_TOKENS:
            raise_http_error(
                status_code=400,
                code=ErrorCode.POLISH_400_MESSAGE,
                message=f"去 AI 味模式输入最多支持 {HUMANIZE_MAX_TOKENS} token（当前估算 {input_tokens}）",
            )

    system_prompt = _build_system_prompt(request.task_type, request.mode, detected_language)

    user_prompt = (
        f"【参考文本】\n{request.reference_text.strip()}\n\n【待润色文本】\n{request.source_text.strip()}"
        if use_reference
        else f"【待去 AI 味文本】\n{request.source_text.strip()}"
    )

    output_max_tokens = HUMANIZE_MAX_TOKENS if request.task_type == "humanize" else min(settings.openai_max_output_tokens, 8000)

    from llm_service.llm_config import FEATURE_MODEL_MAP, effective_temperature

    polish_config = get_model_config(FEATURE_MODEL_MAP["polish"])

    payload = {
        "model": "",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        # 改写需要一定发散度，不沿用该配置为结构化输出准备的低温度；
        # 但部分模型只接受固定温度（如 Kimi K2.6 思考模式必须为 1.0），以模型规则为准
        "temperature": effective_temperature(polish_config, 0.6),
        "max_tokens": output_max_tokens,
        "response_format": {"type": "json_object"},
    }
    # 直接调 HTTP 接口时需自行带上思考开关：JSON 输出在思考模式下不可用
    payload.update((polish_config.get("model_kwargs") or {}).get("extra_body") or {})

    api_key = polish_config.get("api_key")
    if not api_key:
        raise_http_error(status_code=500, code=ErrorCode.POLISH_500_MESSAGE, message="润色模型未配置 API Key")

    payload["model"] = polish_config["model"]

    base_url = (polish_config.get("base_url") or settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=30.0, read=180.0, write=30.0, pool=30.0)) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.ReadTimeout:
        raise_http_error(status_code=504, code=ErrorCode.POLISH_504_MESSAGE, message="润色请求超时，请重试或缩短输入文本")
    except httpx.HTTPError as exc:
        raise_http_error(status_code=502, code=ErrorCode.POLISH_502_EXCEPTION, message="润色模型网络异常", details=str(exc))

    if response.status_code >= 400:
        detail = response.text[:300]
        raise_http_error(status_code=502, code=ErrorCode.POLISH_502_EXCEPTION, message="润色模型调用失败", details=str(detail))

    try:
        data = response.json()
        raw_content = data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise_http_error(status_code=500, code=ErrorCode.POLISH_500_EXCEPTION, message="模型响应格式错误", details=str(exc))

    try:
        parsed = json.loads(raw_content)
    except json.JSONDecodeError:
        parsed = {"rewritten_text": raw_content}

    rewritten_text = _extract_text(parsed)
    if not rewritten_text:
        raise_http_error(status_code=500, code=ErrorCode.POLISH_500_MESSAGE, message="模型未返回有效润色结果")

    return PolishResult(
        rewritten_text=rewritten_text,
        mode=request.mode,
    )


@router.post("/api/polish/rewrite", tags=["文章润色"])
async def rewrite_polish(
    request: PolishRequest,
    db: AsyncSession = Depends(get_db),
    current_user: db_models.User = Depends(auth.get_current_user),
):
    credit_service = CreditService(db)

    balance = await credit_service.get_balance(current_user.id)
    if balance < CreditCost.POLISH:
        raise HTTPException(
            status_code=402,
            detail=f"积分不足：需要 {CreditCost.POLISH} 积分，当前余额 {balance} 积分",
        )

    result = await _call_model(request)

    try:
        await credit_service.deduct_for_polish(current_user.id, request.mode)
        await db.commit()
    except InsufficientCreditsError as e:
        raise_http_error(status_code=402, code=ErrorCode.POLISH_402_EXCEPTION, message="请求失败", details=e.message)

    return {
        "success": True,
        "message": "处理完成",
        "credits_deducted": CreditCost.POLISH,
        "data": result.model_dump(),
    }
