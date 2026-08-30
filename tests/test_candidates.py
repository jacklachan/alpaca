from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from glassbox.candidates import (
    CANDIDATE_SCHEMA_VERSION,
    LIMIT_PRICE_RULE_VERSION,
    CandidateDataInvalid,
    OptionQuoteSnapshot,
    build_candidate_manifest,
    build_selection_receipt,
    canonical_hash,
    derive_limit_price,
)
from glassbox.schema import OptionLeg, TradePlan

OBSERVED_AT = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)


def _quote(
    symbol: str,
    *,
    contract_id: str | None = None,
    bid: Decimal | None = Decimal("1.10"),
    ask: Decimal | None = Decimal("1.14"),
    status: str = "active",
    tradable: bool = True,
    venue_timestamp: datetime | None = None,
) -> OptionQuoteSnapshot:
    return OptionQuoteSnapshot.capture(
        contract_id=contract_id or f"contract-{symbol}",
        symbol=symbol,
        status=status,
        tradable=tradable,
        quote_source="alpaca_option_chain",
        feed="indicative",
        venue_timestamp=venue_timestamp or OBSERVED_AT - timedelta(seconds=4),
        observed_at=OBSERVED_AT,
        bid=bid,
        ask=ask,
        max_age_seconds=Decimal("30"),
        max_spread_pct=Decimal("0.10"),
    )


def _candidate(
    underlying: str,
    call_limit: Decimal = Decimal("1.18"),
    put_limit: Decimal = Decimal("1.24"),
) -> TradePlan:
    call_symbol = f"{underlying}260904C00775000"
    put_symbol = f"{underlying}260904P00765000"
    call_quote = _quote(call_symbol)
    put_quote = _quote(put_symbol, bid=Decimal("1.16"), ask=Decimal("1.20"))
    premium = (call_limit + put_limit) * Decimal("100")
    return TradePlan(
        sleeve="convex",
        action="open",
        instrument="option",
        symbol=underlying,
        side="buy",
        option_legs=(
            OptionLeg(symbol=call_symbol, side="buy", qty=1, limit_price=call_limit),
            OptionLeg(symbol=put_symbol, side="buy", qty=1, limit_price=put_limit),
        ),
        notional_usd=premium,
        max_loss_usd=premium,
        time_exit=datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc),
        is_event_trade=True,
        event_key="ISM Services PMI",
        thesis="A deterministic event-volatility candidate with bounded premium at risk.",
        evidence=("active contracts and fresh Alpaca option quotes validated",),
        confidence=0.6,
        candidate_schema_version=CANDIDATE_SCHEMA_VERSION,
        limit_price_rule_version=LIMIT_PRICE_RULE_VERSION,
        quote_snapshots=(call_quote, put_quote),
    )


@pytest.mark.parametrize(
    ("status", "tradable", "reason"),
    [("inactive", True, "inactive_contract"), ("active", False, "untradable_contract")],
)
def test_candidate_requires_active_venue_contract(status: str, tradable: bool, reason: str) -> None:
    with pytest.raises(CandidateDataInvalid, match=reason):
        _quote("SPY260904C00775000", status=status, tradable=tradable)


def test_deserialized_quote_cannot_bypass_active_contract_invariant() -> None:
    payload = _quote("SPY260904C00775000").model_dump()
    payload["status"] = "inactive"

    with pytest.raises(ValueError):
        OptionQuoteSnapshot.model_validate(payload)


@pytest.mark.parametrize(
    ("bid", "ask", "venue_timestamp", "reason"),
    [
        (None, Decimal("1.14"), OBSERVED_AT - timedelta(seconds=4), "missing_quote"),
        (Decimal("1.10"), None, OBSERVED_AT - timedelta(seconds=4), "missing_quote"),
        (Decimal("1.10"), Decimal("1.14"), OBSERVED_AT - timedelta(seconds=31), "stale_quote"),
        (Decimal("1.10"), Decimal("1.14"), OBSERVED_AT + timedelta(seconds=1), "future_quote"),
    ],
)
def test_candidate_abstains_on_stale_or_missing_quote(
    bid: Decimal | None,
    ask: Decimal | None,
    venue_timestamp: datetime,
    reason: str,
) -> None:
    with pytest.raises(CandidateDataInvalid, match=reason):
        _quote(
            "SPY260904C00775000",
            bid=bid,
            ask=ask,
            venue_timestamp=venue_timestamp,
        )


def test_candidate_abstains_on_missing_quote_timestamp() -> None:
    with pytest.raises(CandidateDataInvalid, match="missing_quote_timestamp"):
        OptionQuoteSnapshot.capture(
            contract_id="contract-SPY260904C00775000",
            symbol="SPY260904C00775000",
            status="active",
            tradable=True,
            quote_source="alpaca_option_chain",
            feed="indicative",
            venue_timestamp=None,  # type: ignore[arg-type]
            observed_at=OBSERVED_AT,
            bid=Decimal("1.10"),
            ask=Decimal("1.14"),
            max_age_seconds=Decimal("30"),
            max_spread_pct=Decimal("0.10"),
        )


@pytest.mark.parametrize(
    ("bid", "ask", "reason"),
    [
        (Decimal("1.20"), Decimal("1.19"), "crossed_quote"),
        (Decimal("0"), Decimal("1.19"), "zero_quote"),
        (Decimal("1.00"), Decimal("1.30"), "wide_quote"),
    ],
)
def test_candidate_abstains_on_crossed_or_excessive_spread(
    bid: Decimal, ask: Decimal, reason: str
) -> None:
    with pytest.raises(CandidateDataInvalid, match=reason):
        _quote("SPY260904C00775000", bid=bid, ask=ask)


def test_candidate_uses_bid_ask_derived_limit_without_float_roundtrip() -> None:
    quote = _quote(
        "SPY260904C00775000",
        bid=Decimal("1.01"),
        ask=Decimal("1.07"),
    )

    limit = derive_limit_price(quote, tolerance=Decimal("0.03"))

    assert limit == Decimal("1.10")
    assert isinstance(limit, Decimal)


def test_candidate_manifest_is_order_stable_and_content_addressed() -> None:
    spy = _candidate("SPY")
    qqq = _candidate("QQQ")

    forward = build_candidate_manifest([spy, qqq])
    reversed_input = build_candidate_manifest([qqq, spy])
    changed = build_candidate_manifest([spy, _candidate("QQQ", put_limit=Decimal("1.25"))])

    assert forward == reversed_input
    assert forward.candidate_ids == tuple(sorted((spy.plan_id, qqq.plan_id)))
    assert forward.manifest_hash == canonical_hash(
        {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "candidates": forward.candidates,
        }
    )
    assert changed.manifest_hash != forward.manifest_hash


def test_candidate_id_changes_when_any_executable_field_changes() -> None:
    original = _candidate("SPY")
    changed_limit = _candidate("SPY", call_limit=Decimal("1.19"))

    assert original.plan_id != changed_limit.plan_id
    assert original.content_hash != changed_limit.content_hash


def test_provenance_candidate_refuses_caller_supplied_identity() -> None:
    candidate = _candidate("SPY")
    payload = candidate.model_dump(exclude={"plan_id", "content_hash"})

    with pytest.raises(ValueError, match="candidate plan ID"):
        TradePlan(plan_id="caller-controlled-id", **payload)


def test_selector_receipt_binds_prompt_model_candidate_set_and_response_hashes() -> None:
    manifest = build_candidate_manifest([_candidate("SPY"), _candidate("QQQ")])
    selector_input = {"candidate_manifest_hash": manifest.manifest_hash, "equity": "100000"}
    selector_output = {"candidate_id": manifest.candidate_ids[0], "rationale": "Best evidence."}

    receipt = build_selection_receipt(
        prompt="bounded-selector-v1",
        model="claude-opus-5",
        manifest=manifest,
        selector_input=selector_input,
        selector_output=selector_output,
    )

    assert receipt.prompt_hash == canonical_hash("bounded-selector-v1")
    assert receipt.model_hash == canonical_hash("claude-opus-5")
    assert receipt.candidate_manifest_hash == manifest.manifest_hash
    assert receipt.input_hash == canonical_hash(selector_input)
    assert receipt.output_hash == canonical_hash(selector_output)
    assert receipt.receipt_hash == canonical_hash(receipt.model_dump(exclude={"receipt_hash"}))
