# credit_service/__init__.py
"""
Quota service (placeholder)

Nothing is metered here: you use your own API keys and pay your model provider directly.

This package is kept rather than deleted because `user_id` and quota checks are threaded
throughout the codebase (43 call sites of `CreditService(db)`); removing it would mean a
sprawling refactor. Instead it exports a null implementation — every deduction is a no-op,
the balance is effectively unlimited, and business logic doesn't need to know the difference.
"""
from runtime.billing import NullCreditService as CreditService
from .exceptions import InsufficientCreditsError, CreditOperationError


class CreditCost:
    """Placeholder cost table: nothing is metered, so every cost is 0"""

    def __getattr__(self, name):  # pragma: no cover
        return 0

    @staticmethod
    def calculate_qa_cost(*args, **kwargs) -> int:
        return 0


# Module-level singleton so `CreditCost.DOCUMENT_UPLOAD`-style access returns 0
CreditCost = CreditCost()

__all__ = [
    'CreditCost',
    'CreditService',
    'InsufficientCreditsError',
    'CreditOperationError',
]
