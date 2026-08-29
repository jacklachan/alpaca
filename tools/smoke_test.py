"""Hour-zero verification. Run this before anything else is trusted.

    python -m tools.smoke_test

Reads credentials from the environment only. Nothing is printed that could
leak a key -- the account number is masked, and no header is ever echoed.

Checks, in order of what blocks the build:
  1. Credentials present, and this is a PAPER account
  2. Account reachable, equity and status readable
  3. Options are enabled, and at what level
  4. Market clock and calendar agree with our timezone handling
  5. SPY snapshot -- is the data feed actually returning quotes
  6. Option chain -- can we see expiries and Greeks
  7. Term structure across the candidate expiries (the expiry question)
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def mask(s: str | None) -> str:
    if not s:
        return "<missing>"
    return f"{s[:4]}...{s[-4:]}" if len(s) > 10 else "<short>"


def ok(msg: str) -> None:
    print(f"  [ok]   {msg}")


def bad(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def warn(msg: str) -> None:
    print(f"  [warn] {msg}")


def main() -> int:
    failures = 0
    print("\nGlassbox smoke test\n" + "=" * 60)

    # --- 1. credentials -------------------------------------------------------
    print("\n1. Credentials")
    key = os.getenv("ALPACA_API_KEY")
    secret = os.getenv("ALPACA_SECRET_KEY")
    env = os.getenv("ALPACA_ENV", "dev")
    paper = os.getenv("ALPACA_PAPER_TRADE", "true").lower() == "true"

    if not key or not secret:
        bad("ALPACA_API_KEY / ALPACA_SECRET_KEY not set. Copy .env.example to .env.")
        return 1
    ok(f"key {mask(key)} loaded from environment (env={env})")

    if not paper:
        bad("ALPACA_PAPER_TRADE is not true. Refusing to continue.")
        return 1
    if not key.startswith("PK"):
        bad(f"key does not look like a paper key (expected PK prefix). REFUSING.")
        return 1
    ok("paper credentials confirmed")

    try:
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest, StockSnapshotRequest
    except ImportError:
        bad("alpaca-py not installed. Run: pip install -r requirements.txt")
        return 1

    trading = TradingClient(key, secret, paper=True)

    # --- 2. account -----------------------------------------------------------
    print("\n2. Account")
    try:
        acct = trading.get_account()
    except Exception as exc:
        bad(f"could not reach the account: {exc}")
        return 1

    num = str(acct.account_number)
    ok(f"account {num[:3]}***{num[-2:]}  status={acct.status}")
    ok(f"equity ${float(acct.equity):,.2f}  cash ${float(acct.cash):,.2f}")

    if env == "scored":
        if abs(float(acct.equity) - 100_000) > 0.01:
            bad(f"scored account equity is {float(acct.equity):,.2f}, expected exactly 100,000.00")
            failures += 1
        else:
            ok("equity is exactly $100,000.00")
        positions = trading.get_all_positions()
        if positions:
            bad(f"scored account already holds {len(positions)} positions -- must be clean")
            failures += 1
        else:
            ok("position list is empty")

    if getattr(acct, "trading_blocked", False):
        bad("trading is blocked on this account")
        failures += 1

    # --- 3. options -----------------------------------------------------------
    print("\n3. Options")
    lvl = getattr(acct, "options_approved_level", None)
    tlvl = getattr(acct, "options_trading_level", None)
    if lvl is None and tlvl is None:
        warn("account object exposes no options level field -- check the dashboard")
    else:
        ok(f"options approved level={lvl}  trading level={tlvl}")
        if (tlvl or 0) < 2:
            bad("options level < 2: cannot buy calls/puts. THIS BLOCKS THE STRATEGY.")
            failures += 1
        elif (tlvl or 0) < 3:
            warn("level 2: single legs only. Leg the strangle as two orders (we do anyway).")
        else:
            ok("level 3: multi-leg available, though we still leg it manually")

    # --- 4. clock -------------------------------------------------------------
    print("\n4. Clock and calendar")
    try:
        clock = trading.get_clock()
        ok(f"market open={clock.is_open}  next open={clock.next_open}  next close={clock.next_close}")
    except Exception as exc:
        bad(f"clock unavailable: {exc}")
        failures += 1

    # --- 5. data --------------------------------------------------------------
    print("\n5. Market data")
    spot = None
    try:
        sd = StockHistoricalDataClient(key, secret)
        snap = sd.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=["SPY", "QQQ"]))
        for sym, s in snap.items():
            px = None
            if s.latest_trade:
                px = float(s.latest_trade.price)
            elif s.latest_quote:
                px = (float(s.latest_quote.ask_price) + float(s.latest_quote.bid_price)) / 2
            if sym == "SPY":
                spot = px
            ok(f"{sym} last={px}")
        if spot is None:
            warn("no SPY price returned -- market may be closed and IEX quiet")
    except Exception as exc:
        bad(f"stock data failed: {exc}")
        failures += 1

    # --- 6/7. chain and term structure ---------------------------------------
    print("\n6. Option chain and term structure")
    if spot is None:
        warn("skipping chain checks without a spot price")
        print("\n" + "=" * 60)
        return 1 if failures else 0

    try:
        od = OptionHistoricalDataClient(key, secret)
        chain = od.get_option_chain(OptionChainRequest(underlying_symbol="SPY"))
        ok(f"chain returned {len(chain)} contracts")

        # Group ATM implied vol by expiry -- this is the expiry question,
        # answered with data rather than reasoning.
        by_exp: dict[date, list[tuple[float, float]]] = {}
        for sym, c in chain.items():
            iv = getattr(c, "implied_volatility", None)
            if iv is None:
                continue
            try:
                exp = date(2000 + int(sym[3:5]), int(sym[5:7]), int(sym[7:9]))
                strike = int(sym[10:]) / 1000
            except Exception:
                continue
            if abs(strike - spot) / spot < 0.01:      # near the money
                by_exp.setdefault(exp, []).append((strike, float(iv)))

        if not by_exp:
            warn("no implied vols in the chain -- the free tier feed is indicative; "
                 "compute IV locally if this stays empty")
        else:
            print("\n   ATM implied vol by expiry:")
            today = date.today()
            for exp in sorted(by_exp)[:8]:
                ivs = [v for _, v in by_exp[exp]]
                avg = sum(ivs) / len(ivs)
                dte = (exp - today).days
                print(f"     {exp}  {dte:>3}d   IV {avg:6.1%}   n={len(ivs)}")
            print("\n   Read this before choosing an expiry:")
            print("     If the short expiries show materially HIGHER IV than the")
            print("     later ones, you are paying an event premium and will eat")
            print("     the post-print vol crush -- take the longer expiry.")
            print("     If they are roughly flat, the holiday discount dominates")
            print("     and the shorter expiry buys more gamma per dollar.")
    except Exception as exc:
        bad(f"option chain failed: {exc}")
        failures += 1

    print("\n" + "=" * 60)
    print("PASS -- safe to build on this account\n" if not failures
          else f"{failures} check(s) FAILED -- resolve before building\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
