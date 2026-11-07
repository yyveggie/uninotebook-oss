from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from utils.logger import get_logger

logger = get_logger(__name__)

CREDENTIALS_NOT_CONFIGURED = "CREDENTIALS_NOT_CONFIGURED"


def error_detail(code: str, message: str, details: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    return payload


def raise_http_error(status_code: int, code: str, message: str, details: Any = None) -> None:
    """
    抛出统一格式的 HTTP 错误。

    特殊处理：若 `details` 里其实是各家 SDK 的「未配置 API Key」原始报错，
    改写为 503 + 可执行指引。原始文本（"The api_key client option must be set…"）
    对用户毫无意义，而这类失败恰恰是用户自己能解决的。

    放在这里而非各个路由，是因为全仓 250 处错误抛出都经由本函数——
    逐个路由打补丁必然会漏。
    """
    if status_code >= 500 and details is not None:
        from runtime.features import looks_like_missing_credentials, missing_credential_hint

        if looks_like_missing_credentials(Exception(str(details))):
            raise HTTPException(
                status_code=503,
                detail=error_detail(CREDENTIALS_NOT_CONFIGURED, missing_credential_hint()),
            )

        from config import settings

        if settings.is_saas_mode:
            # 5xx 的 details 绝大多数是 str(e)：可能带出 SQL、驱动报错、内部路径。
            # 多用户部署下只写日志、不回传客户端；本地版用户即运维者，保留以便排查。
            logger.error("[%s] %s: %s", code, message, details)
            details = None

    raise HTTPException(status_code=status_code, detail=error_detail(code, message, details))
