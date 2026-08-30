from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from glassbox import config as C
from glassbox.kernel import PortfolioState
from glassbox.scheduler import Agent
from glassbox.schema import OptionLeg, TradePlan


class Journal:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []
        self.head = "0" * 64
        self.seq = 0

    def append(self, actor: str, event: str, payload: dict) -> None:
        self.events.append((actor, event, payload))


class Broker:
    def __init__(self, state: PortfolioState, environment: str = "scored") -> None:
        self.state = state
        self.env = environment
        self.reconciliations = 0

    def reconcile(self, **kwargs) -> PortfolioState:
        self.reconciliations += 1
        return self.state


class Manager:
    def __init__(self) -> None:
        self.kill = SimpleNamespace(tripped=False)
        self.ticks = 0

    def tick(self, state: PortfolioState) -> None:
        self.ticks += 1


class Kernel:
    def __init__(self) -> None:
        self.reviewed: list[TradePlan] = []

    def review(self, plan: TradePlan, state: PortfolioState):
        self.reviewed.append(plan)
        return SimpleNamespace(
            approved=True,
            reason="all invariants passed",
            checks_passed=13,
            checks_total=13,
            failed_invariant=None,
        )


class Strategy:
    def __init__(self, plans: list[TradePlan]) -> None:
        self.plans = plans
        self.calls = 0

    def propose_from_state(self, state, positioned) -> list[TradePlan]:
        self.calls += 1
        return self.plans


class Thesis:
    def __init__(self, selected: TradePlan | None) -> None:
        self.selected = selected
        self.offered: list[TradePlan] | None = None

    def select(self, candidates, state, journal):
        self.offered = candidates
        return self.selected


def _state() -> PortfolioState:
    return PortfolioState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        market_open=True,
        now_et=datetime(2026, 8, 31, 10, 0, tzinfo=C.ET),
    )


def _option(plan_id: str, underlying: str) -> TradePlan:
    return TradePlan(
        plan_id=plan_id,
        sleeve="convex",
        action="open",
        instrument="option",
        symbol=underlying,
        option_legs=[
            OptionLeg(
                symbol=f"{underlying}260904C00600000",
                side="buy",
                qty=1,
                limit_price=Decimal("2.00"),
            )
        ],
        side="buy",
        notional_usd=Decimal("200"),
        max_loss_usd=Decimal("200"),
        thesis="A deterministic and fully pre-priced option candidate for selection.",
        evidence=["Deterministic event and option-surface screen passed."],
        confidence=0.7,
    )


def _equity(plan_id: str = "equity-a") -> TradePlan:
    return TradePlan(
        plan_id=plan_id,
        sleeve="core",
        action="open",
        instrument="equity",
        symbol="SPY",
        side="buy",
        notional_usd=Decimal("500"),
        max_loss_usd=Decimal("50"),
        thesis="An injected equity plan that the scored policy must always refuse.",
        evidence=["Policy boundary test fixture."],
        confidence=0.5,
    )


def _agent(tmp_path, monkeypatch, strategies: dict, thesis: Thesis):
    monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(tmp_path / "positioned.json"))
    broker = Broker(_state())
    journal = Journal()
    kernel = Kernel()
    agent = Agent(broker, journal, kernel, Manager(), strategies, thesis)
    submitted: list[TradePlan] = []
    agent.execute = lambda plan, verdict: submitted.append(plan)  # type: ignore[method-assign]
    return agent, journal, kernel, submitted


def test_scored_cycle_offers_all_option_candidates_and_executes_only_selection(
    tmp_path, monkeypatch
) -> None:
    spy = _option("spy-a", "SPY")
    qqq = _option("qqq-a", "QQQ")
    thesis = Thesis(qqq)
    agent, journal, kernel, submitted = _agent(
        tmp_path,
        monkeypatch,
        {"event_spy": Strategy([spy]), "event_qqq": Strategy([qqq])},
        thesis,
    )

    agent.equity_tick()

    assert thesis.offered == [spy, qqq]
    assert kernel.reviewed == [qqq]
    assert submitted == [qqq]


def test_scored_abstention_submits_nothing(tmp_path, monkeypatch) -> None:
    thesis = Thesis(None)
    agent, journal, kernel, submitted = _agent(
        tmp_path, monkeypatch, {"event_spy": Strategy([_option("spy-a", "SPY")])}, thesis
    )

    agent.equity_tick()

    assert thesis.offered is not None
    assert kernel.reviewed == []
    assert submitted == []


def test_scored_policy_refuses_injected_non_option_before_ai(tmp_path, monkeypatch) -> None:
    option = _option("spy-a", "SPY")
    thesis = Thesis(None)
    agent, journal, kernel, submitted = _agent(
        tmp_path,
        monkeypatch,
        {"injected": Strategy([_equity(), option])},
        thesis,
    )

    agent.equity_tick()

    assert thesis.offered == [option]
    assert kernel.reviewed == []
    assert submitted == []
    refusal = next(
        payload for _, event, payload in journal.events if event == "SCORED_POLICY_REFUSED"
    )
    assert refusal["plan_id"] == "equity-a"
    assert refusal["instrument"] == "equity"


def test_scored_policy_rechecks_object_returned_by_selector(tmp_path, monkeypatch) -> None:
    injected = _equity()
    thesis = Thesis(injected)
    agent, journal, kernel, submitted = _agent(
        tmp_path,
        monkeypatch,
        {"event_spy": Strategy([_option("spy-a", "SPY")])},
        thesis,
    )

    agent.equity_tick()

    assert kernel.reviewed == []
    assert submitted == []
    assert journal.events[-1][1] == "SCORED_POLICY_REFUSED"


def test_scored_schedule_omits_crypto_job(tmp_path, monkeypatch) -> None:
    scored, *_ = _agent(tmp_path, monkeypatch, {}, Thesis(None))
    scored_ids = {job.id for job in scored.build().get_jobs()}

    monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(tmp_path / "dev-positioned.json"))
    dev = Agent(Broker(_state(), "dev"), Journal(), Kernel(), Manager(), {}, Thesis(None))
    dev_ids = {job.id for job in dev.build().get_jobs()}

    assert "crypto_tick" not in scored_ids
    assert "crypto_tick" in dev_ids


def test_scored_strategy_construction_is_options_only() -> None:
    from main import strategy_set

    strategies = strategy_set("scored", data=object())

    assert set(strategies) == {"event_vol_spy", "event_vol_qqq"}
    assert {strategy.underlying for strategy in strategies.values()} == {"SPY", "QQQ"}


def test_development_strategy_construction_keeps_connectivity_sleeves() -> None:
    from main import strategy_set

    strategies = strategy_set("dev", data=object())

    assert {"event_vol_spy", "event_vol_qqq", "core", "crypto"} == set(strategies)
