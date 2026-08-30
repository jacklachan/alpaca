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


# -- ledger wiring (scored path) ----------------------------------------------


def _ledger_agent(tmp_path, monkeypatch, *, positions=(), open_orders=(), owned=None):
    """A scored agent with a real ledger, wired the way main.py wires it."""
    from glassbox.position_ledger import PositionLedger

    monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(tmp_path / "positioned.json"))

    book = PositionLedger(account_id="PA-1", environment="scored")
    for symbol, qty in (owned or {}).items():
        book.record_entry_fill(
            plan_id="gbp-1",
            symbol=symbol,
            client_order_id=f"gbx-{symbol}",
            filled_qty=Decimal(str(qty)),
            side="buy",
        )

    state = _state()
    state.positions = list(positions)
    broker = Broker(state)
    broker.open_orders = lambda: list(open_orders)
    agent = Agent(
        broker,
        Journal(),
        Kernel(),
        Manager(),
        {},
        Thesis(None),
        ledger=book,
        ledger_path=tmp_path / "ledger.json",
    )
    return agent, book


def _pos(symbol: str, qty: str):
    return SimpleNamespace(symbol=symbol, instrument="option", qty=Decimal(qty))


CALL = "SPY260904C00600000"


def test_scored_tick_offers_candidates_when_the_ledger_agrees_with_the_venue(tmp_path, monkeypatch):
    agent, _ = _ledger_agent(tmp_path, monkeypatch, positions=[_pos(CALL, "3")], owned={CALL: 3})
    offered: list = []
    agent._scored_selection_tick = lambda state: offered.append(state)

    agent.equity_tick()

    assert offered, "a reconciled book must not block the selection tick"
    assert agent._state_faulted is False


def test_scored_tick_refuses_new_entries_when_venue_disagrees(tmp_path, monkeypatch):
    """Expected three, venue holds one. Until that is explained we do not know
    what we own, and sizing against an unknown book compounds the error."""
    agent, _ = _ledger_agent(tmp_path, monkeypatch, positions=[_pos(CALL, "1")], owned={CALL: 3})
    offered: list = []
    agent._scored_selection_tick = lambda state: offered.append(state)

    agent.equity_tick()

    assert not offered, "new risk was allowed on an unreconciled book"
    assert agent._state_faulted is True
    events = [e for _, e, _ in agent.journal.events]
    assert "POSITION_RECONCILE_FAULT" in events


def test_unknown_venue_exposure_blocks_the_scored_tick(tmp_path, monkeypatch):
    agent, _ = _ledger_agent(
        tmp_path,
        monkeypatch,
        positions=[_pos(CALL, "3"), _pos("QQQ260904P00400000", "5")],
        owned={CALL: 3},
    )
    offered: list = []
    agent._scored_selection_tick = lambda state: offered.append(state)

    agent.equity_tick()

    assert not offered
    reasons = [p for _, e, p in agent.journal.events if e == "POSITION_RECONCILE_FAULT"]
    assert any("foreign_position" in str(r) for r in reasons)


def test_a_foreign_open_order_blocks_the_scored_tick(tmp_path, monkeypatch):
    agent, _ = _ledger_agent(
        tmp_path,
        monkeypatch,
        positions=[_pos(CALL, "3")],
        open_orders=[SimpleNamespace(symbol=CALL, client_order_id="not-ours")],
        owned={CALL: 3},
    )
    offered: list = []
    agent._scored_selection_tick = lambda state: offered.append(state)

    agent.equity_tick()

    assert not offered
    assert agent._state_faulted is True


def test_an_unreadable_open_order_list_fails_closed(tmp_path, monkeypatch):
    agent, _ = _ledger_agent(tmp_path, monkeypatch, positions=[_pos(CALL, "3")], owned={CALL: 3})

    def unavailable():
        raise RuntimeError("venue unreachable")

    agent.broker.open_orders = unavailable
    offered: list = []
    agent._scored_selection_tick = lambda state: offered.append(state)

    agent.equity_tick()

    assert not offered, "we could not see open orders and traded anyway"
    assert agent._state_faulted is True


def test_confirmed_fills_are_recorded_and_persisted(tmp_path, monkeypatch):
    """Only confirmed fills move the ledger, and they survive a restart."""
    from glassbox.position_ledger import PositionLedger

    agent, book = _ledger_agent(tmp_path, monkeypatch)
    plan = _option("gbp-fill", "SPY")
    leg_symbol = plan.option_legs[0].symbol
    result = SimpleNamespace(
        ok=True,
        legs=[
            SimpleNamespace(symbol=leg_symbol, filled_qty=Decimal(2), client_order_id="gbx-a"),
            SimpleNamespace(
                symbol="SPY260904C00700000", filled_qty=Decimal(0), client_order_id="gbx-b"
            ),
        ],
    )

    agent._record_fills(plan, result)

    assert book.entries[leg_symbol].signed_qty == Decimal(2)
    assert "SPY260904C00700000" not in book.entries, "an unfilled leg moved the ledger"

    restored = PositionLedger.load(
        tmp_path / "ledger.json", account_id="PA-1", environment="scored"
    )
    assert restored.entries[leg_symbol].signed_qty == Decimal(2)


def test_partial_fills_on_a_failed_execution_are_still_recorded(tmp_path, monkeypatch):
    """Exposure we do not record is exposure we cannot exit."""
    agent, book = _ledger_agent(tmp_path, monkeypatch)
    plan = _option("gbp-partial", "SPY")
    leg_symbol = plan.option_legs[0].symbol
    result = SimpleNamespace(
        ok=False,
        legs=[SimpleNamespace(symbol=leg_symbol, filled_qty=Decimal(1), client_order_id="gbx-a")],
    )

    agent._record_fills(plan, result)

    assert book.entries[leg_symbol].signed_qty == Decimal(1)


def test_the_development_agent_has_no_ledger_and_is_unaffected(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "POSITIONED_STATE_FILE", str(tmp_path / "positioned.json"))
    agent = Agent(Broker(_state(), "dev"), Journal(), Kernel(), Manager(), {}, Thesis(None))
    assert agent.ledger is None
    assert agent._ledger_reconciled(_state()) is True


def test_foreign_equity_on_the_scored_account_blocks_the_tick(tmp_path, monkeypatch):
    """The scored account is options-only. An equity position on it is
    unaccounted exposure, and filtering reconciliation to options would hide
    precisely the case worth catching."""
    agent, _ = _ledger_agent(
        tmp_path,
        monkeypatch,
        positions=[
            _pos(CALL, "3"),
            SimpleNamespace(symbol="SPY", instrument="equity", qty=Decimal("100")),
        ],
        owned={CALL: 3},
    )
    offered: list = []
    agent._scored_selection_tick = lambda state: offered.append(state)

    agent.equity_tick()

    assert not offered
    reasons = [p for _, e, p in agent.journal.events if e == "POSITION_RECONCILE_FAULT"]
    assert any("SPY" in str(r) for r in reasons)


# -- verifiable evidence: offered set and counterfactual verdicts --------------


def test_the_offered_candidate_set_is_recorded_with_its_ids(tmp_path, monkeypatch):
    """The set the model was allowed to choose from must be on the record, or
    a selection naming something else is deniable rather than detectable."""
    spy, qqq = _option("spy-a", "SPY"), _option("qqq-a", "QQQ")
    agent, journal, _, _ = _agent(
        tmp_path,
        monkeypatch,
        {"event_spy": Strategy([spy]), "event_qqq": Strategy([qqq])},
        Thesis(qqq),
    )

    agent.equity_tick()

    built = [p for _, e, p in journal.events if e == "CANDIDATE_SET_BUILT"]
    assert len(built) == 1
    assert built[0]["count"] == 2
    assert set(built[0]["candidate_ids"]) == {"spy-a", "qqq-a"}
    # These fixtures are bare TradePlans with no candidate provenance, so the
    # manifest builder correctly refuses them -- and the refusal is recorded
    # rather than silently degraded to an empty hash.
    assert built[0]["manifest_unavailable"], "a manifest failure was hidden"
    assert "schema" in built[0]["manifest_unavailable"]


def test_an_abstention_still_records_what_was_offered(tmp_path, monkeypatch):
    spy = _option("spy-a", "SPY")
    agent, journal, kernel, submitted = _agent(
        tmp_path, monkeypatch, {"event_spy": Strategy([spy])}, Thesis(None)
    )

    agent.equity_tick()

    assert [e for _, e, _ in journal.events].count("CANDIDATE_SET_BUILT") == 1
    assert submitted == []
    # An abstention must remain a cycle in which the deciding kernel never ran.
    assert kernel.reviewed == []


def test_candidates_the_model_did_not_take_get_their_own_kernel_verdict(tmp_path, monkeypatch):
    """This is the evidence that the model chose inside a pre-vetted set
    rather than being trusted with the outcome."""
    spy, qqq = _option("spy-a", "SPY"), _option("qqq-a", "QQQ")
    agent, journal, kernel, submitted = _agent(
        tmp_path,
        monkeypatch,
        {"event_spy": Strategy([spy]), "event_qqq": Strategy([qqq])},
        Thesis(qqq),
    )

    agent.equity_tick()

    shadow = [p for a, e, p in journal.events if e == "CANDIDATE_KERNEL_VERDICT"]
    assert [s["plan_id"] for s in shadow] == ["spy-a"], "the unchosen candidate has no verdict"
    assert shadow[0]["selected"] is False
    assert "approved" in shadow[0]

    # The deciding kernel still saw only the selected object.
    assert kernel.reviewed == [qqq]
    assert submitted == [qqq]


def test_evidence_gathering_cannot_break_a_tick(tmp_path, monkeypatch):
    """A failure while recording evidence must cost evidence, never the trade
    decision."""
    spy, qqq = _option("spy-a", "SPY"), _option("qqq-a", "QQQ")
    agent, _, _, submitted = _agent(
        tmp_path,
        monkeypatch,
        {"event_spy": Strategy([spy]), "event_qqq": Strategy([qqq])},
        Thesis(qqq),
    )

    class Exploding:
        def review(self, plan, state):
            raise RuntimeError("shadow kernel blew up")

    agent._shadow = Exploding()

    agent.equity_tick()

    assert submitted == [qqq], "an evidence failure changed the trading outcome"


def test_a_real_provenance_carrying_candidate_set_is_content_addressed(tmp_path, monkeypatch):
    """The bare fixtures above cannot be manifested by design. A real
    candidate, carrying its quote provenance, must produce a stable hash --
    otherwise the evidence trail claims determinism it cannot show."""
    import tests.test_candidates as fixtures

    spy = fixtures._candidate("SPY")
    qqq = fixtures._candidate("QQQ")
    agent, journal, _, _ = _agent(
        tmp_path,
        monkeypatch,
        {"event_spy": Strategy([spy]), "event_qqq": Strategy([qqq])},
        Thesis(qqq),
    )

    agent.equity_tick()

    built = [p for _, e, p in journal.events if e == "CANDIDATE_SET_BUILT"][0]
    assert built["manifest_unavailable"] is None
    assert len(built["manifest_hash"]) == 64, "the offered set is not content-addressed"
    assert set(built["candidate_ids"]) == {spy.plan_id, qqq.plan_id}

    # Determinism: the same set, offered again, hashes identically.
    agent.journal.events.clear()
    agent.equity_tick()
    again = [p for _, e, p in agent.journal.events if e == "CANDIDATE_SET_BUILT"][0]
    assert again["manifest_hash"] == built["manifest_hash"]
