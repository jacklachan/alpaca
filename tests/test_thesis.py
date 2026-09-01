from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from glassbox import config as C
from glassbox.candidates import (
    CANDIDATE_SCHEMA_VERSION,
    LIMIT_PRICE_RULE_VERSION,
    OptionQuoteSnapshot,
    build_candidate_manifest,
    canonical_hash,
)
from glassbox.kernel import PortfolioState
from glassbox.schema import OptionLeg, TradePlan
from glassbox.thesis import ThesisLayer


class Journal:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def append(self, actor: str, event: str, payload: dict) -> None:
        self.events.append((actor, event, payload))


def _state() -> PortfolioState:
    return PortfolioState(
        equity=Decimal("100000"),
        cash=Decimal("100000"),
        now_et=datetime(2026, 8, 31, 10, 0, tzinfo=C.ET),
    )


def _option_candidate(plan_id: str, underlying: str = "SPY") -> TradePlan:
    root = f"{underlying}260904C00600000"
    observed_at = datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)
    quote = OptionQuoteSnapshot.capture(
        contract_id=f"contract-{root}",
        symbol=root,
        status="active",
        tradable=True,
        quote_source="alpaca_option_chain",
        feed="indicative",
        venue_timestamp=observed_at - timedelta(seconds=3),
        observed_at=observed_at,
        bid=Decimal("2.05"),
        ask=Decimal("2.09"),
        max_age_seconds=Decimal("30"),
        max_spread_pct=Decimal("0.055"),
    )
    return TradePlan(
        sleeve="convex",
        action="open",
        instrument="option",
        symbol=underlying,
        option_legs=[
            OptionLeg(
                symbol=root,
                side="buy",
                qty=1,
                limit_price=Decimal("2.15"),
            )
        ],
        side="buy",
        notional_usd=Decimal("215"),
        max_loss_usd=Decimal("215"),
        event_key=f"fixture-{plan_id}",
        thesis="Deterministic candidate with a fully bounded premium at risk.",
        evidence=["Deterministic surface and calendar screen passed."],
        confidence=0.7,
        candidate_schema_version=CANDIDATE_SCHEMA_VERSION,
        limit_price_rule_version=LIMIT_PRICE_RULE_VERSION,
        quote_snapshots=(quote,),
    )


def _equity_candidate(plan_id: str) -> TradePlan:
    return TradePlan(
        plan_id=plan_id,
        sleeve="core",
        action="open",
        instrument="equity",
        symbol="SPY",
        side="buy",
        notional_usd=Decimal("500"),
        max_loss_usd=Decimal("50"),
        thesis="A deliberately non-option candidate used to test the boundary.",
        evidence=["Test fixture."],
        confidence=0.5,
    )


def _layer_returning(response: object) -> ThesisLayer:
    layer = ThesisLayer()
    layer._ask_selection = lambda payload: response  # type: ignore[method-assign]
    return layer


def test_selection_returns_the_exact_original_candidate() -> None:
    candidates = [_option_candidate("candidate-a"), _option_candidate("candidate-b", "QQQ")]
    journal = Journal()
    layer = _layer_returning(
        {"candidate_id": candidates[1].plan_id, "rationale": "Best bounded setup."}
    )

    selected = layer.select(candidates, _state(), journal)

    assert selected is candidates[1]
    assert journal.events[-1][1] == "CANDIDATE_SELECTED"
    assert journal.events[-1][2]["candidate_id"] == candidates[1].plan_id


def test_selector_receipt_binds_prompt_model_candidate_set_and_response_hashes() -> None:
    candidates = [_option_candidate("candidate-b", "QQQ"), _option_candidate("candidate-a")]
    response = {"candidate_id": candidates[1].plan_id, "rationale": "Best bounded setup."}
    journal = Journal()
    captured: dict = {}
    layer = ThesisLayer(model="selector-model-v1")

    def select(payload: dict) -> object:
        captured.update(payload)
        return response

    layer._ask_selection = select  # type: ignore[method-assign]

    selected = layer.select(candidates, _state(), journal)

    manifest = build_candidate_manifest(candidates)
    assert selected is candidates[1]
    assert [row["candidate_id"] for row in captured["candidates"]] == list(manifest.candidate_ids)
    assert captured["candidate_manifest_hash"] == manifest.manifest_hash
    assert journal.events[0][1] == "CANDIDATE_SET_BUILT"
    receipt = journal.events[-1][2]["selector_receipt"]
    assert receipt["candidate_manifest_hash"] == manifest.manifest_hash
    assert receipt["model_hash"] == canonical_hash("selector-model-v1")
    assert receipt["input_hash"] == canonical_hash(captured)
    assert receipt["output_hash"] == canonical_hash(response)


def test_candidate_trade_fields_are_deeply_immutable() -> None:
    candidate = _option_candidate("candidate-a")

    with pytest.raises(AttributeError):
        candidate.option_legs.append(  # type: ignore[attr-defined]
            OptionLeg(
                symbol="SPY260904P00500000",
                side="buy",
                qty=99,
                limit_price=Decimal("9.99"),
            )
        )
    with pytest.raises(AttributeError):
        candidate.evidence.append("model-added evidence")  # type: ignore[attr-defined]


def test_explicit_abstention_returns_no_candidate() -> None:
    journal = Journal()
    layer = _layer_returning({"candidate_id": None, "rationale": "No setup clears the bar."})

    assert layer.select([_option_candidate("candidate-a")], _state(), journal) is None

    assert journal.events[-1][1] == "CANDIDATE_ABSTAINED"


@pytest.mark.parametrize(
    "response",
    [
        {"candidate_id": "unknown", "rationale": "Invented identifier."},
        {"candidate_id": "candidate-a"},
        {"candidate_id": "candidate-a", "rationale": "x", "qty": 999},
        ["candidate-a"],
        "candidate-a",
    ],
)
def test_unknown_or_malformed_selection_abstains(response: object) -> None:
    journal = Journal()
    candidate = _option_candidate("candidate-a")
    if isinstance(response, dict) and response.get("candidate_id") == "candidate-a":
        response = {**response, "candidate_id": candidate.plan_id}

    assert _layer_returning(response).select([candidate], _state(), journal) is None

    assert journal.events[-1][1] == "CANDIDATE_SELECTION_INVALID"


def test_model_failure_abstains_without_raising() -> None:
    journal = Journal()
    layer = ThesisLayer()

    def fail(payload: dict) -> object:
        raise TimeoutError("model timed out")

    layer._ask_selection = fail  # type: ignore[method-assign]

    assert layer.select([_option_candidate("candidate-a")], _state(), journal) is None
    assert journal.events[-1][1] == "CANDIDATE_SELECTION_UNAVAILABLE"


def test_missing_credentials_abstains_without_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Every name the layer will accept, or this asserts nothing: with any one
    # of them set the layer finds a key and attempts a real call.
    for name in ("LLM_API_KEY", "FEATHERLESS_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    journal = Journal()

    assert ThesisLayer().select([_option_candidate("candidate-a")], _state(), journal) is None
    assert journal.events[-1][1] == "CANDIDATE_SELECTION_UNAVAILABLE"


def test_non_option_candidate_is_never_selectable() -> None:
    journal = Journal()
    layer = _layer_returning({"candidate_id": "equity-a", "rationale": "Try to escape policy."})

    assert (
        layer.select(
            [_equity_candidate("equity-a"), _option_candidate("option-a")],
            _state(),
            journal,
        )
        is None
    )

    assert journal.events[-1][1] == "CANDIDATE_SELECTION_INVALID"


def test_empty_candidate_set_abstains_without_calling_model() -> None:
    journal = Journal()
    layer = ThesisLayer()

    def fail_if_called(payload: dict) -> object:
        raise AssertionError("model must not be called")

    layer._ask_selection = fail_if_called  # type: ignore[method-assign]

    assert layer.select([], _state(), journal) is None
    assert journal.events[-1][1] == "CANDIDATE_ABSTAINED"
