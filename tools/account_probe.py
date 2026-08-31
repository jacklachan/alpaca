"""Read-only account probe. Places no orders. Answers: which account is this,
what is it approved for, and has it ever traded."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient  # noqa: E402
from alpaca.trading.enums import QueryOrderStatus  # noqa: E402
from alpaca.trading.requests import GetOrdersRequest  # noqa: E402

from glassbox import env as env_module  # noqa: E402
from glassbox.state import atomic_write_json  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Alpaca account probe.")
    parser.add_argument(
        "--emit",
        default="",
        help="write the account_identity evidence artifact to this path",
    )
    args = parser.parse_args(argv)
    emit = args.emit

    environment = env_module.require_choice("ALPACA_ENV", {"dev", "scored"}, default="dev")
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        print("no credentials")
        return 1

    tc: Any = TradingClient(key, sec, paper=True)
    a = tc.get_account()

    print("NETWORK           : reachable")
    print(f"ALPACA_ENV        : {os.getenv('ALPACA_ENV')}")
    print(f"account_number    : {a.account_number}")
    print(f"status            : {a.status}")
    print(f"equity            : {a.equity}")
    print(f"cash              : {a.cash}")
    print(f"buying_power      : {a.buying_power}")
    print(f"created_at        : {getattr(a, 'created_at', None)}")
    print(f"options_approved  : {getattr(a, 'options_approved_level', None)}")
    print(f"options_trading   : {getattr(a, 'options_trading_level', None)}")
    print(f"shorting_enabled  : {getattr(a, 'shorting_enabled', None)}")
    print(f"pattern_day_trader: {getattr(a, 'pattern_day_trader', None)}")
    print(f"crypto_status     : {getattr(a, 'crypto_status', None)}")

    orders = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL, limit=500))
    positions = tc.get_all_positions()
    print(f"\nlifetime_orders   : {len(orders)}")
    print(f"open_positions    : {len(positions)}")
    for p in positions[:20]:
        print(f"   {p.symbol:24} qty={p.qty:>10} mv={p.market_value:>12} upl={p.unrealized_pl}")

    clock = tc.get_clock()
    print(f"\nmarket_is_open    : {clock.is_open}")
    print(f"next_open         : {clock.next_open}")

    pristine = len(orders) == 0 and len(positions) == 0
    print("\nVERDICT           :", "PRISTINE (never traded)" if pristine else "USED (has history)")

    if emit:
        # The account_identity artifact the release gate is approved against.
        # Redacted on purpose: the manifest carries a suffix, and the full id
        # goes to judges through the submission channel, not through a file
        # that gets copied around.
        expected_key = (
            "ALPACA_EXPECTED_SCORED_ACCOUNT_ID"
            if environment == "scored"
            else "ALPACA_EXPECTED_DEV_ACCOUNT_ID"
        )
        expected = env_module.get(expected_key, "")
        returned = str(getattr(a, "account_number", "") or "")
        matches = bool(expected) and returned == expected
        atomic_write_json(
            emit,
            {
                "check": "account_identity",
                "environment": environment,
                "account_suffix": f"...{returned[-4:]}" if returned else "",
                "expected_suffix": f"...{expected[-4:]}" if expected else "",
                "matches_expected": matches,
                "status": str(getattr(a, "status", "")),
                "equity": str(getattr(a, "equity", "")),
                "options_trading_level": str(getattr(a, "options_trading_level", "")),
                "lifetime_orders": len(orders),
                "open_positions": len(positions),
                "pristine": pristine,
                "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "complete": matches,
            },
        )
        print(
            f"\nartifact          : {emit} ({'matches' if matches else 'DOES NOT MATCH'} expected)"
        )
        if not matches:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
