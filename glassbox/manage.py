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
from .broker import OrderStateUncertain
from .ids import exit_client_order_id
from .kernel import PortfolioState, Position
from .macro import MEASUREMENT_ET
from .mutations import OrderMutationService
from .position_ledger import PositionLedger
from .schema import OptionContract
from .state import StateCorrupt, atomic_write_json, read_json

log = logging.getLogger("glassbox.manage")


def _venue_closed_to_options(exc: Exception) -> bool:
    """Is this the venue refusing an options order because it is shut?

    Alpaca answers 42210000 / "options market orders are only allowed during
    market hours". Matched on the code first because the prose is theirs to
    reword, with a text fallback so a code change does not silently turn a
    deferral back into a stream of failures.
    """
    text = str(exc).lower()
    return "42210000" in text or ("options" in text and "only allowed during market hours" in text)


class UnownedExposure(RuntimeError):
    """Refusing to close exposure this strategy cannot prove it owns."""


@dataclass
class ExitOrder:
    symbol: str
    qty: Decimal
    reason: str
    urgency: str = "normal"  # normal | immediate


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

        return read_json(self.path, default={"tripped": False}, validate=validate)

    def trip(self, reason: str, detail: dict | None = None) -> None:
        if self.tripped:
            return
        rec = {
            "tripped": True,
            "reason": reason,
            "at": datetime.now(C.ET).isoformat(),
            "detail": detail or {},
        }
        atomic_write_json(self.path, rec)
        log.critical("KILL SWITCH TRIPPED: %s", reason)
        if self.journal:
            self.journal.append("risk.kill_switch", "KILL_SWITCH_TRIPPED", rec)

    def rearm(self, who: str, why: str) -> None:
        """Human only. Never called by any automated path."""
        rec = {
            "tripped": False,
            "rearmed_by": who,
            "why": why,
            "at": datetime.now(C.ET).isoformat(),
        }
        atomic_write_json(self.path, rec)
        if self.journal:
            self.journal.append("human", "KILL_SWITCH_REARMED", rec)


class PositionManager:
    def __init__(
        self,
        broker,
        journal,
        kill_switch: KillSwitch | None = None,
        targets_path: str | Path | None = None,
        ledger: PositionLedger | None = None,
        ledger_path: str | Path | None = None,
        exit_state_path: str | Path | None = None,
    ):
        self.broker = broker
        self.journal = journal
        self.kill = kill_switch or KillSwitch(journal=journal)
        # When a ledger is supplied the manager exits exactly what this
        # strategy owns. Without one it falls back to the development
        # symbol-wide path, which must never run on the scored account.
        self.ledger = ledger
        self._ledger_path = Path(ledger_path) if ledger_path else None
        self.mutations = OrderMutationService(
            broker,
            journal,
            ledger=ledger,
            ledger_path=ledger_path,
        )
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
        # Symbols whose exit the venue cannot accept yet. In memory only:
        # a restart should re-report the deferral, not inherit silence.
        self._exit_deferred: set[str] = set()
        # Attempt counts and the uncertainty latch are persisted, not held in
        # memory. An in-memory latch is empty after any restart, and the
        # restart is exactly when the agent would resume submitting an exit
        # under a client id the venue has already rejected as a duplicate --
        # the loop this latch exists to stop. Same reasoning as _targets.
        self._exit_state_path = Path(exit_state_path or C.EXIT_STATE_FILE)
        self._exit_state_path.parent.mkdir(parents=True, exist_ok=True)
        loaded = self._load_exit_state()
        self._exit_attempts: dict[str, int] = loaded["attempts"]
        self._exit_uncertain: set[str] = loaded["uncertain"]

    # -- persistence -----------------------------------------------------------

    def _load_exit_state(self) -> dict:
        def validate(raw) -> dict:
            if not isinstance(raw, dict):
                raise StateCorrupt(f"{self._exit_state_path}: exit state must be an object")
            try:
                attempts = {str(k): int(v) for k, v in (raw.get("attempts") or {}).items()}
                uncertain = {str(v) for v in (raw.get("uncertain") or [])}
            except Exception as exc:
                raise StateCorrupt(f"{self._exit_state_path}: invalid exit state: {exc}") from exc
            return {"attempts": attempts, "uncertain": uncertain}

        return read_json(
            self._exit_state_path,
            default={"attempts": {}, "uncertain": set()},
            validate=validate,
        )

    def _save_exit_state(self) -> None:
        atomic_write_json(
            self._exit_state_path,
            {
                "attempts": dict(self._exit_attempts),
                "uncertain": sorted(self._exit_uncertain),
            },
        )

    def _load_targets(self) -> dict[str, dict]:
        def validate(raw) -> dict[str, dict]:
            if not isinstance(raw, dict):
                raise StateCorrupt(f"{self._targets_path}: exit targets must be an object")
            out: dict[str, dict] = {}
            try:
                for symbol, target in raw.items():
                    if not isinstance(symbol, str) or not isinstance(target, dict):
                        raise ValueError("each target must map a symbol to an object")
                    out[symbol] = {
                        "stop": (Decimal(target["stop"]) if target.get("stop") else None),
                        "target": (Decimal(target["target"]) if target.get("target") else None),
                        "time_exit": (
                            datetime.fromisoformat(target["time_exit"])
                            if target.get("time_exit")
                            else None
                        ),
                        "entry": (Decimal(target["entry"]) if target.get("entry") else None),
                    }
            except Exception as exc:
                raise StateCorrupt(f"{self._targets_path}: invalid exit targets: {exc}") from exc
            return out

        return read_json(self._targets_path, default={}, validate=validate)

    def _save_targets(self) -> None:
        atomic_write_json(
            self._targets_path,
            {
                symbol: {
                    "stop": str(target["stop"]) if target.get("stop") is not None else None,
                    "target": str(target["target"]) if target.get("target") is not None else None,
                    "time_exit": (
                        target["time_exit"].isoformat() if target.get("time_exit") else None
                    ),
                    "entry": str(target["entry"]) if target.get("entry") is not None else None,
                }
                for symbol, target in self._targets.items()
            },
        )

    def register(
        self,
        symbol: str,
        *,
        stop=None,
        target=None,
        time_exit=None,
        entry_price: Decimal | None = None,
    ) -> None:
        self._targets[symbol] = {
            "stop": stop,
            "target": target,
            "time_exit": time_exit,
            "entry": entry_price,
        }
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
                        e = ExitOrder(
                            p.symbol,
                            p.qty,
                            "kill switch latched: flattening convex sleeve",
                            urgency="immediate",
                        )
                        self._close(e)
                        exits.append(e)
        return exits

    # -- rules -----------------------------------------------------------------

    def _measurement_mark_exit(self, p: Position, state: PortfolioState) -> ExitOrder | None:
        """Flatten an option whose mark cannot be trusted at the snapshot.

        The last decision of the week is not "is this position good" but "can
        this position be marked honestly". The account is valued at a known
        instant, and this account prices options off the indicative feed -- a
        derived estimate, not OPRA. A wide quote at that instant produces a
        number nobody can defend, in either direction, and cash has no marking
        ambiguity at all.

        Deliberately narrow. It only ever turns an untrustworthy mark into
        cash: options only, inside the window, and only when the quote is
        genuinely unusable rather than merely worse than we would have entered
        on. It never opens risk and never touches a position it can price.
        """
        if p.instrument != "option" or p.qty <= 0:
            return None
        minutes = (MEASUREMENT_ET - state.now_et).total_seconds() / 60
        if not 0 < minutes <= C.MEASUREMENT_FLATTEN_MINUTES:
            return None

        spread = state.option_quote_spread.get(p.symbol)
        if spread is None:
            # No quote at all is the worst case, not an exemption: an unquoted
            # contract is precisely what we cannot let the snapshot value.
            return ExitOrder(
                p.symbol,
                abs(p.qty),
                f"measurement in {minutes:.0f}m and {p.symbol} has no two-sided "
                f"quote: taking cash over a mark we cannot defend",
                urgency="immediate",
            )
        if spread > C.MEASUREMENT_MAX_MARK_SPREAD_PCT:
            return ExitOrder(
                p.symbol,
                abs(p.qty),
                f"measurement in {minutes:.0f}m and {p.symbol} quotes "
                f"{spread:.1%} wide against a {C.MEASUREMENT_MAX_MARK_SPREAD_PCT:.0%} "
                f"limit: the mark is not evidence, taking cash",
                urgency="immediate",
            )
        return None

    def _evaluate(self, p: Position, state: PortfolioState) -> ExitOrder | None:
        now = state.now_et

        mark_exit = self._measurement_mark_exit(p, state)
        if mark_exit is not None:
            return mark_exit

        if p.instrument == "option":
            expiry = self._expiry_of(p)
            if expiry is not None:
                # Hard deadline: Alpaca stops accepting options orders at 15:30
                # ET on expiry day. We act an hour early, every time.
                if now.date() == expiry:
                    cutoff = now.replace(
                        hour=C.OPTION_FORCE_CLOSE_ET[0],
                        minute=C.OPTION_FORCE_CLOSE_ET[1],
                        second=0,
                        microsecond=0,
                    )
                    if now >= cutoff:
                        return ExitOrder(
                            p.symbol,
                            abs(p.qty),
                            f"expiry close-out: {expiry} is today and it is past "
                            f"{C.OPTION_FORCE_CLOSE_ET[0]:02d}:"
                            f"{C.OPTION_FORCE_CLOSE_ET[1]:02d} ET "
                            f"(broker cutoff 15:30, non-trade activity syncs T+1)",
                            urgency="immediate",
                        )
                if now.date() > expiry:
                    return ExitOrder(
                        p.symbol,
                        abs(p.qty),
                        f"position past expiry {expiry}: closing",
                        urgency="immediate",
                    )

        rules = self._targets.get(p.symbol)
        if not rules:
            return None

        te = rules.get("time_exit")
        if te and now >= te:
            return ExitOrder(p.symbol, abs(p.qty), f"time exit at {te:%Y-%m-%d %H:%M}")

        px = state.snapshot_price.get(p.underlying or p.symbol)
        if px is None:
            return None

        # Options first: their P&L lives in the premium, not in the underlying
        # price the stop/target rules below compare against. Without this an
        # option had no profit exit at all.
        if p.instrument == "option" and p.premium_paid > 0:
            gain = (p.market_value - p.premium_paid) / p.premium_paid
            if gain >= C.OPTION_PROFIT_TARGET_PCT:
                return ExitOrder(
                    p.symbol,
                    abs(p.qty),
                    f"profit target hit: {p.symbol} up {gain:.1%} on premium "
                    f"against {C.OPTION_PROFIT_TARGET_PCT:.0%}",
                )

        stop, target = rules.get("stop"), rules.get("target")
        if p.instrument != "option":
            long = p.qty > 0
            if stop is not None and ((long and px <= stop) or (not long and px >= stop)):
                return ExitOrder(
                    p.symbol,
                    abs(p.qty),
                    f"stop hit: {p.symbol} at {px} against stop {stop}",
                    urgency="immediate",
                )
            if target is not None and ((long and px >= target) or (not long and px <= target)):
                return ExitOrder(
                    p.symbol, abs(p.qty), f"target hit: {p.symbol} at {px} against target {target}"
                )
        return None

    def _evaluate_kill_switch(self, state: PortfolioState) -> None:
        """Per sleeve. The convex sleeve is permitted to go to zero -- that is
        the design, and a switch that fires on it is mis-specified."""
        if self.kill.tripped:
            return
        if state.core_sleeve_cost_basis > 0:
            dd = (
                state.core_sleeve_cost_basis - state.core_sleeve_value
            ) / state.core_sleeve_cost_basis
            if dd >= C.CORE_DRAWDOWN_KILL_PCT:
                self.kill.trip(
                    f"core sleeve drawdown {dd:.1%} at or beyond {C.CORE_DRAWDOWN_KILL_PCT:.0%}",
                    {
                        "core_value": str(state.core_sleeve_value),
                        "core_cost": str(state.core_sleeve_cost_basis),
                    },
                )
                return
        port_dd = (C.STARTING_EQUITY - state.equity) / C.STARTING_EQUITY
        if port_dd >= C.PORTFOLIO_DRAWDOWN_KILL_PCT:
            self.kill.trip(
                f"portfolio drawdown {port_dd:.1%} at or beyond "
                f"{C.PORTFOLIO_DRAWDOWN_KILL_PCT:.0%} backstop",
                {"equity": str(state.equity)},
            )

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
        if e.symbol in self._exits_sent or e.symbol in self._exit_uncertain:
            return
        self._exits_sent.add(e.symbol)

        self.journal.append(
            "manage",
            "EXIT_TRIGGERED",
            {"symbol": e.symbol, "qty": str(e.qty), "reason": e.reason, "urgency": e.urgency},
        )
        try:
            if self.ledger is not None:
                self._close_exact(e)
                self._exit_deferred.discard(e.symbol)
            elif getattr(self.broker, "env", "dev") == "scored":
                # A comment said this must never happen on the scored account;
                # nothing enforced it. Symbol-wide close liquidates whatever the
                # account holds in a contract, so reaching it here would undo
                # the ledger's entire purpose on the one account where it
                # matters. Refuse rather than trust every caller to pass a
                # ledger -- tools/practice.py already builds a manager without
                # one.
                self.journal.append(
                    "manage",
                    "EXIT_REFUSED_NO_LEDGER",
                    {"symbol": e.symbol, "reason": e.reason},
                )
                raise UnownedExposure(
                    f"refusing symbol-wide close of {e.symbol} on the scored "
                    "account: no position ledger was supplied"
                )
            else:
                self.broker.close_position(e.symbol)
        except UnownedExposure as exc:
            # Not a failure and not transient: this exposure is not ours and
            # will never become ours. Keep the guard so a one-minute tick loop
            # does not re-refuse it forever, and do not file it as EXIT_FAILED
            # -- mislabelling a correct refusal corrupts the evidence.
            log.warning("refusing to close %s: %s", e.symbol, exc)
        except OrderStateUncertain as exc:
            # The exit order was sent and we cannot prove where it ended up.
            # Releasing the guard here would retry with the same deterministic
            # client id, which the venue rejects as a duplicate, forever --
            # while the real fill stays unrecorded. Latch instead and let
            # reconciliation resolve it.
            self._exit_uncertain.add(e.symbol)
            self._save_exit_state()
            self.journal.append(
                "manage",
                "EXIT_STATE_UNCERTAIN",
                {"symbol": e.symbol, "reason": e.reason, "error": str(exc)},
            )
            log.error("exit state uncertain for %s: %s", e.symbol, exc)
        except Exception as exc:
            # A genuinely transient failure must stay retryable.
            self._exits_sent.discard(e.symbol)
            if _venue_closed_to_options(exc):
                # Not a failure. `time_exit` for the convex sleeve is
                # MEASUREMENT_ET (16:00 ET), which is the exact minute Alpaca
                # stops accepting options market orders -- so the exit is
                # correct, on time, and unfillable until the next session.
                #
                # Retrying is right; filing it as EXIT_FAILED once a minute is
                # not. It put 45 identical ERROR lines in the log and 45
                # EXIT_FAILED entries in the journal for one deferred order,
                # which reads as a broken agent to anyone auditing the run.
                # Say it once, stay quiet, stay retryable.
                if e.symbol not in self._exit_deferred:
                    self._exit_deferred.add(e.symbol)
                    self.journal.append(
                        "manage",
                        "EXIT_DEFERRED_VENUE_CLOSED",
                        {"symbol": e.symbol, "reason": e.reason, "error": str(exc)},
                    )
                    log.info("deferring close of %s until the venue reopens: %s", e.symbol, exc)
                return
            self._exit_deferred.discard(e.symbol)
            self.journal.append(
                "manage", "EXIT_FAILED", {"symbol": e.symbol, "reason": e.reason, "error": str(exc)}
            )
            log.error("failed to close %s: %s", e.symbol, exc)

    def _close_exact(self, e: ExitOrder) -> None:
        """Sell exactly what this strategy owns, under a deterministic id.

        `close_position` liquidates whatever the account holds in the contract.
        If anything else in the account is in the same contract, a symbol-wide
        close takes it too, and its acceptance proves nothing about our
        quantity. So: exact size, deterministic id, intent durable before the
        mutation, and flatness proven from a terminal order plus a zero venue
        quantity rather than from the close being accepted.
        """
        assert self.ledger is not None
        entry = self.ledger.entries.get(e.symbol)
        if entry is None:
            # Exposure we never recorded is not ours to close.
            self.journal.append(
                "manage",
                "EXIT_REFUSED_UNOWNED",
                {"symbol": e.symbol, "reason": e.reason},
            )
            raise UnownedExposure(f"{e.symbol} is not in the strategy ledger")

        qty = entry.exit_qty
        if qty <= 0:
            self.journal.append("manage", "EXIT_ALREADY_FLAT", {"symbol": e.symbol})
            return

        coid = exit_client_order_id(entry.plan_id, e.symbol, self._exit_attempts.get(e.symbol, 0))

        self.journal.append(
            "manage",
            "EXIT_INTENT",
            {
                "symbol": e.symbol,
                "plan_id": entry.plan_id,
                "qty": str(qty),
                "side": "sell",
                "client_order_id": coid,
                "reason": e.reason,
            },
        )
        outcome = self.mutations.exact_exit(
            plan_id=entry.plan_id,
            symbol=e.symbol,
            purpose="exit",
            reason=e.reason,
            first_attempt=self._exit_attempts.get(e.symbol, 0),
            max_attempts=1,
        )
        state = outcome.states[-1]
        self._exit_attempts[e.symbol] = self._exit_attempts.get(e.symbol, 0) + 1
        self._save_exit_state()

        remaining = self.ledger.entries[e.symbol].exit_qty
        self.journal.append(
            "manage",
            "EXIT_ORDER_TERMINAL",
            {
                "symbol": e.symbol,
                "client_order_id": coid,
                "status": state.status,
                "filled_qty": str(state.filled_qty),
                "remaining_qty": str(remaining),
                "terminal": state.terminal,
            },
        )
        if remaining > 0:
            # A partial exit is not a failure, but it is not flat either. Let
            # the next tick size a fresh exit from what is left.
            self._exits_sent.discard(e.symbol)
        elif not outcome.flat:
            raise OrderStateUncertain(
                f"exit {coid} reached terminal state but exact venue flatness was not proven"
            )


def measurement_countdown(now: datetime) -> str:
    hours = (MEASUREMENT_ET - now).total_seconds() / 3600
    if hours < 0:
        return "measurement has passed"
    return f"{hours:.1f}h to measurement"
