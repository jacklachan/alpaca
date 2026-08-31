"""Trading sessions, and which calendar answered.

The number under test decides whether a contract has real quotes or is a 0DTE
stub, so the cases that matter are the ones where a hardcoded table is quietly
wrong: a holiday outside the competition week, and a venue that will not answer.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from glassbox import market_calendar as mc
from glassbox.market_calendar import MarketCalendar

MON = date(2026, 8, 31)
FRI = date(2026, 9, 4)
LABOR_DAY = date(2026, 9, 7)
THANKSGIVING = date(2026, 11, 26)


def venue(sessions):
    """A fake Alpaca calendar returning exactly these session days."""

    def fetch(start, end):
        return [SimpleNamespace(date=d) for d in sessions if start <= d <= end]

    return fetch


# -- the venue is the authority ------------------------------------------------


def test_sessions_come_from_the_venue_when_a_client_is_available():
    days = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
    cal = MarketCalendar(fetch=venue(days))

    assert cal.sessions_between(MON, date(2026, 9, 3)) == 3
    assert cal.source == "alpaca"


def test_the_venue_can_contradict_the_weekday_heuristic():
    """A Wednesday the venue says is closed must count as closed. This is the
    whole reason not to compute sessions from weekday numbers."""
    closed_wednesday = [date(2026, 9, 1), date(2026, 9, 3)]  # 2 Sep missing
    cal = MarketCalendar(fetch=venue(closed_wednesday))

    assert cal.is_session(date(2026, 9, 2)) is False
    assert cal.sessions_between(MON, date(2026, 9, 3)) == 2


def test_one_fetch_serves_a_whole_run():
    calls = {"n": 0}

    def counting(start, end):
        calls["n"] += 1
        return [SimpleNamespace(date=d) for d in mc._weekday_sessions(start, end)]

    cal = MarketCalendar(fetch=counting)
    cal.sessions_between(MON, FRI)
    cal.sessions_between(MON, date(2026, 9, 3))
    cal.is_session(date(2026, 9, 2))

    assert calls["n"] == 1, "the calendar refetched inside a covered window"


# -- the fallback, and saying so -----------------------------------------------


def test_no_client_means_offline_sessions_and_says_so():
    cal = MarketCalendar()
    assert cal.sessions_between(MON, FRI) == 4  # Tue-Fri
    assert cal.source == "offline"
    assert cal.provenance()["source"] == "offline"


def test_a_calendar_outage_falls_back_without_stopping_the_agent():
    """A calendar failure must not halt trading, and must not pass unnoticed."""

    def broken(start, end):
        raise RuntimeError("calendar endpoint down")

    cal = MarketCalendar(fetch=broken)
    assert cal.sessions_between(MON, FRI) == 4
    assert "unavailable" in cal.source


def test_an_empty_venue_response_falls_back_rather_than_reporting_no_sessions():
    """Zero sessions would make every contract look expired."""
    cal = MarketCalendar(fetch=lambda s, e: [])
    assert cal.sessions_between(MON, FRI) == 4


def test_the_offline_table_knows_holidays_outside_the_competition_week():
    """The old hardcoded set held exactly one date. An agent still running in
    November counted Thanksgiving as a session and overstated every expiry."""
    cal = MarketCalendar()
    assert cal.is_session(LABOR_DAY) is False
    assert cal.is_session(THANKSGIVING) is False
    assert cal.is_session(date(2026, 12, 25)) is False


# -- the numbers the strategy actually asks for --------------------------------


def test_sessions_between_is_exclusive_of_the_start_day():
    cal = MarketCalendar(fetch=venue(mc._weekday_sessions(MON, FRI)))
    assert cal.sessions_between(MON, MON) == 0
    assert cal.sessions_between(MON, date(2026, 9, 1)) == 1


def test_sessions_between_is_zero_when_the_end_precedes_the_start():
    assert MarketCalendar().sessions_between(FRI, MON) == 0


def test_sessions_remaining_includes_the_measurement_day():
    """The option still has that session to live when the snapshot is taken."""
    cal = MarketCalendar()
    measurement = date(2026, 9, 3)
    assert cal.sessions_remaining_at(date(2026, 9, 3), measurement) == 1
    assert cal.sessions_remaining_at(date(2026, 9, 4), measurement) == 2


def test_an_expiry_before_measurement_has_no_sessions_left():
    assert MarketCalendar().sessions_remaining_at(date(2026, 9, 1), date(2026, 9, 3)) == 0


# -- parsing whatever the venue sends ------------------------------------------


@pytest.mark.parametrize(
    "value",
    [date(2026, 9, 1), "2026-09-01", "2026-09-01T00:00:00Z"],
)
def test_session_dates_parse_from_every_shape_alpaca_uses(value):
    assert mc._session_date(SimpleNamespace(date=value)) == date(2026, 9, 1)


def test_an_unparseable_session_entry_is_dropped_not_guessed():
    assert mc._session_date(SimpleNamespace(date="not-a-date")) is None


def test_unparseable_entries_do_not_poison_the_window():
    cal = MarketCalendar(
        fetch=lambda s, e: [SimpleNamespace(date=date(2026, 9, 1)), SimpleNamespace(date="junk")]
    )
    assert cal.sessions_between(MON, FRI) == 1
    assert cal.source == "alpaca"


def test_provenance_records_the_window_and_the_source():
    cal = MarketCalendar(fetch=venue(mc._weekday_sessions(MON, FRI)))
    cal.sessions_between(MON, FRI)
    p = cal.provenance()
    assert p["source"] == "alpaca"
    assert p["sessions_known"] > 0
    assert p["covered_from"] and p["covered_to"]


def test_from_broker_builds_a_calendar_that_calls_the_trading_client():
    seen = {}

    class FakeBroker:
        def _call(self, fn, what):
            seen["what"] = what
            return fn()

        trading = SimpleNamespace(
            get_calendar=lambda request: [SimpleNamespace(date=date(2026, 9, 1))]
        )

    cal = mc.from_broker(FakeBroker())
    assert cal.sessions_between(MON, FRI) == 1
    assert seen["what"] == "get_calendar"
