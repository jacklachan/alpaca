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
    when: datetime  # ET, timezone-aware
    tier: int  # 1 = market-moving, 2 = secondary
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
    MacroEvent(
        "Month-end rebalancing",
        _et(2026, 8, 31, 15, 0),
        2,
        45,
        "seasonal flow, not a scheduled release",
    ),
    MacroEvent(
        "ISM Manufacturing PMI",
        _et(2026, 9, 1, 10, 0),
        2,
        40,
        "ISM, first business day of the month",
    ),
    MacroEvent(
        "ADP National Employment", _et(2026, 9, 2, 8, 15), 2, 35, "ADP, Wednesday before payrolls"
    ),
    MacroEvent("ISM Services PMI", _et(2026, 9, 3, 10, 0), 2, 40, "ISM, third business day"),
    MacroEvent("Initial jobless claims", _et(2026, 9, 3, 8, 30), 2, 20, "DOL, weekly"),
    MacroEvent(
        "Employment Situation (Aug)",
        _et(2026, 9, 4, 8, 30),
        1,
        85,
        "BLS September 2026 release schedule, verified",
    ),
)

# When the account is measured. RESOLVED 29 Aug by Alpaca's official guidelines:
#
#   "Official P&L measurement: Monday, August 31 at 9:30 a.m. ET to Friday,
#    September 4 at 9:30 a.m. ET. We will be looking at the portfolio's total
#    equity as of EOD Thursday Sep 3rd. Any option exercises and assignments
#    for options expiring on Sep 3rd will be reflected in the EOD value."
#
# This moved the target by a full session and it invalidates the trade this
# strategy was originally built around. We had assumed Friday's opening bell,
# which put the 08:30 ET payrolls print sixty minutes INSIDE the window. It is
# now ~17 hours OUTSIDE it. Buying convexity for payrolls would have been
# paying for a catalyst that resolves after the account is photographed --
# the most expensive kind of wrong, because it looks like a thesis.
#
# Two consequences, both handled below:
#   1. next_event() now refuses to return a catalyst at or after measurement.
#   2. Sep 3 expiries are no longer a stub risk. Alpaca states exercises and
#      assignments for that expiry ARE reflected in the EOD value, which is
#      the specific settlement concern that pushed us out to later expiries.
#      We still close at 14:30 ET (OPTION_FORCE_CLOSE_ET) rather than rely on
#      it, because a realised close is worth more than a settlement promise.
# Re-verified 30 Aug against the archived guidelines document itself, because
# the public event page shows only the 4 September deadline and a reference
# audit therefore recorded this cutoff as unconfirmed. The document carries
# both dates, and they describe the same number rather than disagreeing:
#
#   "The measurement window ends at 9:30 a.m. ET on Friday, September 4, when
#    a snapshot of total account equity will be taken."
#
# The market is shut between Thursday's close and that Friday snapshot, so
# equity at EOD Thursday Sep 3 is the equity the snapshot photographs. Taking
# the earlier of the two as the operative deadline is both correct and the
# conservative reading: it means stop taking risk by Thursday's close.
MEASUREMENT_ET = _et(2026, 9, 3, 16, 0)
MEASUREMENT_IS_CONFIRMED = True
MEASUREMENT_SOURCE = (
    "Alpaca official guidelines doc, 'Timeline' and FAQ rows; "
    "re-verified against the archived document 2026-08-30"
)

# Alpaca stops accepting options orders at 15:30 ET on expiration day.
OPTION_ORDER_CUTOFF_ET = (15, 30)

# Market holidays inside and just after the window. Labor Day matters because
# it sits between the 4 Sep measurement and the 8 Sep expiry, which is what
# makes that expiry cheap per calendar day.
HOLIDAYS_2026 = frozenset({date(2026, 9, 7)})  # Labor Day


def next_event(
    now: datetime,
    tier: int | None = None,
    within_hours: float = 48,
    measurement: datetime | None = None,
) -> MacroEvent | None:
    """The next scheduled catalyst that resolves BEFORE we are measured.

    The measurement filter is not a refinement, it is the point. The largest
    event in the calendar -- the August Employment Situation, tier 1, an 85bp
    expected move -- falls on Fri 4 Sep at 08:30 ET, which is AFTER the EOD
    Thu 3 Sep snapshot. Without this filter the agent spends its convexity
    budget on Wednesday buying a catalyst whose payoff lands after the account
    has already been photographed. It would look like a well-reasoned trade
    right up until it scored nothing.
    """
    cutoff = measurement or MEASUREMENT_ET
    upcoming = [
        e
        for e in CALENDAR
        if e.when > now
        and e.when <= cutoff
        and (tier is None or e.tier <= tier)
        and e.hours_until(now) <= within_hours
    ]
    return min(upcoming, key=lambda e: e.when) if upcoming else None


def post_measurement_events(measurement: datetime | None = None) -> list[MacroEvent]:
    """Catalysts that fall outside the scored window. Excluded from trading,
    kept so the journal can say explicitly why they were skipped."""
    cutoff = measurement or MEASUREMENT_ET
    return sorted([e for e in CALENDAR if e.when > cutoff], key=lambda e: e.when)


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


def sessions_remaining_at_measurement(expiry: date, measurement: datetime | None = None) -> int:
    """How many trading sessions the contract still has when we are measured.

    Inclusive of the measurement day itself, because the option still has that
    session to live when the account is valued. This is the number that decides
    whether a contract marks off a real two-sided quote or a 0DTE stub.

    For the EOD Thu 3 Sep measurement, with Labor Day on Mon 7 Sep:

        3 Sep  -> 1   expires the same afternoon. Alpaca states exercises and
                      assignments for this expiry ARE reflected in the EOD
                      value, so it is no longer a settlement risk -- but we
                      still close at 14:30 ET rather than rely on that.
        4 Sep  -> 2   Thu + Fri
        8 Sep  -> 3   Thu + Fri + Tue. Cheap per calendar day precisely BECAUSE
                      the holiday weekend sits inside it and carries no vol.
        11 Sep -> 6   Thu + Fri + Tue + Wed + Thu + Fri
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
