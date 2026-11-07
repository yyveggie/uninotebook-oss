from __future__ import annotations

import logging
import os
import sys
from typing import Any, Optional

_configured = False
_legacy_deprecation_warned = False


def _is_development_env() -> bool:
    env = (os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development").strip().lower()
    return env in {"dev", "development", "local"}


def _warn_legacy_deprecated_once() -> None:
    global _legacy_deprecation_warned
    if _legacy_deprecation_warned or not _is_development_env():
        return

    _legacy_deprecation_warned = True
    get_logger("legacy-compat").warning(
        "DEPRECATION: `log_legacy` / `legacy_print` are compatibility shims and will be removed in a future release. "
        "Please migrate callsites to explicit logger.info/warning/error/exception."
    )


def configure_logging(level: Optional[str] = None) -> None:
    """Configure root logging once for the whole backend process."""
    global _configured
    if _configured:
        return

    log_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, log_level, logging.INFO))

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Ensures base logging is configured."""
    configure_logging()
    return logging.getLogger(name)


def _join_message(args: tuple[Any, ...]) -> str:
    return " ".join(str(arg) for arg in args)


def log_info(*args: Any, logger_name: Optional[str] = None) -> None:
    logger = get_logger(logger_name or "app")
    logger.info(_join_message(args))


def log_warning(*args: Any, logger_name: Optional[str] = None) -> None:
    logger = get_logger(logger_name or "app")
    logger.warning(_join_message(args))


def log_error(
    *args: Any,
    logger_name: Optional[str] = None,
    exc_info: bool = False,
) -> None:
    logger = get_logger(logger_name or "app")
    logger.error(_join_message(args), exc_info=exc_info)


def log_exception(*args: Any, logger_name: Optional[str] = None) -> None:
    logger = get_logger(logger_name or "app")
    logger.exception(_join_message(args))


def log_legacy(*args: Any, **kwargs: Any) -> None:
    """Minimal compatibility shim for historical print-style callsites.

    Deprecated:
    - `log_legacy` is deprecated and retained only for backward compatibility.

    Notes:
    - Keeps only basic print-like argument handling (`sep`, `end`).
    - Ignores other print kwargs (`file`, `flush`, etc.).
    - Always writes at INFO level; semantic levels should use explicit logger methods.
    """
    _warn_legacy_deprecated_once()

    message = kwargs.get("sep", " ").join(str(arg) for arg in args)
    end = kwargs.get("end", "\n")
    if end and end != "\n":
        message = f"{message}{end}"

    get_logger("legacy-compat").info(message.rstrip("\n"))


def legacy_print(*args: Any, **kwargs: Any) -> None:
    """Deprecated alias of `log_legacy` kept only for backward compatibility."""
    log_legacy(*args, **kwargs)
