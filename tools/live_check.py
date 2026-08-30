"""The pre-flight that has to run on a machine with real network access.

Everything else in this repo has been verified offline. This is the part that
cannot be: it talks to Alpaca, confirms the account is what we think it is,
places a REAL order on the DEV account, and closes it again.

Run it from a terminal that can reach the internet:

    python tools/live_check.py            # read-only checks
    python tools/live_check.py --trade    # also place and close one real order

It refuses to run against the scored account. Read-only mode places nothing.

Why the --trade step matters: until an order has actually gone to a venue and
come back filled, "the execution path works" is a claim supported only by
tests against a fake broker. Crypto trades 24/7, so this works at the weekend
when the option and equity markets are shut.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from glassbox import config as C  # noqa: E402
from glassbox import env  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

_fails: list[str] = []
_warns: list[str] = []

LIVE_CHECK_MAX_NOTIONAL_USD = Decimal("50.00")
_TERMINAL_ORDER_STATES = {
    "filled",
    "canceled",
    "cancelled",
    "expired",
    "rejected",
    "suspended",
    "done_for_day",
    "replaced",
}


@dataclass(frozen=True)
class LiveTradeResult:
    """Outcome of the development-account venue proof."""

    ok: bool
    reason: str
    entry_filled: Decimal = Decimal(0)
    exit_filled: Decimal = Decimal(0)


def _status(order) -> str:
    return str(getattr(order, "status", "")).lower().split(".")[-1]


def _filled_qty(order) -> Decimal:
    return Decimal(str(getattr(order, "filled_qty", 0) or 0))


def _settle_order(
    broker,
    order,
    client_order_id: str,
    *,
    wait_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], None],
):
    """Observe terminal state, canceling and confirming at the deadline."""
    deadline = time.monotonic() + wait_seconds
    while True:
        current = broker.get_order_by_coid(client_order_id)
        if current is not None and _status(current) in _TERMINAL_ORDER_STATES:
            return current
        if time.monotonic() >= deadline:
            return broker.cancel_and_confirm(
                str(order.id),
                client_order_id,
                timeout=max(wait_seconds, 0.01),
                poll_seconds=poll_seconds,
            )
        sleep(poll_seconds)


def run_trade_check(
    broker,
    journal,
    notional: Decimal,
    *,
    run_id: str | None = None,
    wait_seconds: float = 30.0,
    poll_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> LiveTradeResult:
    """Place and exactly reverse one bounded dev-account BTC/USD trade."""
    requested = Decimal(str(notional))
    if requested <= 0 or requested > LIVE_CHECK_MAX_NOTIONAL_USD:
        return LiveTradeResult(
            False,
            f"notional {requested} is outside the positive "
            f"${LIVE_CHECK_MAX_NOTIONAL_USD} hard ceiling",
        )

    try:
        broker.assert_ready()
    except Exception as exc:
        return LiveTradeResult(False, f"account identity was not proven: {exc}")

    try:
        baseline_positions = broker.positions()
        baseline_orders = broker.open_orders()
    except Exception as exc:
        return LiveTradeResult(False, f"could not prove a clean baseline: {exc}")
    if baseline_positions:
        return LiveTradeResult(False, "clean baseline required: positions exist")
    if baseline_orders:
        return LiveTradeResult(False, "clean baseline required: open orders exist")

    symbol = "BTC/USD"
    try:
        price = broker.snapshot_prices([symbol]).get(symbol)
    except Exception as exc:
        return LiveTradeResult(False, f"could not price {symbol}: {exc}")
    if price is None or price <= 0:
        return LiveTradeResult(False, f"no positive {symbol} price")

    quantity = (requested / price).quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    if quantity <= 0:
        return LiveTradeResult(False, "notional produces zero tradeable quantity")

    from glassbox.ids import client_order_id

    proof_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    entry_coid = client_order_id(f"live-check:{proof_id}", 0)
    exit_coid = client_order_id(f"live-check:{proof_id}", 1)
    journal.append(
        "live_check",
        "VENUE_PROOF_STARTED",
        {
            "symbol": symbol,
            "notional_ceiling": str(LIVE_CHECK_MAX_NOTIONAL_USD),
            "requested_notional": str(requested),
            "requested_qty": str(quantity),
            "entry_client_order_id": entry_coid,
            "exit_client_order_id": exit_coid,
        },
    )

    try:
        entry = broker.submit(
            symbol=symbol,
            qty=quantity,
            side="buy",
            client_order_id=entry_coid,
            limit_price=(price * Decimal("1.01")).quantize(Decimal("0.01")),
            instrument="crypto",
        )
        entry_final = _settle_order(
            broker,
            entry,
            entry_coid,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            sleep=sleep,
        )
    except Exception as exc:
        return LiveTradeResult(False, f"entry state is uncertain: {exc}")

    entry_filled = _filled_qty(entry_final)
    if entry_filled <= 0:
        try:
            flat = not broker.positions() and not broker.open_orders()
        except Exception:
            flat = False
        reason = "entry reached terminal state without a fill"
        if not flat:
            reason += "; exact baseline was not restored"
        return LiveTradeResult(False, reason)

    try:
        exit_order = broker.submit(
            symbol=symbol,
            qty=entry_filled,
            side="sell",
            client_order_id=exit_coid,
            limit_price=(price * Decimal("0.99")).quantize(Decimal("0.01")),
            instrument="crypto",
        )
        exit_final = _settle_order(
            broker,
            exit_order,
            exit_coid,
            wait_seconds=wait_seconds,
            poll_seconds=poll_seconds,
            sleep=sleep,
        )
    except Exception as exc:
        return LiveTradeResult(False, f"cleanup exit state is uncertain: {exc}", entry_filled)

    exit_filled = _filled_qty(exit_final)
    try:
        residual_positions = broker.positions()
        residual_orders = broker.open_orders()
    except Exception as exc:
        return LiveTradeResult(
            False, f"exact baseline reconciliation failed: {exc}", entry_filled, exit_filled
        )

    if exit_filled != entry_filled or residual_positions:
        return LiveTradeResult(
            False,
            "cleanup did not restore the exact baseline position quantity",
            entry_filled,
            exit_filled,
        )
    created_ids = {entry_coid, exit_coid}
    if any(str(getattr(o, "client_order_id", "")) in created_ids for o in residual_orders):
        return LiveTradeResult(
            False, "cleanup left a test-owned open order", entry_filled, exit_filled
        )

    journal.append(
        "live_check",
        "VENUE_PROOF_RECONCILED",
        {
            "entry_filled": str(entry_filled),
            "exit_filled": str(exit_filled),
            "positions": 0,
            "test_owned_open_orders": 0,
        },
    )
    return LiveTradeResult(
        True,
        "entry and exact-quantity exit filled; account reconciled flat",
        entry_filled,
        exit_filled,
    )


def ok(msg: str, detail: str = "") -> None:
    print(f"  [{GREEN}PASS{RESET}] {msg}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")


def bad(msg: str, detail: str = "") -> None:
    _fails.append(msg)
    print(f"  [{RED}FAIL{RESET}] {msg}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")


def warn(msg: str, detail: str = "") -> None:
    _warns.append(msg)
    print(f"  [{YELLOW}WARN{RESET}] {msg}")
    if detail:
        print(f"         {DIM}{detail}{RESET}")


def section(title: str) -> None:
    print(f"\n{YELLOW}{title}{RESET}")


def main() -> int:
    _fails.clear()
    _warns.clear()
    ap = argparse.ArgumentParser(prog="live_check")
    ap.add_argument(
        "--trade",
        action="store_true",
        help="place and immediately close ONE small real crypto order",
    )
    ap.add_argument(
        "--notional",
        type=Decimal,
        default=LIVE_CHECK_MAX_NOTIONAL_USD,
        help="test order USD size; hard maximum 50.00",
    )
    args = ap.parse_args()

    print(f"{YELLOW}Glassbox live check{RESET}")
    print(f"{DIM}The only checks that need real network access.{RESET}")

    # -- 0. environment --------------------------------------------------------
    section("0. Environment")
    problems = env.preflight(ROOT / ".env", strict=False)
    if problems:
        for p in problems:
            bad("environment", p)
        return 1
    ok("preflight", "systemd and dotenv agree on .env")

    mode = env.get("ALPACA_ENV", "dev")
    if mode != "dev":
        bad(
            f"ALPACA_ENV is {mode!r}", "this script places orders; point it at the dev account only"
        )
        return 1
    ok("pointed at the dev account")

    # -- 1. connectivity and identity -----------------------------------------
    section("1. Account")
    try:
        from glassbox.broker import Broker
        from glassbox.journal import Journal

        journal = Journal(ROOT / "state" / "live_check.jsonl")
        broker = Broker(journal=journal)
    except Exception as exc:
        bad("could not construct the broker", str(exc))
        return 1

    try:
        info = broker.assert_ready()
        acct = broker.account()
    except Exception as exc:
        bad("cannot prove Alpaca account identity", str(exc))
        print(
            f"\n{DIM}If this says proxy/403, you are behind an egress filter. "
            f"Run this from an ordinary terminal.{RESET}"
        )
        return 1

    ok("reached expected Alpaca account", f"account {info['account_number']}, status {acct.status}")
    ok("equity", f"${Decimal(str(acct.equity)):,}")

    level = getattr(acct, "options_trading_level", None)
    approved = getattr(acct, "options_approved_level", None)
    if level is None:
        warn("no options level reported", "check the dashboard before Monday")
    elif int(level) >= 2:
        ok(
            f"options enabled at level {level}",
            f"approved level {approved}; level 3 is needed for spreads",
        )
    else:
        bad(
            f"options level is {level}",
            "the strategy trades long options; enable options on this account",
        )

    if getattr(acct, "trading_blocked", False):
        bad("trading is blocked on this account")

    # -- 2. market data --------------------------------------------------------
    section("2. Market data")
    prices: dict[str, Decimal] = {}
    try:
        prices = broker.snapshot_prices(["SPY", "QQQ", "IWM"])
        missing = [s for s in ("SPY", "QQQ", "IWM") if s not in prices]
        if missing:
            bad(f"no price for {missing}", "a symbol with no snapshot price can never be traded")
        else:
            ok("equity snapshots", ", ".join(f"{k} {v}" for k, v in sorted(prices.items())))
    except Exception as exc:
        bad("equity snapshot failed", str(exc))

    try:
        cprices = broker.snapshot_prices(sorted(C.CRYPTO_ALLOWLIST))
        if cprices:
            ok("crypto snapshots", ", ".join(f"{k} {v}" for k, v in sorted(cprices.items())))
        else:
            bad("no crypto prices", "the crypto sleeve cannot trade without them")
    except Exception as exc:
        bad("crypto snapshot failed", str(exc))

    # -- 3. option chain -------------------------------------------------------
    section("3. Option chain")
    try:
        from glassbox.data import MarketData

        md = MarketData(broker)
        spot = prices.get("SPY")
        quotes = md.expiry_quotes("SPY", spot) if spot is not None else None
        if quotes:
            ok(f"{len(quotes)} expiries quoted")
            for q in quotes[:6]:
                print(f"         {DIM}{q.expiry}  iv={q.atm_iv}  spread={q.bid_ask_pct}{RESET}")
        else:
            warn(
                "no expiry quotes returned",
                "expected at the weekend; re-run Monday during market hours",
            )
    except Exception as exc:
        warn("option chain unavailable", str(exc))

    # -- 4. clock --------------------------------------------------------------
    section("4. Clock")
    try:
        clock = broker.clock()
        ok(f"market_open={clock.is_open}", f"next open {clock.next_open}")
    except Exception as exc:
        bad("clock failed", str(exc))

    # -- 5. a real order -------------------------------------------------------
    section("5. Real order")
    if not args.trade:
        print(f"  {DIM}skipped. Re-run with --trade to place one.{RESET}")
    else:
        result = run_trade_check(broker, journal, args.notional)
        if result.ok:
            ok(
                "venue proof reconciled",
                f"entry {result.entry_filled} BTC; exact exit {result.exit_filled} BTC; flat",
            )
        else:
            bad("venue proof failed", result.reason)

    # -- verdict ---------------------------------------------------------------
    print(f"\n{'-' * 66}")
    if _fails:
        print(f"{RED}LIVE CHECK FAILED{RESET}  {len(_fails)} problem(s)")
        for f in _fails:
            print(f"  - {f}")
        return 1
    if _warns:
        print(f"{GREEN}LIVE CHECK PASSED{RESET}  with {len(_warns)} warning(s)")
    else:
        print(f"{GREEN}LIVE CHECK PASSED{RESET}")
    print(f"{DIM}Journal written to state/live_check.jsonl{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
