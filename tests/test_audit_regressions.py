"""Regressions for the defects a full-codebase audit found on 29 Aug.

Every test here corresponds to a bug that was live in code that had 142 passing
tests. They are grouped by what the bug would have cost, because that is the
order in which they matter: money first, then a dead sleeve, then bookkeeping.

The lesson worth keeping: all of these passed the existing suite. Tests that
assert the code does what it does are not the same as tests that assert the
code does what it should.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal

from glassbox import config as C
from glassbox.execute import ExecutionResult, LegResult
from glassbox.ids import EVENT_PREFIX, PREFIX, client_order_id

# --- money ---------------------------------------------------------------------


class TestPartialFillAccounting:
    """A repriced leg must not lose the fill its predecessor already got.

    The bug: _await_fills overwrote filled_qty with the NEW order's fill, so a
    leg that filled 4 then 6 reported 6. `remaining` was then recomputed from
    the reset value and the agent ordered MORE than approved -- 14 contracts
    against an approved 10, a 21.9% breach of the max-loss invariant.
    """

    def test_fills_accumulate_across_reprices(self):
        leg = LegResult(leg_index=0, symbol="SPY260904C00780000", requested_qty=Decimal(10))
        leg.current_qty, leg.current_avg = Decimal(4), Decimal("3.00")
        leg.bank()  # first order cancelled
        leg.current_qty, leg.current_avg = Decimal(6), Decimal("3.50")

        assert leg.filled_qty == Decimal(10)
        assert leg.complete
        assert not leg.partial

    def test_remaining_never_re_orders_settled_quantity(self):
        leg = LegResult(leg_index=0, symbol="X", requested_qty=Decimal(10))
        leg.current_qty = Decimal(4)
        leg.bank()
        assert leg.requested_qty - leg.filled_qty == Decimal(6)
        leg.current_qty = Decimal(6)
        leg.bank()
        assert leg.requested_qty - leg.filled_qty == Decimal(0)

    def test_average_price_is_weighted_across_orders(self):
        leg = LegResult(leg_index=0, symbol="X", requested_qty=Decimal(10))
        leg.current_qty, leg.current_avg = Decimal(4), Decimal("3.00")
        leg.bank()
        leg.current_qty, leg.current_avg = Decimal(6), Decimal("3.50")
        # (4*3.00 + 6*3.50) / 10
        assert leg.avg_price == Decimal("3.30")

    def test_a_complete_leg_is_not_reported_as_partial(self):
        """The other half of the bug: a filled strangle was unwound."""
        leg = LegResult(leg_index=0, symbol="X", requested_qty=Decimal(10))
        leg.current_qty = Decimal(4)
        leg.bank()
        leg.current_qty = Decimal(6)
        assert leg.complete, "a fully filled leg must not be unwound"


class TestEventBudgetIsIdentifiable:
    """reconcile() sees broker orders, not plans. Without a marker in the id
    there is no way to attribute premium to the event allowance, which made
    EVENT_TRADE_DAILY_CAP a per-order check that two $16k strangles both pass.
    """

    def test_event_orders_are_tagged(self):
        assert client_order_id("p", 0, event=True).startswith(EVENT_PREFIX)
        assert client_order_id("p", 0, event=False).startswith(PREFIX)

    def test_tagging_preserves_determinism(self):
        assert client_order_id("p", 1, event=True) == client_order_id("p", 1, event=True)

    def test_the_two_namespaces_do_not_collide(self):
        assert client_order_id("p", 0, event=True) != client_order_id("p", 0)


class TestKillSwitchDoesNotFireOnDesignedBehaviour:
    def test_backstop_sits_below_the_designed_floor(self):
        """The convex sleeve is permitted to go to zero. A backstop inside that
        range latches, flattens the sleeve at its low, and halts trading for
        the rest of the week with nobody present to re-arm."""
        floor = (
            C.STARTING_EQUITY - C.CONVEX_SLEEVE_USD - C.CORE_SLEEVE_USD * C.CORE_DRAWDOWN_KILL_PCT
        )
        floor_dd = (C.STARTING_EQUITY - floor) / C.STARTING_EQUITY
        assert C.PORTFOLIO_DRAWDOWN_KILL_PCT > floor_dd, (
            f"backstop at {C.PORTFOLIO_DRAWDOWN_KILL_PCT:.0%} fires on a "
            f"designed {floor_dd:.1%} drawdown"
        )


class TestCryptoQuantitiesSurvive:
    """int(0.0968) == 0. Crypto quantities are fractional; truncating them made
    `complete` true at zero fill and reported a filled order as a failure."""

    def test_fractional_quantity_is_not_truncated(self):
        leg = LegResult(leg_index=0, symbol="BTC/USD", requested_qty=Decimal("0.0968"))
        assert leg.requested_qty > 0
        assert not leg.complete
        leg.current_qty = Decimal("0.0968")
        assert leg.complete

    def test_option_multiplier_is_not_applied_to_crypto(self):
        leg = LegResult(leg_index=0, symbol="BTC/USD", requested_qty=Decimal("0.1"))
        leg.current_qty, leg.current_avg = Decimal("0.1"), Decimal("95000")
        opt = ExecutionResult("p", True, "ok", [leg])
        spot = ExecutionResult("p", True, "ok", [leg], multiplier=1)
        assert spot.premium_paid == Decimal("9500.0")
        assert opt.premium_paid == spot.premium_paid * 100


# --- a whole sleeve that could never trade -------------------------------------


class TestTradeableUniverseIsPriced:
    def test_every_allowlisted_symbol_can_be_priced(self):
        """A symbol with no snapshot price is skipped by every strategy and
        hard-refused by invariants 02/05/12. Pricing only what we already HOLD
        meant a symbol we did not hold could never be bought, so it was never
        held -- IWM sat in that trap with 30% of the core sleeve idle."""
        import inspect

        from glassbox import broker as B

        src = inspect.getsource(B.Broker.reconcile)
        assert "EQUITY_ALLOWLIST" in src, (
            "reconcile must price the whole tradeable universe, not just held names"
        )
        assert "CRYPTO_ALLOWLIST" in src

    def test_crypto_symbols_are_not_filtered_out_of_pricing(self):
        import inspect

        from glassbox import broker as B

        src = inspect.getsource(B.Broker.snapshot_prices)
        assert "crypto" in src.lower(), "crypto symbols must get a price somewhere"


class TestCryptoTickActuallyTrades:
    def test_crypto_tick_proposes(self):
        """It used to reconcile and manage but never iterate strategies, so the
        sleeve proposed nothing, ever. equity_tick returns early when the
        market is closed -- exactly when the crypto sleeve should be working."""
        import inspect

        from glassbox.scheduler import Agent

        src = inspect.getsource(Agent.crypto_tick)
        assert "propose_from_state" in src


# --- restart and loop safety ---------------------------------------------------


class TestCatalystDeduplication:
    def test_scheduler_and_strategy_agree_on_the_key(self):
        """The scheduler recorded plan.symbol ("SPY"); the strategy checked
        event.name ("ADP National Employment"). Those never intersect, so one
        catalyst was re-traded every tick until the total premium cap bound --
        $24,720 on a single print in two minutes."""
        import inspect

        from glassbox.scheduler import Agent

        assert "event_key" in inspect.getsource(Agent.execute)

    def test_event_plans_carry_an_event_key(self):
        from glassbox.schema import TradePlan

        assert "event_key" in TradePlan.model_fields

    def test_positioned_set_survives_a_restart(self, tmp_path, monkeypatch):
        path = tmp_path / "positioned.json"
        path.write_text(
            json.dumps({"day": datetime.now(C.ET).date().isoformat(), "keys": ["ISM Services PMI"]})
        )
        monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(path))
        from glassbox.scheduler import Agent

        agent = Agent.__new__(Agent)
        agent._positioned_path = path
        assert "ISM Services PMI" in Agent._load_positioned(agent)

    def test_yesterdays_catalysts_do_not_block_today(self, tmp_path):
        path = tmp_path / "positioned.json"
        path.write_text(json.dumps({"day": "2020-01-01", "keys": ["Old Event"]}))
        from glassbox.scheduler import Agent

        agent = Agent.__new__(Agent)
        agent._positioned_path = path
        assert Agent._load_positioned(agent) == set()


class TestExitsAreNotReissuedEveryTick:
    def test_one_close_per_symbol(self, tmp_path):
        """`_close` fired unconditionally each tick until the broker's position
        list caught up. On a one-minute loop the 14:30 expiry close-out could
        send ~90 duplicate market orders for one contract."""
        from glassbox.journal import Journal
        from glassbox.manage import ExitOrder, KillSwitch, PositionManager

        class B:
            def __init__(self):
                self.closed = []

            def close_position(self, sym):
                self.closed.append(sym)

        j = Journal(tmp_path / "j.jsonl")
        b = B()
        m = PositionManager(
            b, j, KillSwitch(tmp_path / "k.json", journal=j), targets_path=tmp_path / "t.json"
        )
        e = ExitOrder(symbol="SPY260904C00780000", qty=Decimal(2), reason="expiry")
        for _ in range(5):
            m._close(e)
        assert b.closed == ["SPY260904C00780000"]

    def test_a_failed_close_can_be_retried(self, tmp_path):
        from glassbox.journal import Journal
        from glassbox.manage import ExitOrder, KillSwitch, PositionManager

        class B:
            def __init__(self):
                self.n = 0

            def close_position(self, sym):
                self.n += 1
                if self.n == 1:
                    raise RuntimeError("transient")

        j = Journal(tmp_path / "j.jsonl")
        b = B()
        m = PositionManager(
            b, j, KillSwitch(tmp_path / "k.json", journal=j), targets_path=tmp_path / "t.json"
        )
        e = ExitOrder(symbol="X", qty=Decimal(1), reason="stop")
        m._close(e)
        m._close(e)
        assert b.n == 2, "a failed close must not be permanently suppressed"


class TestExitTargetsSurviveRestart:
    def test_targets_are_reloaded(self, tmp_path):
        from glassbox.journal import Journal
        from glassbox.manage import KillSwitch, PositionManager

        j = Journal(tmp_path / "j.jsonl")
        ks = KillSwitch(tmp_path / "k.json", journal=j)
        path = tmp_path / "targets.json"

        class B:
            pass

        m1 = PositionManager(B(), j, ks, targets_path=path)
        when = datetime(2026, 9, 3, 16, 0, tzinfo=C.ET)
        m1.register("SPY260904C00780000", stop=Decimal("1.50"), time_exit=when)

        m2 = PositionManager(B(), j, ks, targets_path=path)
        t = m2._targets["SPY260904C00780000"]
        assert t["stop"] == Decimal("1.50")
        assert t["time_exit"] == when


class TestSchedulerCoversTheClose:
    def test_there_is_an_equity_tick_at_1600(self):
        """time_exit is MEASUREMENT_ET (16:00). With hour="9-15" no equity tick
        existed then, so the convex sleeve was carried past its flatten."""
        import inspect

        from glassbox.scheduler import Agent

        src = inspect.getsource(Agent.build)
        assert '"9-16"' in src or "'9-16'" in src


class TestPerSymbolOrderCapReachesOptions:
    def test_option_contracts_are_counted(self):
        """plan.symbol is the underlying; the order map is keyed by OCC
        contract. 100 orders in one contract passed an 8-order cap."""
        from glassbox.kernel import _underlying_of

        assert _underlying_of("SPY260904C00780000") == "SPY"
        assert _underlying_of("SPY") == "SPY"


class TestEveryEnvReadIsCleaned:
    def test_no_module_reads_credentials_with_raw_getenv(self):
        """An inline '#' comment in .env would make every option-chain call
        fail, and event_vol swallows that in a bare except -- the flagship
        strategy would go silent with nothing in the journal."""
        import pathlib

        offenders = []
        for path in pathlib.Path("glassbox").glob("*.py"):
            if path.name == "env.py":
                continue
            src = path.read_text(encoding="utf-8")
            for token in (
                "ALPACA_API_KEY",
                "ALPACA_SECRET_KEY",
                "ANTHROPIC_API_KEY",
                "DISCORD_WEBHOOK_URL",
            ):
                if f'os.getenv("{token}")' in src or f'os.environ["{token}"]' in src:
                    offenders.append(f"{path.name}:{token}")
        assert not offenders, f"raw env reads bypass env.clean(): {offenders}"
