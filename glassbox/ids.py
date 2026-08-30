"""Deterministic order identity.

A retry after a network timeout must not be able to double a position. The
client_order_id is a pure function of (plan_id, leg_index), so resubmitting the
same leg produces the same id and the broker rejects the duplicate.
"""

from __future__ import annotations

import hashlib
import json

PREFIX = "gbx-"
EVENT_PREFIX = "gbxe-"
PLAN_PREFIX = "gbp-"


def stable_plan_id(namespace: str, *parts: object) -> str:
    """Return a deterministic identity for one semantic trade opportunity."""
    body = json.dumps([namespace, *parts], sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(body.encode()).hexdigest()
    return f"{PLAN_PREFIX}{digest[:32]}"


def client_order_id(plan_id: str, leg_index: int = 0, *, event: bool = False) -> str:
    """Stable, collision-resistant, and short enough for Alpaca's field.

    The prefix carries one bit of information on purpose. `reconcile()` sees
    only what the broker returns -- orders, not plans -- so without a marker in
    the id there is no way to tell an event trade's premium from any other
    option premium, and EVENT_TRADE_DAILY_CAP degrades into a per-order check
    that two $16,000 strangles in one day both pass.
    """
    digest = hashlib.sha256(f"{plan_id}:{leg_index}".encode()).hexdigest()
    return f"{EVENT_PREFIX if event else PREFIX}{digest[:32]}"


EXIT_PREFIX = "gbx-x-"


def exit_client_order_id(plan_id: str, symbol: str, attempt: int = 0) -> str:
    """Deterministic identity for one exit order on one contract.

    Derived, not minted: a crash between registering the intent and submitting
    must produce the same id on restart, so the recovery path looks the order
    up instead of sending a second one.
    """
    digest = hashlib.sha256(f"exit:{plan_id}:{symbol}:{attempt}".encode()).hexdigest()
    return f"{EXIT_PREFIX}{digest[:26]}"
