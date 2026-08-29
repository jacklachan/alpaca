"""Read-only account probe. Places no orders. Answers: which account is this,
what is it approved for, and has it ever traded."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

from alpaca.trading.client import TradingClient  # noqa: E402
from alpaca.trading.requests import GetOrdersRequest  # noqa: E402
from alpaca.trading.enums import QueryOrderStatus  # noqa: E402


def main() -> int:
    key, sec = os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY")
    if not key or not sec:
        print("no credentials")
        return 1

    tc = TradingClient(key, sec, paper=True)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
