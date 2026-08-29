"""Deterministic order identity.

A retry after a network timeout must not be able to double a position. The
client_order_id is a pure function of (plan_id, leg_index), so resubmitting the
same leg produces the same id and the broker rejects the duplicate.
"""

from __future__ import annotations

import hashlib


def client_order_id(plan_id: str, leg_index: int = 0) -> str:
    """Stable, collision-resistant, and short enough for Alpaca's field."""
    digest = hashlib.sha256(f"{plan_id}:{leg_index}".encode()).hexdigest()
    return f"gbx-{digest[:32]}"
