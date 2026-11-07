"""
Notification service (placeholder)

There's no registration, login, or password-reset flow here, so no verification emails or
SMS messages are needed.

This package is kept because `routes/auth.py` imports these symbols at module level — the
import runs at startup even though the corresponding routes are never reachable.
Everything below is a no-op that fails loudly if it somehow gets called.
"""
from typing import Any


class SmsSendError(Exception):
    """SMS delivery failure (cannot happen in this build)"""


def _unsupported(feature: str) -> None:
    raise NotImplementedError(
        f"{feature} is unavailable: there are no accounts here, so no verification is needed."
    )


def build_message(*args: Any, **kwargs: Any) -> None:
    _unsupported("Email composition")


def send_verification_email(*args: Any, **kwargs: Any) -> None:
    _unsupported("Verification email")


async def send_verification_email_async(*args: Any, **kwargs: Any) -> None:
    _unsupported("Verification email")


def send_password_reset_email(*args: Any, **kwargs: Any) -> None:
    _unsupported("Password reset email")


async def send_password_reset_email_async(*args: Any, **kwargs: Any) -> None:
    _unsupported("Password reset email")


async def send_register_sms_async(*args: Any, **kwargs: Any) -> None:
    _unsupported("Registration SMS")


async def send_reset_password_sms_async(*args: Any, **kwargs: Any) -> None:
    _unsupported("Password reset SMS")
