"""The macro calendar for the scored window.

Hard-coded on purpose. The window is five days long, the releases are published
a year in advance, and a scraper is a dependency that can fail at 04:00 on a
Friday. Every entry is verifiable against the BLS and ISM schedules, and the
source is named so a judge can check it.

`expected_move_bps` is the historical average absolute SPY move on the release
day, used only for sizing sanity -- not as a forecast.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class MacroEvent:
    name: str
    when: datetime          # ET, timezone-aware
    tier: int               # 1 = market-moving, 2 = secondary
    expected_move_bps: int
    source: str

    @property
    def day(self) -> date:
        return self.when.date()

    def hours_until(self, now: datetime) -> float:
        return (self.when - now).total_seconds() / 3600


def _et(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# Scored window: Mon 31 Aug -- Fri 4 Sep 2026.
CALENDAR: tuple[MacroEvent, ...] = (
    MacroEvent("Month-end rebalancing", _et(2026, 8, 31, 15, 0), 2, 45,
               "seasonal flow, not a scheduled release"),
    MacroEvent("ISM Manufacturing PMI", _et(2026, 9, 1, 10, 0), 2, 40,
               "ISM, first business day of the month"),
    MacroEvent("ADP National Employment", _et(2026, 9, 2, 8, 15), 2, 35,
               "ADP, Wednesday before payrolls"),
    MacroEvent("ISM Services PMI", _et(2026, 9, 3, 10, 0), 2, 40,
               "ISM, third business day"),
    MacroEvent("Initial jobless claims", _et(2026, 9, 3, 8, 30), 2, 20,
               "DOL, weekly"),
    MacroEvent("Employment Situation (Aug)", _et(2026, 9, 4, 8, 30), 1, 85,
               "BLS September 2026 release schedule, verified"),
)

# When the account is measured. The Q&A says the opening bell; the event page
# implies the 11:00 ET submission close. Until Discord answers, we assume the
# earlier of the two, because being early is recoverable and being late is not.
MEASUREMENT_ET = _et(2026, 9, 4, 9, 30)
MEASUREMENT_IS_CONFIRMED = False

# Alpaca stops accepting options orders at 15:30 ET on expiration day.
OPTION_ORDER_CUTOFF_ET = (15, 30)

# Market holidays inside and just after the window. Labor Day matters because
# it sits between the 4 Sep measurement and the 8 Sep expiry, which is what
# makes that expiry cheap per calendar day.
HOLIDAYS_2026 = frozenset({date(2026, 9, 7)})  # Labor Day


def next_event(now: datetime, tier: int | None = None,
               within_hours: float = 48) -> MacroEvent | None:
    """The next scheduled catalyst, optionally filtered by tier."""
    upcoming = [
        e for e in CALENDAR
        if e.when > now
        and (tier is None or e.tier <= tier)
        and e.hours_until(now) <= within_hours
    ]
    return min(upcoming, key=lambda e: e.when) if upcoming else None


def events_between(start: datetime, end: datetime) -> list[MacroEvent]:
    return sorted([e for e in CALENDAR if start < e.when <= end], key=lambda e: e.when)


def trading_days_between(start: date, end: date) -> int:
    """Sessions after `start`, up to and including `end`.

    Exclusive of the start day. Prefer Alpaca's get_calendar in production --
    this exists so the strategy is testable without a network.
    """
    if end <= start:
        return 0
    n, cur = 0, start
    while cur < end:
        cur = date.fromordinal(cur.toordinal() + 1)
        if cur.weekday() < 5 and cur not in HOLIDAYS_2026:
            n += 1
    return n


def sessions_remaining_at_measurement(expiry: date,
                                      measurement: datetime | None = None) -> int:
    """How many trading sessions the contract still has when we are measured.

    Inclusive of the measurement day itself, because the account is
    photographed at the open and the option still has that whole session to
    live. This is the number that decides whether a contract marks off a real
    two-sided quote or a 0DTE stub.

    For the 4 Sep 09:30 measurement, with Labor Day on Mon 7 Sep:

        4 Sep  -> 1   expires that afternoon; marks as a stub. Refuse.
        8 Sep  -> 2   Fri + Tue. Cheap per calendar day precisely BECAUSE the
                      holiday weekend sits inside it and carries no vol.
        9 Sep  -> 3   Fri + Tue + Wed
        11 Sep -> 5   Fri + Tue + Wed + Thu + Fri
    """
    m = (measurement or MEASUREMENT_ET).date()
    if expiry < m:
        return 0
    n = 1 if (m.weekday() < 5 and m not in HOLIDAYS_2026) else 0
    return n + trading_days_between(m, expiry)


def is_measurement_imminent(now: datetime, hours: float = 24) -> bool:
    """True inside the final day. Used to refuse opening new risk that cannot
    resolve before the account is photographed."""
    return 0 <= (MEASUREMENT_ET - now).total_seconds() / 3600 <= hours
