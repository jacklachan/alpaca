"""Trading sessions from the venue, not from a table someone typed.

Every expiry decision this agent makes rests on one number: how many sessions
sit between now and a date. That number decides whether a contract has real
two-sided quotes or is a 0DTE stub, and it feeds the kernel's expiry guard.

It was computed from `weekday() < 5` minus a hardcoded set containing exactly
one holiday, Labor Day 2026. That is correct for the week it was written for
and silently wrong everywhere else: an unattended agent still running in
October counts Thanksgiving as a session, overstates the life of every
contract, and does it without a single error in the log. Wrong-and-quiet is
the failure mode this codebase spends most of its effort avoiding.

So sessions come from Alpaca's own calendar when a client is available. The
offline table remains, because the strategy has to stay testable without a
network and because a calendar fetch can fail at exactly the wrong moment --
but which one answered is recorded, so a session count is never anonymous.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable

log = logging.getLogger("glassbox.calendar")

#: How far around a request to fetch, so one call serves a whole run.
FETCH_PADDING_DAYS = 120

#: Offline fallback. Correct for the competition window, and deliberately
#: narrow: it is a stand-in for the venue calendar, not a substitute for it.
FALLBACK_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)


def _weekday_sessions(start: date, end: date) -> list[date]:
    """Weekdays minus the known holidays. The fallback, not the authority."""
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5 and cur not in FALLBACK_HOLIDAYS:
            out.append(cur)
        cur += timedelta(days=1)
    return out


@dataclass
class MarketCalendar:
    """Session dates for a window, with the source recorded.

    `fetch` takes (start, end) and returns objects exposing `.date`. Passing
    None keeps the calendar entirely offline, which is what tests and the
    pure strategy code want.
    """

    fetch: Callable[[date, date], Iterable[Any]] | None = None
    _sessions: set[date] = field(default_factory=set)
    _covered: tuple[date, date] | None = None
    source: str = "offline"

    # -- acquisition -----------------------------------------------------------

    def _ensure(self, start: date, end: date) -> None:
        """Load a window, once, widened so one call serves a whole run."""
        if self._covered and self._covered[0] <= start and end <= self._covered[1]:
            return

        low = start - timedelta(days=FETCH_PADDING_DAYS)
        high = end + timedelta(days=FETCH_PADDING_DAYS)

        if self.fetch is not None:
            try:
                days = list(self.fetch(low, high))
            except Exception as exc:
                # A calendar outage must not stop the agent, but it must not
                # pass unnoticed either: the fallback is less correct and the
                # evidence has to say which one answered.
                log.warning("venue calendar unavailable (%s); using offline sessions", exc)
                self._adopt(_weekday_sessions(low, high), low, high, "offline (venue unavailable)")
                return
            parsed = {d for d in (_session_date(day) for day in days) if d is not None}
            if parsed:
                self._adopt(sorted(parsed), low, high, "alpaca")
                return
            log.warning("venue calendar returned no sessions; using offline sessions")

        self._adopt(_weekday_sessions(low, high), low, high, self.source or "offline")

    def _adopt(self, sessions: Iterable[date], low: date, high: date, source: str) -> None:
        self._sessions = set(sessions)
        self._covered = (low, high)
        self.source = source

    # -- queries ---------------------------------------------------------------

    def is_session(self, day: date) -> bool:
        self._ensure(day, day)
        return day in self._sessions

    def sessions_between(self, start: date, end: date) -> int:
        """Sessions after `start`, up to and including `end`.

        Exclusive of the start day, matching how the strategy reasons about
        "days from now until expiry".
        """
        if end <= start:
            return 0
        self._ensure(start, end)
        return sum(1 for d in self._sessions if start < d <= end)

    def sessions_remaining_at(self, expiry: date, measurement: date) -> int:
        """Sessions a contract still has when the account is valued.

        Inclusive of the measurement day, because the option still has that
        session to live at the moment the snapshot is taken.
        """
        if expiry < measurement:
            return 0
        self._ensure(measurement, expiry)
        return sum(1 for d in self._sessions if measurement <= d <= expiry)

    def provenance(self) -> dict[str, Any]:
        """Which calendar answered, and for what window."""
        return {
            "source": self.source,
            "covered_from": self._covered[0].isoformat() if self._covered else None,
            "covered_to": self._covered[1].isoformat() if self._covered else None,
            "sessions_known": len(self._sessions),
        }


def _session_date(day: Any) -> date | None:
    """Pull a date out of an Alpaca calendar entry, defensively."""
    value = getattr(day, "date", day)
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def from_broker(broker: Any) -> MarketCalendar:
    """A calendar backed by this broker's Alpaca client."""

    def fetch(start: date, end: date) -> Iterable[Any]:
        from alpaca.trading.requests import GetCalendarRequest

        request = GetCalendarRequest(start=start, end=end)
        return broker._call(
            lambda: broker.trading.get_calendar(request),
            "get_calendar",
        )

    return MarketCalendar(fetch=fetch)
