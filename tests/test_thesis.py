from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from glassbox import config as C
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
    return TradePlan(
        plan_id=plan_id,
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
        thesis="Deterministic candidate with a fully bounded premium at risk.",
        evidence=["Deterministic surface and calendar screen passed."],
        confidence=0.7,
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
    layer = _layer_returning({"candidate_id": "candidate-b", "rationale": "Best bounded setup."})

    selected = layer.select(candidates, _state(), journal)

    assert selected is candidates[1]
    assert journal.events[-1][1] == "CANDIDATE_SELECTED"
    assert journal.events[-1][2]["candidate_id"] == "candidate-b"


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

    assert (
        _layer_returning(response).select([_option_candidate("candidate-a")], _state(), journal)
        is None
    )

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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
