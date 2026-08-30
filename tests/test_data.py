from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from alpaca.data.enums import OptionsFeed
from alpaca.trading.enums import AssetStatus, ContractType

from glassbox.data import MarketData

EXPIRY = date(2026, 9, 4)
OBSERVED_AT = datetime(2026, 9, 1, 14, 30, tzinfo=timezone.utc)
CALL = "SPY260904C00775000"
PUT = "SPY260904P00765000"


def _contract(
    symbol: str,
    contract_id: str,
    contract_type: ContractType,
    *,
    status: AssetStatus = AssetStatus.ACTIVE,
    tradable: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=contract_id,
        symbol=symbol,
        status=status,
        tradable=tradable,
        expiration_date=EXPIRY,
        underlying_symbol="SPY",
        type=contract_type,
    )


class FakeJournal:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, dict]] = []

    def append(self, actor: str, event: str, payload: dict) -> None:
        self.events.append((actor, event, payload))


class FakeTrading:
    def __init__(self) -> None:
        self.requests = []

    def get_option_contracts(self, request):
        self.requests.append(request)
        if request.type == ContractType.CALL and request.page_token is None:
            return SimpleNamespace(
                option_contracts=[_contract(CALL, "call-id", ContractType.CALL)],
                next_page_token="call-page-2",
            )
        if request.type == ContractType.CALL:
            return SimpleNamespace(option_contracts=[], next_page_token=None)
        return SimpleNamespace(
            option_contracts=[_contract(PUT, "put-id", ContractType.PUT)],
            next_page_token=None,
        )


class FakeOptionData:
    def __init__(self, chain: dict | None = None) -> None:
        self.chain = chain or {}
        self.requests = []

    def get_option_chain(self, request):
        self.requests.append(request)
        return self.chain


class FakeBroker:
    def __init__(self, trading: FakeTrading, journal: FakeJournal | None = None) -> None:
        self.trading = trading
        self.journal = journal

    @staticmethod
    def _call(fn, _what):
        return fn()


def _snapshot(bid: str, ask: str, timestamp: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        latest_quote=SimpleNamespace(
            bid_price=float(bid),
            ask_price=float(ask),
            timestamp=timestamp or OBSERVED_AT - timedelta(seconds=4),
        ),
        implied_volatility=0.18,
    )


def test_active_option_contracts_use_explicit_filters_and_pagination() -> None:
    trading = FakeTrading()
    data = MarketData(
        FakeBroker(trading),
        option_data_client=FakeOptionData(),
        clock=lambda: OBSERVED_AT,
    )

    contracts = data.active_option_contracts("SPY", EXPIRY)

    assert set(contracts) == {CALL, PUT}
    assert len(trading.requests) == 3
    assert [request.type for request in trading.requests] == [
        ContractType.CALL,
        ContractType.CALL,
        ContractType.PUT,
    ]
    assert trading.requests[1].page_token == "call-page-2"
    assert all(request.underlying_symbols == ["SPY"] for request in trading.requests)
    assert all(request.status == AssetStatus.ACTIVE for request in trading.requests)
    assert all(request.expiration_date == EXPIRY for request in trading.requests)


def test_active_option_contracts_validate_documented_dictionary_fallback() -> None:
    class DictionaryTrading:
        @staticmethod
        def get_option_contracts(request):
            rows = (
                [
                    {
                        "id": "call-id",
                        "symbol": CALL,
                        "status": "active",
                        "tradable": True,
                        "expiration_date": EXPIRY.isoformat(),
                        "underlying_symbol": "SPY",
                        "type": "call",
                    }
                ]
                if request.type == ContractType.CALL
                else []
            )
            return {"option_contracts": rows, "next_page_token": None}

    data = MarketData(
        FakeBroker(DictionaryTrading()),
        option_data_client=FakeOptionData(),
        clock=lambda: OBSERVED_AT,
    )

    contracts = data.active_option_contracts("SPY", EXPIRY)

    assert contracts[CALL].contract_id == "call-id"
    assert contracts[CALL].symbol == CALL


def test_option_data_client_is_reused_and_feed_is_explicit() -> None:
    option_data = FakeOptionData()
    data = MarketData(
        FakeBroker(FakeTrading()),
        option_data_client=option_data,
        clock=lambda: OBSERVED_AT,
        cache_seconds=0,
    )

    data.option_chain("SPY")
    data.option_chain("QQQ")

    assert len(option_data.requests) == 2
    assert all(request.feed == OptionsFeed.INDICATIVE for request in option_data.requests)


def test_chain_legs_captures_active_contract_quote_provenance() -> None:
    option_data = FakeOptionData({CALL: _snapshot("1.10", "1.14"), PUT: _snapshot("1.16", "1.20")})
    data = MarketData(
        FakeBroker(FakeTrading()),
        option_data_client=option_data,
        clock=lambda: OBSERVED_AT,
    )

    legs = data.chain_legs("SPY", EXPIRY, Decimal("770"))[EXPIRY]

    assert [leg.symbol for leg in legs] == [CALL, PUT]
    assert legs[0].quote_snapshot.contract_id == "call-id"
    assert legs[0].quote_snapshot.feed == "indicative"
    assert legs[0].quote_snapshot.venue_timestamp == OBSERVED_AT - timedelta(seconds=4)
    assert legs[0].quote_snapshot.observed_at == OBSERVED_AT
    assert legs[0].quote_snapshot.age_seconds == Decimal("4.0")
    assert legs[0].quote_snapshot.bid == Decimal("1.1")
    assert legs[0].quote_snapshot.ask == Decimal("1.14")


def test_invalid_quote_is_journaled_once_and_removed() -> None:
    journal = FakeJournal()
    option_data = FakeOptionData(
        {
            CALL: _snapshot("1.20", "1.10"),
            PUT: _snapshot("1.16", "1.20"),
        }
    )
    data = MarketData(
        FakeBroker(FakeTrading(), journal),
        option_data_client=option_data,
        clock=lambda: OBSERVED_AT,
    )

    legs = data.chain_legs("SPY", EXPIRY, Decimal("770"))[EXPIRY]

    assert [leg.symbol for leg in legs] == [PUT]
    assert journal.events == [
        (
            "market_data",
            "OPTION_QUOTE_REJECTED",
            {"symbol": CALL, "reason": "crossed_quote"},
        )
    ]


def test_missing_quote_timestamp_is_journaled_and_removed() -> None:
    journal = FakeJournal()
    snapshot = _snapshot("1.10", "1.14")
    snapshot.latest_quote.timestamp = None
    data = MarketData(
        FakeBroker(FakeTrading(), journal),
        option_data_client=FakeOptionData({CALL: snapshot}),
        clock=lambda: OBSERVED_AT,
    )

    assert data.chain_legs("SPY", EXPIRY, Decimal("770"))[EXPIRY] == []
    assert journal.events == [
        (
            "market_data",
            "OPTION_QUOTE_REJECTED",
            {"symbol": CALL, "reason": "missing_quote_timestamp"},
        )
    ]
