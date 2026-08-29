"""Position management. Runs every tick, with no model involved.

Once a position is open the LLM has no further say in it. Exits are mechanical:
stops, targets, time exits, and the expiry close-out.

The expiry close-out is the one with a hard deadline attached to somebody
else's clock. Alpaca stops accepting options orders at 15:30 ET on expiration
day, then auto-exercises anything in the money and lets the rest expire.
Non-trade activity syncs the FOLLOWING day, which means an expiry event could
land after the account is photographed. We close at 14:30 ET and never go near
it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from . import config as C
from .kernel import PortfolioState, Position
from .macro import MEASUREMENT_ET
from .schema import OptionContract
from .state import StateCorrupt, atomic_write_json, read_json

log = logging.getLogger("glassbox.manage")


@dataclass
class ExitOrder:
    symbol: str
    qty: Decimal
    reason: str
    urgency: str = "normal"     # normal | immediate


class KillSwitch:
    """Latching. Trips automatically, re-arms only by human decision.

    The re-arm rule is written down in the README before it is needed, because
    deciding it at 02:00 on Wednesday with the account down is how teams turn
    one bad day into a bad week.
    """

    def __init__(self, path: str | Path = C.KILL_SWITCH_STATE_FILE, journal=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal = journal

    @property
    def tripped(self) -> bool:
        try:
            return bool(self._read_state()["tripped"])
        except StateCorrupt:
            return True

    def state(self) -> dict:
        try:
            return self._read_state()
        except StateCorrupt:
            return {"tripped": True, "reason": "state file unreadable"}

    def _read_state(self) -> dict:
        def validate(raw) -> dict:
            if not isinstance(raw, dict) or not isinstance(raw.get("tripped"), bool):
                raise StateCorrupt(f"{self.path}: invalid kill-switch state")
            return raw

        return read_json(
            self.path, default={"tripped": False}, validate=validate)

    def trip(self, reason: str, detail: dict | None = None) -> None:
        if self.tripped:
            return
        rec = {"tripped": True, "reason": reason,
               "at": datetime.now(C.ET).isoformat(), "detail": detail or {}}
        atomic_write_json(self.path, rec)
        log.critical("KILL SWITCH TRIPPED: %s", reason)
        if self.journal:
            self.journal.append("risk.kill_switch", "KILL_SWITCH_TRIPPED", rec)

    def rearm(self, who: str, why: str) -> None:
        """Human only. Never called by any automated path."""
        rec = {"tripped": False, "rearmed_by": who, "why": why,
               "at": datetime.now(C.ET).isoformat()}
        atomic_write_json(self.path, rec)
        if self.journal:
            self.journal.append("human", "KILL_SWITCH_REARMED", rec)


class PositionManager:
    def __init__(self, broker, journal, kill_switch: KillSwitch | None = None,
                 targets_path: str | Path | None = None):
        self.broker = broker
        self.journal = journal
        self.kill = kill_switch or KillSwitch(journal=journal)
        # AUDIT NOTE: this was an in-memory dict and nothing rebuilt it. After
        # any restart every registered stop, target and time exit was silently
        # gone, leaving positions open with no exit logic at all -- only the
        # symbol-derived expiry close-out still worked. For an agent whose
        # whole claim is surviving restarts unattended, that was the gap that
        # mattered most.
        self._targets_path = Path(targets_path or C.TARGETS_STATE_FILE)
        self._targets_path.parent.mkdir(parents=True, exist_ok=True)
        self._targets: dict[str, dict] = self._load_targets()

        # Exits already sent this session, so a position the broker has not yet
        # dropped is not closed again on the next tick. `_close` used to fire
        # unconditionally every tick until the position list caught up: on a
        # one-minute loop, the 14:30 expiry close-out could issue ~90 duplicate
        # market orders for one contract.
        self._exits_sent: set[str] = set()

    # -- persistence -----------------------------------------------------------

    def _load_targets(self) -> dict[str, dict]:
        def validate(raw) -> dict[str, dict]:
            if not isinstance(raw, dict):
                raise StateCorrupt(
                    f"{self._targets_path}: exit targets must be an object")
            out: dict[str, dict] = {}
            try:
                for symbol, target in raw.items():
                    if not isinstance(symbol, str) or not isinstance(target, dict):
                        raise ValueError("each target must map a symbol to an object")
                    out[symbol] = {
                        "stop": (Decimal(target["stop"])
                                 if target.get("stop") else None),
                        "target": (Decimal(target["target"])
                                   if target.get("target") else None),
                        "time_exit": (datetime.fromisoformat(target["time_exit"])
                                      if target.get("time_exit") else None),
                        "entry": (Decimal(target["entry"])
                                  if target.get("entry") else None),
                    }
            except Exception as exc:
                raise StateCorrupt(
                    f"{self._targets_path}: invalid exit targets: {exc}") from exc
            return out

        return read_json(self._targets_path, default={}, validate=validate)

    def _save_targets(self) -> None:
        atomic_write_json(self._targets_path, {
            symbol: {
                "stop": str(target["stop"])
                if target.get("stop") is not None else None,
                "target": str(target["target"])
                if target.get("target") is not None else None,
                "time_exit": (target["time_exit"].isoformat()
                              if target.get("time_exit") else None),
                "entry": str(target["entry"])
                if target.get("entry") is not None else None,
            } for symbol, target in self._targets.items()})

    def register(self, symbol: str, *, stop=None, target=None, time_exit=None,
                 entry_price: Decimal | None = None) -> None:
        self._targets[symbol] = {"stop": stop, "target": target,
                                 "time_exit": time_exit, "entry": entry_price}
        self._save_targets()

    # -- the tick --------------------------------------------------------------

    def tick(self, state: PortfolioState) -> list[ExitOrder]:
        """Evaluate every open position and act. Returns what was closed."""
        self._evaluate_kill_switch(state)

        exits: list[ExitOrder] = []
        for p in state.positions:
            e = self._evaluate(p, state)
            if e:
                exits.append(e)

        for e in exits:
            self._close(e)

        # A tripped switch flattens the convex sleeve. Long options can go to
        # zero and there is no reason to hold decaying premium once we have
        # stopped taking risk.
        if self.kill.tripped:
            for p in state.positions:
                if p.instrument == "option" and p.qty > 0:
                    if not any(x.symbol == p.symbol for x in exits):
                        e = ExitOrder(p.symbol, p.qty,
                                      "kill switch latched: flattening convex sleeve",
                                      urgency="immediate")
                        self._close(e)
                        exits.append(e)
        return exits

    # -- rules -----------------------------------------------------------------

    def _evaluate(self, p: Position, state: PortfolioState) -> ExitOrder | None:
        now = state.now_et

        if p.instrument == "option":
            expiry = self._expiry_of(p)
            if expiry is not None:
                # Hard deadline: Alpaca stops accepting options orders at 15:30
                # ET on expiry day. We act an hour early, every time.
                if now.date() == expiry:
                    cutoff = now.replace(hour=C.OPTION_FORCE_CLOSE_ET[0],
                                         minute=C.OPTION_FORCE_CLOSE_ET[1],
                                         second=0, microsecond=0)
                    if now >= cutoff:
                        return ExitOrder(
                            p.symbol, abs(p.qty),
                            f"expiry close-out: {expiry} is today and it is past "
                            f"{C.OPTION_FORCE_CLOSE_ET[0]:02d}:"
                            f"{C.OPTION_FORCE_CLOSE_ET[1]:02d} ET "
                            f"(broker cutoff 15:30, non-trade activity syncs T+1)",
                            urgency="immediate")
                if now.date() > expiry:
                    return ExitOrder(p.symbol, abs(p.qty),
                                     f"position past expiry {expiry}: closing",
                                     urgency="immediate")

        rules = self._targets.get(p.symbol)
        if not rules:
            return None

        te = rules.get("time_exit")
        if te and now >= te:
            return ExitOrder(p.symbol, abs(p.qty), f"time exit at {te:%Y-%m-%d %H:%M}")

        px = state.snapshot_price.get(p.underlying or p.symbol)
        if px is None:
            return None

        stop, target = rules.get("stop"), rules.get("target")
        if p.instrument != "option":
            long = p.qty > 0
            if stop is not None and ((long and px <= stop) or (not long and px >= stop)):
                return ExitOrder(p.symbol, abs(p.qty),
                                 f"stop hit: {p.symbol} at {px} against stop {stop}",
                                 urgency="immediate")
            if target is not None and ((long and px >= target) or (not long and px <= target)):
                return ExitOrder(p.symbol, abs(p.qty),
                                 f"target hit: {p.symbol} at {px} against target {target}")
        return None

    def _evaluate_kill_switch(self, state: PortfolioState) -> None:
        """Per sleeve. The convex sleeve is permitted to go to zero -- that is
        the design, and a switch that fires on it is mis-specified."""
        if self.kill.tripped:
            return
        if state.core_sleeve_cost_basis > 0:
            dd = ((state.core_sleeve_cost_basis - state.core_sleeve_value)
                  / state.core_sleeve_cost_basis)
            if dd >= C.CORE_DRAWDOWN_KILL_PCT:
                self.kill.trip(
                    f"core sleeve drawdown {dd:.1%} at or beyond "
                    f"{C.CORE_DRAWDOWN_KILL_PCT:.0%}",
                    {"core_value": str(state.core_sleeve_value),
                     "core_cost": str(state.core_sleeve_cost_basis)})
                return
        port_dd = (C.STARTING_EQUITY - state.equity) / C.STARTING_EQUITY
        if port_dd >= C.PORTFOLIO_DRAWDOWN_KILL_PCT:
            self.kill.trip(
                f"portfolio drawdown {port_dd:.1%} at or beyond "
                f"{C.PORTFOLIO_DRAWDOWN_KILL_PCT:.0%} backstop",
                {"equity": str(state.equity)})

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _expiry_of(p: Position) -> date | None:
        try:
            return OptionContract.parse(p.symbol).expiry
        except Exception:
            return None

    def _close(self, e: ExitOrder) -> None:
        # One exit per symbol per session. The broker takes time to reflect a
        # close, and `tick` re-reads positions every minute, so without this
        # the same contract is closed again on every tick until it disappears.
        if e.symbol in self._exits_sent:
            return
        self._exits_sent.add(e.symbol)

        self.journal.append("manage", "EXIT_TRIGGERED", {
            "symbol": e.symbol, "qty": str(e.qty),
            "reason": e.reason, "urgency": e.urgency})
        try:
            self.broker.close_position(e.symbol)
        except Exception as exc:
            # A failed close must be retryable, so release the guard.
            self._exits_sent.discard(e.symbol)
            self.journal.append("manage", "EXIT_FAILED", {
                "symbol": e.symbol, "reason": e.reason, "error": str(exc)})
            log.error("failed to close %s: %s", e.symbol, exc)


def measurement_countdown(now: datetime) -> str:
    hours = (MEASUREMENT_ET - now).total_seconds() / 3600
    if hours < 0:
        return "measurement has passed"
    return f"{hours:.1f}h to measurement"
