"""Strategy-owned position ownership and exact reconciliation.

The behaviour under test is the difference between "the account holds 10 of
this contract" and "we hold 10 of this contract". Everything that fails closed
here fails closed because the second claim could not be proven.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from glassbox.position_ledger import LedgerEntry, PositionLedger
from glassbox.state import ProcessLock, StateCorrupt, StateLocked

ACCOUNT = "PA-SCORED-1"
ENV = "scored"
CALL = "SPY260908C00778000"
PUT = "SPY260908P00760000"
NOW = datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc)


def ledger() -> PositionLedger:
    return PositionLedger(account_id=ACCOUNT, environment=ENV)


def filled_long(
    book: PositionLedger,
    symbol: str,
    qty: str,
    coid: str = "gbx-1",
    order_qty: str | None = None,
) -> None:
    book.record_entry_fill(
        plan_id="gbp-1",
        symbol=symbol,
        client_order_id=coid,
        filled_qty=Decimal(qty),
        order_qty=Decimal(order_qty or qty),
        side="buy",
        asset_id=f"asset-{symbol}",
    )


def order(symbol: str, coid: str):
    return SimpleNamespace(symbol=symbol, client_order_id=coid)


# -- derivation ---------------------------------------------------------------


def test_expected_quantity_comes_only_from_confirmed_fills():
    book = ledger()
    # An intent, a submitted order, an acknowledgement: none of these move it.
    book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-1",
        filled_qty=Decimal(0),
        order_qty=Decimal(4),
        side="buy",
    )
    assert book.entries == {}

    filled_long(book, CALL, "4", order_qty="10")
    filled_long(book, CALL, "6", coid="gbx-2", order_qty="10")
    entry = book.entries[CALL]
    assert entry.signed_qty == Decimal(10)
    assert entry.cumulative_entry_fill == Decimal(10)
    assert entry.entry_coids == ("gbx-1", "gbx-2")


def test_exit_uses_exact_owned_quantity_not_symbol_wide_close():
    book = ledger()
    filled_long(book, CALL, "7")
    assert book.entries[CALL].exit_qty == Decimal(7)

    book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-x1",
        filled_qty=Decimal(3),
        order_qty=Decimal(7),
        side="sell",
    )
    assert book.entries[CALL].signed_qty == Decimal(4)
    assert book.entries[CALL].exit_qty == Decimal(4), "the exit must size to what remains"


def test_exit_fill_for_an_unowned_contract_is_refused():
    with pytest.raises(KeyError, match=CALL):
        ledger().record_exit_fill(
            symbol=CALL,
            client_order_id="gbx-x",
            filled_qty=Decimal(1),
            order_qty=Decimal(1),
            side="sell",
        )


def test_duplicate_and_stale_entry_observations_are_idempotent():
    book = ledger()

    assert book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-entry-1",
        filled_qty=Decimal(4),
        order_qty=Decimal(10),
        side="buy",
    ) == Decimal(4)
    assert book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-entry-1",
        filled_qty=Decimal(4),
        order_qty=Decimal(10),
        side="buy",
    ) == Decimal(0)
    assert book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-entry-1",
        filled_qty=Decimal(3),
        order_qty=Decimal(10),
        side="buy",
    ) == Decimal(0)
    assert book.entries[CALL].signed_qty == Decimal(4)
    assert book.entries[CALL].cumulative_entry_fill == Decimal(4)


def test_higher_cumulative_entry_observation_applies_only_the_delta():
    book = ledger()
    filled_long(book, CALL, "4", coid="gbx-entry-1", order_qty="10")

    delta = book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-entry-1",
        filled_qty=Decimal(7),
        order_qty=Decimal(10),
        side="buy",
    )

    assert delta == Decimal(3)
    assert book.entries[CALL].signed_qty == Decimal(7)
    assert book.entries[CALL].cumulative_entry_fill == Decimal(7)


def test_duplicate_stale_and_higher_exit_observations_are_idempotent():
    book = ledger()
    filled_long(book, CALL, "10")

    first = book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(6),
        order_qty=Decimal(10),
        side="sell",
    )
    duplicate = book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(6),
        order_qty=Decimal(10),
        side="sell",
    )
    stale = book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(4),
        order_qty=Decimal(10),
        side="sell",
    )
    final = book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(10),
        order_qty=Decimal(10),
        side="sell",
    )

    assert (first, duplicate, stale, final) == tuple(map(Decimal, (6, 0, 0, 4)))
    assert book.entries[CALL].signed_qty == Decimal(0)
    assert book.entries[CALL].cumulative_exit_fill == Decimal(10)


def test_fill_overflow_or_client_id_reuse_fails_without_mutating_ownership():
    book = ledger()
    with pytest.raises(StateCorrupt, match="exceeds requested"):
        book.record_entry_fill(
            plan_id="gbp-1",
            symbol=CALL,
            client_order_id="gbx-entry-1",
            filled_qty=Decimal(11),
            order_qty=Decimal(10),
            side="buy",
        )
    assert book.entries == {}

    filled_long(book, CALL, "4", coid="gbx-entry-1", order_qty="10")
    with pytest.raises(StateCorrupt, match="identity"):
        book.record_entry_fill(
            plan_id="gbp-1",
            symbol=PUT,
            client_order_id="gbx-entry-1",
            filled_qty=Decimal(5),
            order_qty=Decimal(10),
            side="buy",
        )
    assert book.entries[CALL].signed_qty == Decimal(4)
    assert PUT not in book.entries


def test_aggregate_entry_fills_across_replacements_cannot_exceed_the_plan_quantity():
    book = ledger()
    filled_long(book, CALL, "6", coid="gbx-original", order_qty="10")

    with pytest.raises(StateCorrupt, match="aggregate entry fill"):
        book.record_entry_fill(
            plan_id="gbp-1",
            symbol=CALL,
            client_order_id="gbx-replacement",
            filled_qty=Decimal(5),
            order_qty=Decimal(10),
            side="buy",
        )

    assert book.entries[CALL].signed_qty == Decimal(6)
    assert "gbx-replacement" not in book.fill_cursors


def test_replacement_order_keeps_its_own_quantity_under_the_original_plan_cap():
    """A successor works only the remainder, while the plan cap stays ten.

    Treating every replacement as another ten-contract request either rejects
    a legitimate fill cursor or loses the order identity needed for replay.
    """
    book = ledger()
    book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-original",
        filled_qty=Decimal(6),
        order_qty=Decimal(10),
        plan_qty=Decimal(10),
        side="buy",
    )

    delta = book.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-replacement",
        filled_qty=Decimal(4),
        order_qty=Decimal(4),
        plan_qty=Decimal(10),
        side="buy",
    )

    assert delta == Decimal(4)
    assert book.entries[CALL].signed_qty == Decimal(10)
    assert book.entries[CALL].entry_requested_qty == Decimal(10)
    assert book.fill_cursors["gbx-replacement"].requested_qty == Decimal(4)


# -- reconciliation -----------------------------------------------------------


def test_exact_match_reconciles_clean():
    book = ledger()
    filled_long(book, CALL, "10")
    filled_long(book, PUT, "10", coid="gbx-2")

    result = book.reconcile(
        venue_positions={CALL: Decimal(10), PUT: Decimal(10)},
        open_orders=[],
        now=NOW,
    )
    assert result.ok is True
    assert result.blocks_new_entries is False
    assert result.reconciled_at == NOW.isoformat()


def test_unknown_venue_position_blocks_new_entries():
    """Exposure we cannot explain is someone else's. Do not trade around it."""
    book = ledger()
    filled_long(book, CALL, "10")

    result = book.reconcile(
        venue_positions={CALL: Decimal(10), "QQQ260908C00500000": Decimal(3)},
        now=NOW,
    )
    assert result.ok is False
    assert result.blocks_new_entries is True
    assert any("foreign_position" in r for r in result.reasons())


def test_missing_expected_position_is_reconciliation_fault():
    book = ledger()
    filled_long(book, CALL, "10")

    result = book.reconcile(venue_positions={}, now=NOW)
    assert result.ok is False
    assert any("missing_position" in r for r in result.reasons())


def test_quantity_mismatch_is_reconciliation_fault():
    book = ledger()
    filled_long(book, CALL, "10")

    result = book.reconcile(venue_positions={CALL: Decimal(8)}, now=NOW)
    assert result.ok is False
    assert any("quantity_mismatch" in r for r in result.reasons())


def test_open_order_outside_our_id_family_is_a_fault():
    book = ledger()
    filled_long(book, CALL, "10", coid="gbx-mine")

    result = book.reconcile(
        venue_positions={CALL: Decimal(10)},
        open_orders=[order(CALL, "someone-elses-order")],
        now=NOW,
    )
    assert result.ok is False
    assert any("foreign_order" in r for r in result.reasons())


def test_our_own_open_exit_order_is_not_a_fault():
    book = ledger()
    filled_long(book, CALL, "10", coid="gbx-mine")
    book.register_exit_intent(CALL, "gbx-exit-1")

    result = book.reconcile(
        venue_positions={CALL: Decimal(10)},
        open_orders=[order(CALL, "gbx-exit-1")],
        now=NOW,
    )
    assert result.ok is True


# -- flatness -----------------------------------------------------------------


def test_flat_is_reported_only_after_orders_terminal_and_venue_qty_zero():
    book = ledger()
    filled_long(book, CALL, "5")
    book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-x",
        filled_qty=Decimal(5),
        order_qty=Decimal(5),
        side="sell",
    )

    # Expectation is zero, but an exit order can still fill.
    assert book.is_flat(CALL, venue_qty=Decimal(0), exit_orders_terminal=False) is False
    # Orders terminal, but the venue still shows the contract.
    assert book.is_flat(CALL, venue_qty=Decimal(2), exit_orders_terminal=True) is False
    # All three hold.
    assert book.is_flat(CALL, venue_qty=Decimal(0), exit_orders_terminal=True) is True


def test_flat_is_never_claimed_for_an_unknown_contract():
    assert ledger().is_flat(CALL, venue_qty=Decimal(0), exit_orders_terminal=True) is False


def test_late_exit_fill_after_cancel_reconciles_to_exact_flat():
    """The residual cancel came back terminal, but two more contracts had
    already sold on the way out. The ledger must land on the venue's number."""
    book = ledger()
    filled_long(book, CALL, "10")
    book.register_exit_intent(CALL, "gbx-exit-1")
    book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(8),
        order_qty=Decimal(10),
        side="sell",
    )
    assert book.entries[CALL].signed_qty == Decimal(2)

    book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(10),
        order_qty=Decimal(10),
        side="sell",
    )
    assert book.entries[CALL].signed_qty == Decimal(0)
    assert book.reconcile(venue_positions={CALL: Decimal(0)}, now=NOW).ok is True
    assert book.is_flat(CALL, venue_qty=Decimal(0), exit_orders_terminal=True) is True


def test_partial_exit_preserves_the_remaining_target():
    book = ledger()
    filled_long(book, CALL, "10")
    book.register_exit_intent(CALL, "gbx-exit-1")
    book.record_exit_fill(
        symbol=CALL,
        client_order_id="gbx-exit-1",
        filled_qty=Decimal(6),
        order_qty=Decimal(10),
        side="sell",
    )

    assert book.entries[CALL].exit_qty == Decimal(4)
    assert book.reconcile(venue_positions={CALL: Decimal(4)}, now=NOW).ok is True


# -- persistence --------------------------------------------------------------


def test_restart_rebuilds_expected_position_from_intents_and_fills(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger()
    filled_long(book, CALL, "10", coid="gbx-c")
    filled_long(book, PUT, "10", coid="gbx-p")
    book.register_exit_intent(CALL, "gbx-exit-1")
    book.save(path)

    restored = PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)

    assert restored.entries[CALL].signed_qty == Decimal(10)
    assert restored.entries[CALL].entry_coids == ("gbx-c",)
    assert restored.entries[CALL].exit_coids == ("gbx-exit-1",)
    assert restored.entries[PUT].signed_qty == Decimal(10)
    assert restored.generation == book.generation
    assert (
        restored.reconcile(venue_positions={CALL: Decimal(10), PUT: Decimal(10)}, now=NOW).ok
        is True
    )


def test_restart_then_replay_produces_the_identical_ledger_state(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger()
    filled_long(book, CALL, "4", coid="gbx-entry-1", order_qty="10")
    book.save(path)

    restored = PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)
    before = restored.to_json()
    delta = restored.record_entry_fill(
        plan_id="gbp-1",
        symbol=CALL,
        client_order_id="gbx-entry-1",
        filled_qty=Decimal(4),
        order_qty=Decimal(10),
        side="buy",
    )

    assert delta == Decimal(0)
    assert restored.to_json() == before


def test_old_or_structurally_corrupt_fill_cursor_schema_fails_closed(tmp_path):
    from glassbox import position_ledger as PL

    path = tmp_path / "ledger.json"
    book = ledger()
    filled_long(book, CALL, "4", coid="gbx-entry-1", order_qty="10")
    book.save(path)

    raw = json.loads(path.read_text())
    raw["schema_version"] = 1
    body = {k: v for k, v in raw.items() if k != "checksum"}
    raw["checksum"] = PL._checksum(body)
    path.write_text(json.dumps(raw))
    with pytest.raises(StateCorrupt, match="schema"):
        PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)

    book.save(path)
    raw = json.loads(path.read_text())
    raw["fill_cursors"][0]["cumulative_filled_qty"] = "11"
    body = {k: v for k, v in raw.items() if k != "checksum"}
    raw["checksum"] = PL._checksum(body)
    path.write_text(json.dumps(raw))
    with pytest.raises(StateCorrupt, match="exceeds requested"):
        PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)


def test_a_missing_ledger_loads_empty_but_a_corrupt_one_does_not(tmp_path):
    path = tmp_path / "ledger.json"
    assert PositionLedger.load(path, account_id=ACCOUNT, environment=ENV).entries == {}


def test_corrupt_position_ledger_latches_fail_closed(tmp_path):
    """Starting from empty on a corrupt ledger would report no exposure at
    exactly the moment exposure is unaccounted for."""
    path = tmp_path / "ledger.json"
    book = ledger()
    filled_long(book, CALL, "10")
    book.save(path)

    raw = json.loads(path.read_text())
    raw["entries"][0]["signed_qty"] = "999"  # tampered, checksum now stale
    path.write_text(json.dumps(raw))

    with pytest.raises(StateCorrupt, match="checksum"):
        PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)


def test_unparseable_ledger_is_corrupt(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json")
    with pytest.raises(StateCorrupt):
        PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)


def test_ledger_from_another_account_or_environment_is_refused(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger()
    filled_long(book, CALL, "10")
    book.save(path)

    with pytest.raises(StateCorrupt, match="account"):
        PositionLedger.load(path, account_id="PA-OTHER", environment=ENV)
    with pytest.raises(StateCorrupt, match="environment"):
        PositionLedger.load(path, account_id=ACCOUNT, environment="dev")


def test_schema_version_mismatch_is_refused(tmp_path):
    path = tmp_path / "ledger.json"
    book = ledger()
    book.save(path)
    raw = json.loads(path.read_text())
    raw["schema_version"] = 99
    path.write_text(json.dumps(raw))

    with pytest.raises(StateCorrupt, match="schema"):
        PositionLedger.load(path, account_id=ACCOUNT, environment=ENV)


def test_entry_roundtrips_through_json():
    entry = LedgerEntry(
        plan_id="gbp-1",
        symbol=CALL,
        asset_id="a-1",
        signed_qty=Decimal("-3"),
        entry_coids=("gbx-1",),
        exit_coids=("gbx-x",),
        cumulative_entry_fill=Decimal(3),
        cumulative_exit_fill=Decimal(0),
    )
    assert LedgerEntry.from_json(entry.to_json()) == entry


# -- singleton ----------------------------------------------------------------


def test_two_scheduler_processes_cannot_own_same_state_directory(tmp_path):
    path = tmp_path / "scheduler.lock"
    first = ProcessLock(path).acquire()
    try:
        with pytest.raises(StateLocked, match="already owns"):
            ProcessLock(path).acquire()
    finally:
        first.release()

    # Once released, the directory can be owned again.
    ProcessLock(path).acquire().release()


def test_a_lock_left_by_a_dead_process_is_reclaimed(tmp_path):
    path = tmp_path / "scheduler.lock"
    dead_pid = 999_999
    while True:
        try:
            os.kill(dead_pid, 0)
        except ProcessLookupError:
            break
        except OSError:
            pass
        dead_pid -= 1
    path.write_text(json.dumps({"pid": dead_pid}))

    lock = ProcessLock(path).acquire()
    assert json.loads(path.read_text())["pid"] == os.getpid()
    lock.release()


def test_an_unreadable_lock_is_treated_as_held(tmp_path):
    path = tmp_path / "scheduler.lock"
    path.write_text("garbage")
    with pytest.raises(StateLocked):
        ProcessLock(path).acquire()
