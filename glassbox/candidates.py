"""Immutable market-data provenance and content addressing for option candidates."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal, Mapping, Protocol, Sequence, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

CANDIDATE_SCHEMA_VERSION = "1"
LIMIT_PRICE_RULE_VERSION = "ask-plus-tolerance-v1"
_CENT = Decimal("0.01")


class CandidateDataInvalid(ValueError):
    """A venue contract or quote cannot safely support an executable candidate."""


def _field(record: object, name: str) -> object:
    if isinstance(record, Mapping):
        return record.get(name)
    return getattr(record, name, None)


def _enum_text(value: object) -> str:
    return str(getattr(value, "value", value)).lower()


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def canonical_hash(value: Any) -> str:
    """Hash one canonical JSON representation without float coercion."""
    body = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class ActiveOptionContract(BaseModel):
    """Validated server-authoritative identity for one option contract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    status: Literal["active"]
    tradable: Literal[True]
    expiration_date: date
    underlying_symbol: str
    contract_type: Literal["call", "put"]

    @classmethod
    def capture(
        cls,
        record: object,
        *,
        underlying: str,
        expiry: date,
        contract_type: str,
    ) -> "ActiveOptionContract":
        """Validate an SDK model or its documented dictionary fallback."""
        status = _enum_text(_field(record, "status"))
        if status != "active":
            raise CandidateDataInvalid("inactive_contract")
        if _field(record, "tradable") is not True:
            raise CandidateDataInvalid("untradable_contract")
        expiration_date = _field(record, "expiration_date")
        if isinstance(expiration_date, str):
            try:
                expiration_date = date.fromisoformat(expiration_date)
            except ValueError as exc:
                raise CandidateDataInvalid("invalid_contract_record") from exc
        captured_type = _enum_text(_field(record, "type"))
        if not isinstance(expiration_date, date) or captured_type not in ("call", "put"):
            raise CandidateDataInvalid("invalid_contract_record")
        try:
            captured = cls(
                contract_id=str(_field(record, "id") or ""),
                symbol=str(_field(record, "symbol") or "").upper(),
                status="active",
                tradable=True,
                expiration_date=expiration_date,
                underlying_symbol=str(_field(record, "underlying_symbol") or "").upper(),
                contract_type=cast(Literal["call", "put"], captured_type),
            )
        except ValidationError as exc:
            raise CandidateDataInvalid("invalid_contract_record") from exc
        if captured.underlying_symbol != underlying.upper():
            raise CandidateDataInvalid("contract_underlying_mismatch")
        if captured.expiration_date != expiry:
            raise CandidateDataInvalid("contract_expiry_mismatch")
        if captured.contract_type != contract_type:
            raise CandidateDataInvalid("contract_type_mismatch")
        return captured


class OptionQuoteSnapshot(BaseModel):
    """One validated active Alpaca contract and its point-in-time quote."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    status: Literal["active"]
    tradable: Literal[True]
    quote_source: str = Field(min_length=1)
    feed: str = Field(min_length=1)
    venue_timestamp: datetime
    observed_at: datetime
    age_seconds: Decimal = Field(ge=0)
    bid: Decimal = Field(gt=0)
    ask: Decimal = Field(gt=0)
    spread_pct: Decimal = Field(ge=0)

    @field_validator("symbol")
    @classmethod
    def _uppercase_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @classmethod
    def capture(
        cls,
        *,
        contract_id: str,
        symbol: str,
        status: str,
        tradable: bool,
        quote_source: str,
        feed: str,
        venue_timestamp: datetime,
        observed_at: datetime,
        bid: Decimal | None,
        ask: Decimal | None,
        max_age_seconds: Decimal,
        max_spread_pct: Decimal,
    ) -> "OptionQuoteSnapshot":
        """Validate venue authority, freshness, and quote coherence."""
        if status.lower() != "active":
            raise CandidateDataInvalid("inactive_contract")
        if not tradable:
            raise CandidateDataInvalid("untradable_contract")
        if bid is None or ask is None:
            raise CandidateDataInvalid("missing_quote")
        if bid <= 0 or ask <= 0:
            raise CandidateDataInvalid("zero_quote")
        if ask < bid:
            raise CandidateDataInvalid("crossed_quote")
        if not isinstance(venue_timestamp, datetime):
            raise CandidateDataInvalid("missing_quote_timestamp")
        if not isinstance(observed_at, datetime):
            raise CandidateDataInvalid("invalid_observation_timestamp")
        if venue_timestamp.tzinfo is None or observed_at.tzinfo is None:
            raise CandidateDataInvalid("naive_quote_timestamp")

        age_seconds = Decimal(str((observed_at - venue_timestamp).total_seconds()))
        if age_seconds < 0:
            raise CandidateDataInvalid("future_quote")
        if age_seconds > max_age_seconds:
            raise CandidateDataInvalid("stale_quote")

        midpoint = (bid + ask) / Decimal("2")
        spread_pct = (ask - bid) / midpoint
        if spread_pct > max_spread_pct:
            raise CandidateDataInvalid("wide_quote")

        return cls(
            contract_id=contract_id,
            symbol=symbol,
            status="active",
            tradable=tradable,
            quote_source=quote_source,
            feed=feed,
            venue_timestamp=venue_timestamp,
            observed_at=observed_at,
            age_seconds=age_seconds,
            bid=bid,
            ask=ask,
            spread_pct=spread_pct,
        )


def derive_limit_price(quote: OptionQuoteSnapshot, *, tolerance: Decimal) -> Decimal:
    """Derive a bounded marketable buy limit using Decimal arithmetic only."""
    if tolerance < 0:
        raise ValueError("limit tolerance cannot be negative")
    return (quote.ask * (Decimal("1") + tolerance)).quantize(_CENT, rounding=ROUND_HALF_UP)


class _Candidate(Protocol):
    @property
    def plan_id(self) -> str: ...

    @property
    def instrument(self) -> str: ...

    @property
    def content_hash(self) -> str: ...

    @property
    def candidate_schema_version(self) -> str | None: ...

    @property
    def quote_snapshots(self) -> tuple[OptionQuoteSnapshot, ...]: ...


class CandidateManifestEntry(BaseModel):
    """Content identity for one offered candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str
    content_hash: str


class CandidateManifest(BaseModel):
    """Canonical, order-stable identity of the complete selector offer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = CANDIDATE_SCHEMA_VERSION
    candidates: tuple[CandidateManifestEntry, ...]
    manifest_hash: str

    @property
    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(candidate.candidate_id for candidate in self.candidates)


def build_candidate_manifest(candidates: Sequence[_Candidate]) -> CandidateManifest:
    """Validate and canonically address an offered option-candidate set."""
    entries: list[CandidateManifestEntry] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.instrument != "option":
            raise CandidateDataInvalid("non_option_candidate")
        if candidate.candidate_schema_version != CANDIDATE_SCHEMA_VERSION:
            raise CandidateDataInvalid("unsupported_candidate_schema")
        if not candidate.quote_snapshots:
            raise CandidateDataInvalid("missing_quote_provenance")
        if not candidate.content_hash:
            raise CandidateDataInvalid("missing_candidate_hash")
        if candidate.plan_id in seen:
            raise CandidateDataInvalid("duplicate_candidate_id")
        seen.add(candidate.plan_id)
        entries.append(
            CandidateManifestEntry(
                candidate_id=candidate.plan_id,
                content_hash=candidate.content_hash,
            )
        )

    ordered = tuple(sorted(entries, key=lambda entry: entry.candidate_id))
    manifest_payload = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidates": ordered,
    }
    return CandidateManifest(candidates=ordered, manifest_hash=canonical_hash(manifest_payload))


class SelectionReceipt(BaseModel):
    """Content-addressed proof of one bounded selector interaction."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_hash: str
    model_hash: str
    candidate_manifest_hash: str
    input_hash: str
    output_hash: str
    receipt_hash: str


def build_selection_receipt(
    *,
    prompt: str,
    model: str,
    manifest: CandidateManifest,
    selector_input: object,
    selector_output: object,
) -> SelectionReceipt:
    """Bind prompt, model, candidate set, input, and response to one receipt."""
    payload = {
        "prompt_hash": canonical_hash(prompt),
        "model_hash": canonical_hash(model),
        "candidate_manifest_hash": manifest.manifest_hash,
        "input_hash": canonical_hash(selector_input),
        "output_hash": canonical_hash(selector_output),
    }
    return SelectionReceipt(**payload, receipt_hash=canonical_hash(payload))
