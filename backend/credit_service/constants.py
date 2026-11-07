"""
Cost table (placeholder)

Nothing is metered here — you use your own API keys and pay your model provider directly,
so every cost is zero.

This module exists because several call sites import `CreditCost` from it directly
(`from credit_service.constants import CreditCost`). Removing it would break those imports
at runtime — which is exactly what happened once before.
"""
from . import CreditCost

__all__ = ["CreditCost"]
