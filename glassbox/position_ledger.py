"""What this strategy believes it owns, and the proof that the venue agrees.

The distinction this module exists to hold: a *position in a symbol* is not the
same thing as *our position in a symbol*. Alpaca reports the first. Only we can
know the second, and only by deriving it from fills we actually confirmed.

That difference is why symbol-wide close is banned as an exit. `close_position`
liquidates everything the account holds in a contract. If anything else in the
account is in the same contract -- another strategy, a manual trade, a leftover
from a previous run -- a symbol-wide close silently takes it too, and its
success tells us nothing about whether *our* quantity went to zero.

So the ledger:

  * derives expected signed quantity only from confirmed fills, never from an
    order we sent or an intent we journaled;
  * reconciles that expectation against the venue exactly, per contract;
  * fails closed on anything it cannot explain -- a contract we do not know, a
    quantity that does not match, an open order whose client id is not ours;
  * proves flat only from a terminal order plus a zero venue quantity.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from .state import StateCorrupt, atomic_write_json, read_json

SCHEMA_VERSION = 2


def _decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal(0)
    return Decimal(str(value))


def _quantity(value: object, *, field_name: str) -> Decimal:
    try:
        quantity = _decimal(value)
    except Exception as exc:
        raise StateCorrupt(f"{field_name} is not a decimal quantity: {value!r}") from exc
    if not quantity.is_finite():
        raise StateCorrupt(f"{field_name} is not finite: {value!r}")
    return quantity


@dataclass(frozen=True)
class FillCursor:
    """Last cumulative venue fill observed for one immutable order identity."""

    client_order_id: str
    plan_id: str
    symbol: str
    purpose: str
    side: str
    requested_qty: Decimal
    cumulative_filled_qty: Decimal

    @property
    def identity(self) -> tuple[str, str, str, str, Decimal]:
        return (self.plan_id, self.symbol, self.purpose, self.side, self.requested_qty)

    def to_json(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "plan_id": self.plan_id,
            "symbol": self.symbol,
            "purpose": self.purpose,
            "side": self.side,
            "requested_qty": str(self.requested_qty),
            "cumulative_filled_qty": str(self.cumulative_filled_qty),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> FillCursor:
        cursor = cls(
            client_order_id=str(raw["client_order_id"]),
            plan_id=str(raw["plan_id"]),
            symbol=str(raw["symbol"]),
            purpose=str(raw["purpose"]),
            side=str(raw["side"]).lower(),
            requested_qty=_quantity(raw["requested_qty"], field_name="requested_qty"),
            cumulative_filled_qty=_quantity(
                raw["cumulative_filled_qty"], field_name="cumulative_filled_qty"
            ),
        )
        _validate_cursor(cursor)
        return cursor


def _validate_cursor(cursor: FillCursor) -> None:
    if not cursor.client_order_id:
        raise StateCorrupt("confirmed fill has no client_order_id")
    if not cursor.plan_id or not cursor.symbol:
        raise StateCorrupt(f"fill cursor {cursor.client_order_id} has no plan or symbol identity")
    if cursor.purpose not in {"entry", "exit"}:
        raise StateCorrupt(f"fill cursor {cursor.client_order_id} has invalid purpose")
    if cursor.side not in {"buy", "sell"}:
        raise StateCorrupt(f"fill cursor {cursor.client_order_id} has invalid side")
    if cursor.requested_qty <= 0:
        raise StateCorrupt(
            f"fill cursor {cursor.client_order_id} has non-positive requested quantity"
        )
    if cursor.cumulative_filled_qty < 0:
        raise StateCorrupt(f"fill cursor {cursor.client_order_id} has a negative cumulative fill")
    if cursor.cumulative_filled_qty > cursor.requested_qty:
        raise StateCorrupt(
            f"fill cursor {cursor.client_order_id} cumulative fill "
            f"{cursor.cumulative_filled_qty} exceeds requested {cursor.requested_qty}"
        )


@dataclass(frozen=True)
class LedgerEntry:
    """Our expected holding in exactly one option contract."""

    plan_id: str
    symbol: str
    asset_id: str = ""
    #: Signed: positive long, negative short. Confirmed fills only.
    signed_qty: Decimal = Decimal(0)
    entry_coids: tuple[str, ...] = ()
    exit_coids: tuple[str, ...] = ()
    entry_requested_qty: Decimal = Decimal(0)
    cumulative_entry_fill: Decimal = Decimal(0)
    cumulative_exit_fill: Decimal = Decimal(0)

    def owns_coid(self, coid: str) -> bool:
        return coid in self.entry_coids or coid in self.exit_coids

    @property
    def exit_qty(self) -> Decimal:
        """Exactly what we may sell to flatten. Never a symbol-wide close."""
        return abs(self.signed_qty)

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "signed_qty",
            "entry_requested_qty",
            "cumulative_entry_fill",
            "cumulative_exit_fill",
        ):
            data[key] = str(data[key])
        data["entry_coids"] = list(self.entry_coids)
        data["exit_coids"] = list(self.exit_coids)
        return data

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> LedgerEntry:
        return cls(
            plan_id=str(raw["plan_id"]),
            symbol=str(raw["symbol"]),
            asset_id=str(raw.get("asset_id", "")),
            signed_qty=_decimal(raw.get("signed_qty")),
            entry_coids=tuple(raw.get("entry_coids", ())),
            exit_coids=tuple(raw.get("exit_coids", ())),
            entry_requested_qty=_decimal(raw.get("entry_requested_qty")),
            cumulative_entry_fill=_decimal(raw.get("cumulative_entry_fill")),
            cumulative_exit_fill=_decimal(raw.get("cumulative_exit_fill")),
        )


@dataclass(frozen=True)
class Fault:
    """One reason the ledger refuses to let new risk on."""

    kind: str
    symbol: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"{self.kind}[{self.symbol}]: {self.detail}"


@dataclass(frozen=True)
class Reconciliation:
    """The outcome of comparing expectation with venue truth."""

    ok: bool
    faults: tuple[Fault, ...] = ()
    checked_symbols: tuple[str, ...] = ()
    reconciled_at: str = ""

    @property
    def blocks_new_entries(self) -> bool:
        return not self.ok

    def reasons(self) -> tuple[str, ...]:
        return tuple(str(f) for f in self.faults)


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _checksum(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass
class PositionLedger:
    """Durable, checksummed, per-contract ownership."""

    account_id: str
    environment: str
    entries: dict[str, LedgerEntry] = field(default_factory=dict)
    fill_cursors: dict[str, FillCursor] = field(default_factory=dict)
    generation: int = 0
    last_reconciled_at: str | None = None

    # -- derivation ------------------------------------------------------------

    def record_entry_fill(
        self,
        *,
        plan_id: str,
        symbol: str,
        client_order_id: str,
        filled_qty: Decimal,
        order_qty: Decimal,
        plan_qty: Decimal | None = None,
        side: str,
        asset_id: str = "",
    ) -> Decimal:
        """Apply only the new part of one cumulative confirmed entry fill."""
        current = self.entries.get(symbol)
        if current is not None and current.plan_id != plan_id:
            raise StateCorrupt(
                f"contract {symbol} is owned by plan {current.plan_id}, not {plan_id}"
            )
        cursor, delta = self._next_fill_cursor(
            plan_id=plan_id,
            symbol=symbol,
            purpose="entry",
            side=side,
            client_order_id=client_order_id,
            filled_qty=filled_qty,
            order_qty=order_qty,
        )
        requested_cap = _quantity(
            plan_qty if plan_qty is not None else order_qty,
            field_name="plan_qty",
        )
        if requested_cap <= 0:
            raise StateCorrupt(f"plan {plan_id} has non-positive requested quantity")
        if cursor.requested_qty > requested_cap:
            raise StateCorrupt(
                f"order {client_order_id} requests {cursor.requested_qty}, "
                f"above plan quantity {requested_cap}"
            )
        if delta == 0:
            return Decimal(0)

        if current is not None:
            if current.entry_requested_qty != requested_cap:
                raise StateCorrupt(
                    f"plan {plan_id} requested quantity changed from "
                    f"{current.entry_requested_qty} to {requested_cap}"
                )
            if current.cumulative_entry_fill + delta > current.entry_requested_qty:
                raise StateCorrupt(
                    f"plan {plan_id} aggregate entry fill exceeds requested quantity"
                )
        signed = delta if cursor.side == "buy" else -delta
        if current is None:
            current = LedgerEntry(
                plan_id=plan_id,
                symbol=symbol,
                asset_id=asset_id,
                entry_requested_qty=requested_cap,
            )
        coids = current.entry_coids
        if client_order_id and client_order_id not in coids:
            coids = coids + (client_order_id,)
        self.entries[symbol] = replace(
            current,
            asset_id=asset_id or current.asset_id,
            signed_qty=current.signed_qty + signed,
            entry_coids=coids,
            cumulative_entry_fill=current.cumulative_entry_fill + delta,
        )
        self.fill_cursors[client_order_id] = cursor
        return delta

    def record_exit_fill(
        self,
        *,
        symbol: str,
        client_order_id: str,
        filled_qty: Decimal,
        order_qty: Decimal,
        side: str,
    ) -> Decimal:
        """Apply only the new part of one cumulative confirmed exit fill."""
        current = self.entries.get(symbol)
        if current is None:
            raise KeyError(f"exit fill for unknown contract {symbol}")
        cursor, delta = self._next_fill_cursor(
            plan_id=current.plan_id,
            symbol=symbol,
            purpose="exit",
            side=side,
            client_order_id=client_order_id,
            filled_qty=filled_qty,
            order_qty=order_qty,
        )
        if delta == 0:
            return Decimal(0)

        signed = delta if cursor.side == "buy" else -delta
        remaining = current.signed_qty + signed
        if current.signed_qty > 0 and (cursor.side != "sell" or remaining < 0):
            raise StateCorrupt(f"exit fill {client_order_id} exceeds strategy-owned long quantity")
        if current.signed_qty < 0 and (cursor.side != "buy" or remaining > 0):
            raise StateCorrupt(f"exit fill {client_order_id} exceeds strategy-owned short quantity")
        if current.signed_qty == 0:
            raise StateCorrupt(f"exit fill {client_order_id} would move a flat contract")
        coids = current.exit_coids
        if client_order_id and client_order_id not in coids:
            coids = coids + (client_order_id,)
        self.entries[symbol] = replace(
            current,
            signed_qty=remaining,
            exit_coids=coids,
            cumulative_exit_fill=current.cumulative_exit_fill + delta,
        )
        self.fill_cursors[client_order_id] = cursor
        return delta

    def _next_fill_cursor(
        self,
        *,
        plan_id: str,
        symbol: str,
        purpose: str,
        side: str,
        client_order_id: str,
        filled_qty: Decimal,
        order_qty: Decimal,
    ) -> tuple[FillCursor, Decimal]:
        candidate = FillCursor(
            client_order_id=client_order_id,
            plan_id=plan_id,
            symbol=symbol,
            purpose=purpose,
            side=side.lower(),
            requested_qty=_quantity(order_qty, field_name="order_qty"),
            cumulative_filled_qty=_quantity(filled_qty, field_name="filled_qty"),
        )
        _validate_cursor(candidate)
        previous = self.fill_cursors.get(client_order_id)
        if previous is not None:
            if previous.identity != candidate.identity:
                raise StateCorrupt(
                    f"client order {client_order_id} identity changed from "
                    f"{previous.identity!r} to {candidate.identity!r}"
                )
            if candidate.cumulative_filled_qty <= previous.cumulative_filled_qty:
                return previous, Decimal(0)
            delta = candidate.cumulative_filled_qty - previous.cumulative_filled_qty
        else:
            delta = candidate.cumulative_filled_qty
        return candidate, delta

    def register_exit_intent(self, symbol: str, client_order_id: str) -> None:
        """Claim an exit client id before the order is sent.

        Durable before mutation: if we crash between here and the submit, the
        id is already ours, so restart looks it up instead of minting a new one.
        """
        current = self.entries[symbol]
        if client_order_id not in current.exit_coids:
            self.entries[symbol] = replace(
                current, exit_coids=current.exit_coids + (client_order_id,)
            )

    # -- reconciliation --------------------------------------------------------

    def reconcile(
        self,
        *,
        venue_positions: Mapping[str, Decimal],
        open_orders: Iterable[Any] = (),
        now: datetime | None = None,
    ) -> Reconciliation:
        """Compare expectation with venue truth, exactly, per contract.

        `venue_positions` maps contract symbol to signed venue quantity.
        `open_orders` are objects exposing `client_order_id` and `symbol`.
        """
        faults: list[Fault] = []
        stamp = (now or datetime.now(timezone.utc)).isoformat()

        for symbol, entry in sorted(self.entries.items()):
            venue_qty = _decimal(venue_positions.get(symbol, 0))
            if symbol not in venue_positions and entry.signed_qty != 0:
                faults.append(
                    Fault(
                        "missing_position",
                        symbol,
                        f"expected {entry.signed_qty}, venue reports no position",
                    )
                )
                continue
            if venue_qty != entry.signed_qty:
                faults.append(
                    Fault(
                        "quantity_mismatch",
                        symbol,
                        f"expected {entry.signed_qty}, venue reports {venue_qty}",
                    )
                )

        # Exposure in a contract we do not own is someone else's, and we must
        # not trade around it or close it.
        for symbol, qty in sorted(venue_positions.items()):
            if _decimal(qty) == 0:
                continue
            if symbol not in self.entries:
                faults.append(
                    Fault("foreign_position", symbol, f"venue reports {qty}, ledger has no entry")
                )

        for order in open_orders:
            symbol = str(getattr(order, "symbol", "") or "")
            coid = str(getattr(order, "client_order_id", "") or "")
            owner = self.entries.get(symbol)
            if owner is None:
                if symbol:
                    faults.append(Fault("foreign_order", symbol, f"open order {coid} is not ours"))
                continue
            if not owner.owns_coid(coid):
                faults.append(
                    Fault("foreign_order", symbol, f"open order {coid} is not in our id family")
                )

        self.last_reconciled_at = stamp
        return Reconciliation(
            ok=not faults,
            faults=tuple(faults),
            checked_symbols=tuple(sorted(set(self.entries) | set(venue_positions))),
            reconciled_at=stamp,
        )

    def is_flat(
        self,
        symbol: str,
        *,
        venue_qty: Decimal,
        exit_orders_terminal: bool,
    ) -> bool:
        """Flat is proven, never assumed.

        All three must hold: we expect nothing, the venue holds nothing, and no
        exit order can still fill. A close request being accepted is none of
        these.
        """
        entry = self.entries.get(symbol)
        if entry is None:
            return False
        return entry.signed_qty == 0 and _decimal(venue_qty) == 0 and exit_orders_terminal

    # -- persistence -----------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        body = {
            "schema_version": SCHEMA_VERSION,
            "account_id": self.account_id,
            "environment": self.environment,
            "generation": self.generation,
            "last_reconciled_at": self.last_reconciled_at,
            "entries": [self.entries[k].to_json() for k in sorted(self.entries)],
            "fill_cursors": [self.fill_cursors[k].to_json() for k in sorted(self.fill_cursors)],
        }
        return {**body, "checksum": _checksum(body)}

    def save(self, path: str | Path) -> None:
        self.generation += 1
        atomic_write_json(path, self.to_json())

    @classmethod
    def load(cls, path: str | Path, *, account_id: str, environment: str) -> PositionLedger:
        """Load and validate. A corrupt or foreign ledger raises, never heals.

        Silently starting from empty would be the worst possible recovery: it
        reports no exposure at exactly the moment exposure is unaccounted for.
        """

        def validate(raw: Any) -> PositionLedger:
            if not isinstance(raw, dict):
                raise StateCorrupt(f"{path}: ledger is not an object")
            if raw.get("schema_version") != SCHEMA_VERSION:
                raise StateCorrupt(
                    f"{path}: ledger schema {raw.get('schema_version')!r} is not {SCHEMA_VERSION}"
                )
            stored = raw.get("checksum")
            body = {k: v for k, v in raw.items() if k != "checksum"}
            if stored != _checksum(body):
                raise StateCorrupt(f"{path}: ledger checksum mismatch")
            if raw.get("account_id") != account_id:
                raise StateCorrupt(
                    f"{path}: ledger belongs to account {raw.get('account_id')!r}, "
                    f"not {account_id!r}"
                )
            if raw.get("environment") != environment:
                raise StateCorrupt(
                    f"{path}: ledger belongs to environment {raw.get('environment')!r}, "
                    f"not {environment!r}"
                )
            raw_entries = raw.get("entries")
            raw_cursors = raw.get("fill_cursors")
            if not isinstance(raw_entries, list) or not isinstance(raw_cursors, list):
                raise StateCorrupt(f"{path}: ledger entries and fill_cursors must be lists")

            entries: dict[str, LedgerEntry] = {}
            for item in raw_entries:
                entry = LedgerEntry.from_json(item)
                if entry.symbol in entries:
                    raise StateCorrupt(f"{path}: duplicate contract entry {entry.symbol}")
                if (
                    entry.entry_requested_qty < 0
                    or entry.cumulative_entry_fill < 0
                    or entry.cumulative_exit_fill < 0
                ):
                    raise StateCorrupt(f"{path}: contract {entry.symbol} has a negative fill total")
                entries[entry.symbol] = entry

            fill_cursors: dict[str, FillCursor] = {}
            for item in raw_cursors:
                cursor = FillCursor.from_json(item)
                if cursor.client_order_id in fill_cursors:
                    raise StateCorrupt(f"{path}: duplicate fill cursor {cursor.client_order_id}")
                fill_cursors[cursor.client_order_id] = cursor

            restored = cls(
                account_id=account_id,
                environment=environment,
                entries=entries,
                fill_cursors=fill_cursors,
                generation=int(raw.get("generation", 0)),
                last_reconciled_at=raw.get("last_reconciled_at"),
            )
            restored._validate_fill_totals(path)
            return restored

        return read_json(
            path,
            default=cls(account_id=account_id, environment=environment),
            validate=validate,
        )

    def _validate_fill_totals(self, path: str | Path) -> None:
        """Prove persisted ownership is exactly the sum of per-order cursors."""
        for cursor in self.fill_cursors.values():
            entry = self.entries.get(cursor.symbol)
            if entry is None:
                raise StateCorrupt(
                    f"{path}: fill cursor {cursor.client_order_id} has no contract entry"
                )
            if entry.plan_id != cursor.plan_id:
                raise StateCorrupt(
                    f"{path}: fill cursor {cursor.client_order_id} has the wrong plan identity"
                )
            owned_ids = entry.entry_coids if cursor.purpose == "entry" else entry.exit_coids
            if cursor.client_order_id not in owned_ids:
                raise StateCorrupt(
                    f"{path}: fill cursor {cursor.client_order_id} is absent from its contract"
                )

        for symbol, entry in self.entries.items():
            cursors = [c for c in self.fill_cursors.values() if c.symbol == symbol]
            entry_total = sum(
                (c.cumulative_filled_qty for c in cursors if c.purpose == "entry"), Decimal(0)
            )
            exit_total = sum(
                (c.cumulative_filled_qty for c in cursors if c.purpose == "exit"), Decimal(0)
            )
            signed_total = sum(
                (
                    c.cumulative_filled_qty if c.side == "buy" else -c.cumulative_filled_qty
                    for c in cursors
                ),
                Decimal(0),
            )
            if entry.cumulative_entry_fill != entry_total:
                raise StateCorrupt(f"{path}: contract {symbol} entry fill total is inconsistent")
            if entry_total > entry.entry_requested_qty:
                raise StateCorrupt(
                    f"{path}: contract {symbol} aggregate entry fill exceeds requested quantity"
                )
            for cursor in cursors:
                if cursor.purpose == "entry" and cursor.requested_qty > entry.entry_requested_qty:
                    raise StateCorrupt(
                        f"{path}: contract {symbol} entry order exceeds the plan quantity"
                    )
            if entry.cumulative_exit_fill != exit_total:
                raise StateCorrupt(f"{path}: contract {symbol} exit fill total is inconsistent")
            if entry.signed_qty != signed_total:
                raise StateCorrupt(f"{path}: contract {symbol} signed quantity is inconsistent")
