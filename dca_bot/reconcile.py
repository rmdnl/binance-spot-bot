"""Backward-compatible facade for the canonical reconciliation service.

New execution code must import :mod:`dca_bot.reconciliation` directly.
This module intentionally contains no fill/accounting implementation.
"""

from .reconciliation import ReconcileStats, reconcile_orders

__all__ = ["ReconcileStats", "reconcile_orders"]
