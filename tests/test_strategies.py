"""Core and crypto sleeves, and the feature layer.

Every plan a strategy emits must survive the kernel. If a strategy can produce
something the kernel refuses, that is a bug in the strategy, not in the kernel.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from glassbox import config as C
from glassbox.data import atr, iv_to_rv, overnight_gap, realised_vol
from glassbox.kernel import PortfolioState, Position, RiskKernel
from glassbox.strategies.core import WEIGHTS, CoreStrategy
from glassbox.strategies.crypto import CryptoStrategy

K = RiskKernel()
PRICES = {"SPY": Decimal("769.28"), "QQQ": Decimal("716.91"), "IWM": Decimal("240"),
          "BTC/USD": Decimal("95000"), "ETH/USD": Decimal("3200")}


def state(now=None, positions=None, **kw) -> PortfolioState:
    base = dict(
        equity=Decimal("100000"), cash=Decimal("100000"),
        core_sleeve_value=Decimal(0), core_sleeve_cost_basis=Decimal(0),
        positions=positions or [], snapshot_price=dict(PRICES),
        market_open=True, median_order_notional=Decimal("20000"),
        now_et=now or datetime(2026, 8, 31, 9, 35, tzinfo=C.ET))
    base.update(kw)
    return PortfolioState(**base)


# --- core ---------------------------------------------------------------------

def test_core_buys_the_full_allocation_once():
    plans = CoreStrategy().propose_from_state(state())
    assert {p.symbol for p in plans} == set(WEIGHTS)
    assert sum(p.notional_usd for p in plans) == C.CORE_SLEEVE_USD


def test_core_never_tops_up_an_existing_holding():
    held = [Position(symbol="SPY", instrument="equity", qty=Decimal(40),
                     market_value=Decimal("32500"))]
    plans = CoreStrategy().propose_from_state(state(positions=held))
    assert "SPY" not in {p.symbol for p in plans}


def test_core_does_nothing_when_the_market_is_closed():
    assert CoreStrategy().propose_from_state(state(market_open=False)) == []


def test_core_claims_no_edge_and_says_so():
    """The passive core is a decision, and the journal should record it as one."""
    p = CoreStrategy().propose_from_state(state())[0]
    assert "edge_claimed=none" in p.evidence
    assert "no directional edge" in p.thesis


def test_every_core_plan_passes_the_kernel():
    s = state()
    for p in CoreStrategy().propose_from_state(s):
        v = K.review(p, s)
        assert v.approved, f"{p.symbol}: {v.reason}"


# --- crypto -------------------------------------------------------------------

def test_crypto_allocates_its_sleeve():
    plans = CryptoStrategy().propose_from_state(state())
    assert {p.symbol for p in plans} == set(C.CRYPTO_ALLOWLIST)
    assert sum(p.notional_usd for p in plans) == C.CRYPTO_SLEEVE_USD


def test_crypto_trades_while_the_equity_market_is_closed():
    plans = CryptoStrategy().propose_from_state(state(market_open=False))
    assert plans, "the crypto sleeve is the reason the agent runs at 03:00"


def test_crypto_event_window_after_the_payrolls_print():
    """08:30 ET Friday: the number is out and equities do not open until 09:30."""
    during = datetime(2026, 9, 4, 8, 45, tzinfo=C.ET)
    plans = CryptoStrategy().propose_from_state(state(now=during, market_open=False))
    assert plans
    p = plans[0]
    assert "equity market is closed" in p.thesis
    assert p.time_exit is not None, "must have a hard exit before measurement"
    assert "equity_market_open=False" in " ".join(p.evidence)


def test_crypto_outside_the_window_is_a_baseline_allocation():
    before = datetime(2026, 9, 4, 7, 0, tzinfo=C.ET)
    p = CryptoStrategy().propose_from_state(state(now=before, market_open=False))[0]
    assert "baseline allocation" in p.thesis
    assert p.time_exit is None


def test_every_crypto_plan_passes_the_kernel():
    s = state(market_open=False)
    for p in CryptoStrategy().propose_from_state(s):
        v = K.review(p, s)
        assert v.approved, f"{p.symbol}: {v.reason}"


# --- features -----------------------------------------------------------------

def test_realised_vol_is_zero_for_a_flat_series():
    assert realised_vol([100.0] * 10) == 0.0


def test_realised_vol_rises_with_dispersion():
    calm = realised_vol([100, 100.2, 100.1, 100.3, 100.2, 100.4, 100.3])
    wild = realised_vol([100, 104, 97, 105, 96, 106, 95])
    assert wild > calm > 0


def test_realised_vol_needs_enough_data():
    assert realised_vol([100.0, 101.0]) == 0.0


def test_iv_to_rv_flags_cheap_and_expensive_convexity():
    assert iv_to_rv(0.094, 0.12) < 1.0            # cheap: buy
    assert iv_to_rv(0.20, 0.08) > C.MAX_IV_TO_RV_RATIO   # expensive: stand down
    assert iv_to_rv(0.10, 0.0) == float("inf")    # no realised vol -> refuse


def test_atr_and_gap():
    highs = [102, 103, 104, 103]
    lows = [99, 100, 101, 100]
    closes = [101, 102, 103, 101]
    assert atr(highs, lows, closes) > 0
    assert overnight_gap(100.0, 101.0) == 0.01
    assert overnight_gap(0.0, 101.0) == 0.0
