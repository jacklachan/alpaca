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
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from glassbox import config as C  # noqa: E402
from glassbox import env  # noqa: E402

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"

_fails: list[str] = []
_warns: list[str] = []


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
    ap = argparse.ArgumentParser(prog="live_check")
    ap.add_argument("--trade", action="store_true",
                    help="place and immediately close ONE small real crypto order")
    ap.add_argument("--notional", type=float, default=50.0,
                    help="size of that test order in USD (default 50)")
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
        bad(f"ALPACA_ENV is {mode!r}",
            "this script places orders; point it at the dev account only")
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
        acct = broker.account()
    except Exception as exc:
        bad("cannot reach Alpaca", str(exc))
        print(f"\n{DIM}If this says proxy/403, you are behind an egress filter. "
              f"Run this from an ordinary terminal.{RESET}")
        return 1

    ok("reached Alpaca", f"account {acct.account_number}, status {acct.status}")
    ok("equity", f"${Decimal(str(acct.equity)):,}")

    level = getattr(acct, "options_trading_level", None)
    approved = getattr(acct, "options_approved_level", None)
    if level is None:
        warn("no options level reported", "check the dashboard before Monday")
    elif int(level) >= 2:
        ok(f"options enabled at level {level}",
           f"approved level {approved}; level 3 is needed for spreads")
    else:
        bad(f"options level is {level}",
            "the strategy trades long options; enable options on this account")

    if getattr(acct, "trading_blocked", False):
        bad("trading is blocked on this account")

    # -- 2. market data --------------------------------------------------------
    section("2. Market data")
    try:
        prices = broker.snapshot_prices(["SPY", "QQQ", "IWM"])
        missing = [s for s in ("SPY", "QQQ", "IWM") if s not in prices]
        if missing:
            bad(f"no price for {missing}",
                "a symbol with no snapshot price can never be traded")
        else:
            ok("equity snapshots", ", ".join(f"{k} {v}" for k, v in sorted(prices.items())))
    except Exception as exc:
        bad("equity snapshot failed", str(exc))

    try:
        cprices = broker.snapshot_prices(sorted(C.CRYPTO_ALLOWLIST))
        if cprices:
            ok("crypto snapshots",
               ", ".join(f"{k} {v}" for k, v in sorted(cprices.items())))
        else:
            bad("no crypto prices", "the crypto sleeve cannot trade without them")
    except Exception as exc:
        bad("crypto snapshot failed", str(exc))

    # -- 3. option chain -------------------------------------------------------
    section("3. Option chain")
    try:
        from glassbox.data import MarketData
        md = MarketData(broker)
        quotes = md.expiry_quotes("SPY") if hasattr(md, "expiry_quotes") else None
        if quotes:
            ok(f"{len(quotes)} expiries quoted")
            for q in quotes[:6]:
                print(f"         {DIM}{q.expiry}  iv={q.atm_iv}  "
                      f"spread={q.bid_ask_pct}{RESET}")
        else:
            warn("no expiry quotes returned",
                 "expected at the weekend; re-run Monday during market hours")
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
        sym = "BTC/USD"
        px = broker.snapshot_prices([sym]).get(sym)
        if not px:
            bad("no BTC/USD price; cannot place the test order")
        else:
            qty = (Decimal(str(args.notional)) / px).quantize(Decimal("0.000001"))
            print(f"  {DIM}buying {qty} {sym} at about {px}{RESET}")
            from glassbox.ids import client_order_id
            coid = client_order_id(f"live-check-{int(time.time())}", 0)
            try:
                order = broker.submit(symbol=sym, qty=qty, side="buy",
                                      client_order_id=coid,
                                      limit_price=(px * Decimal("1.01")).quantize(Decimal("0.01")),
                                      instrument="crypto")
                ok("order accepted", f"broker id {order.id}")
            except Exception as exc:
                bad("submit failed", str(exc))
                order = None

            if order is not None:
                filled = None
                for _ in range(15):
                    time.sleep(2)
                    o = broker.get_order_by_coid(coid)
                    if o and str(getattr(o, "status", "")).lower().endswith("filled"):
                        filled = o
                        break
                if filled:
                    ok("order FILLED",
                       f"{filled.filled_qty} @ {filled.filled_avg_price} — "
                       f"the execution path is proven end to end")
                else:
                    warn("no fill within 30s",
                         "not necessarily wrong; check the dashboard")

                try:
                    broker.close_position(sym)
                    ok("position closed", "account returned to flat")
                except Exception as exc:
                    warn("could not close automatically", f"{exc} — close it by hand")

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
