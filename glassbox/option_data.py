"""Alpaca option-contract and quote acquisition with point-in-time validation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any, Callable, Sequence, cast

from . import config as C
from . import env
from .candidates import ActiveOptionContract, CandidateDataInvalid, OptionQuoteSnapshot
from .schema import OptionContract
from .strategies.event_vol import ChainLeg, ExpiryQuote


class OptionDataGateway:
    """Own one option client and shape authoritative contracts and quotes."""

    def __init__(
        self,
        broker,
        cache_seconds: int,
        *,
        option_data_client: Any | None,
        clock: Callable[[], datetime],
    ) -> None:
        self.broker = broker
        self.cache_seconds = cache_seconds
        self._option_data = option_data_client
        self.clock = clock
        self._cache: dict[str, tuple[datetime, object]] = {}

    def _cached(self, key: str, fetch: Callable[[], object]) -> object:
        now = self.clock()
        hit = self._cache.get(key)
        if hit and (now - hit[0]).total_seconds() < self.cache_seconds:
            return hit[1]
        value = fetch()
        self._cache[key] = (now, value)
        return value

    def option_chain(self, underlying: str) -> dict:
        """Return one explicitly sourced Alpaca option chain snapshot."""
        from alpaca.data.enums import OptionsFeed
        from alpaca.data.requests import OptionChainRequest

        def fetch() -> object:
            request = OptionChainRequest(
                underlying_symbol=underlying,
                feed=OptionsFeed(C.OPTION_DATA_FEED),
            )
            return self.broker._call(
                lambda: self._option_client().get_option_chain(request),
                f"chain:{underlying}",
            )

        return cast(dict, self._cached(f"chain:{underlying}", fetch))

    def option_surface(self, underlying: str, symbols: Sequence[str]) -> dict[str, Any]:
        """Greeks and implied volatility for specific contracts.

        Sourced from the same cached chain snapshot the rest of the module
        uses, so adding surface analysis costs no extra request against a rate
        limit that is shared by every loop. A contract whose snapshot carries
        no usable Greeks is simply absent from the result -- the caller
        abstains rather than substituting zeros.
        """
        from .greeks import LegGreeks

        chain = self.option_chain(underlying)
        surface: dict[str, Any] = {}
        for symbol in symbols:
            snapshot = chain.get(symbol) if isinstance(chain, dict) else None
            if snapshot is None:
                continue
            parsed = LegGreeks.from_snapshot(symbol, snapshot)
            if parsed is not None:
                surface[symbol] = parsed
        return surface

    def _option_client(self):
        if self._option_data is None:
            from alpaca.data.historical.option import OptionHistoricalDataClient

            self._option_data = OptionHistoricalDataClient(
                env.get("ALPACA_API_KEY"), env.get("ALPACA_SECRET_KEY")
            )
        return self._option_data

    def active_contracts(self, underlying: str, expiry: date) -> dict[str, ActiveOptionContract]:
        """Fetch every active call and put page for one exact expiry."""
        key = f"contracts:{underlying}:{expiry}"
        return cast(
            dict[str, ActiveOptionContract],
            self._cached(key, lambda: self._fetch_active_contracts(underlying, expiry)),
        )

    def _fetch_active_contracts(
        self, underlying: str, expiry: date
    ) -> dict[str, ActiveOptionContract]:
        from alpaca.trading.enums import AssetStatus, ContractType
        from alpaca.trading.requests import GetOptionContractsRequest

        contracts: dict[str, ActiveOptionContract] = {}
        for contract_type in (ContractType.CALL, ContractType.PUT):
            page_token: str | None = None
            seen_tokens: set[str] = set()
            while True:
                request = GetOptionContractsRequest(
                    underlying_symbols=[underlying],
                    status=AssetStatus.ACTIVE,
                    expiration_date=expiry,
                    type=contract_type,
                    limit=1000,
                    page_token=page_token,
                )
                response = self.broker._call(
                    lambda request=request: self.broker.trading.get_option_contracts(request),
                    f"option_contracts:{underlying}:{expiry}:{contract_type.value}",
                )
                records = _response_field(response, "option_contracts")
                if records is None:
                    records = []
                if not isinstance(records, (list, tuple)):
                    raise RuntimeError("option contract response contains invalid records")
                for record in records:
                    try:
                        captured = ActiveOptionContract.capture(
                            record,
                            underlying=underlying,
                            expiry=expiry,
                            contract_type=contract_type.value,
                        )
                    except CandidateDataInvalid as exc:
                        self._record_rejection(
                            "OPTION_CONTRACT_REJECTED",
                            str(_record_field(record, "symbol") or "unknown"),
                            str(exc),
                        )
                        continue
                    contracts[captured.symbol] = captured

                next_token = _response_field(response, "next_page_token")
                if not next_token:
                    break
                if str(next_token) in seen_tokens:
                    raise RuntimeError("option contract pagination repeated a token")
                seen_tokens.add(str(next_token))
                page_token = str(next_token)
        return contracts

    def expiry_quotes(
        self, underlying: str, spot: Decimal, band: float = 0.01
    ) -> list[ExpiryQuote]:
        """Build the deterministic ATM term structure consumed by the strategy."""
        rows: dict[date, list[tuple[Decimal, Decimal, Decimal]]] = {}
        for symbol, snapshot in self.option_chain(underlying).items():
            implied = getattr(snapshot, "implied_volatility", None)
            quote = getattr(snapshot, "latest_quote", None)
            if implied is None or quote is None:
                continue
            try:
                contract = OptionContract.parse(symbol)
            except ValueError:
                continue
            if abs(contract.strike - spot) / spot >= Decimal(str(band)):
                continue
            bid = _decimal_or_none(getattr(quote, "bid_price", None))
            ask = _decimal_or_none(getattr(quote, "ask_price", None))
            if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
                continue
            midpoint = (bid + ask) / Decimal("2")
            spread = (ask - bid) / midpoint
            rows.setdefault(contract.expiry, []).append((Decimal(str(implied)), spread, midpoint))

        output: list[ExpiryQuote] = []
        for expiry, values in sorted(rows.items()):
            implied = sum((value[0] for value in values), Decimal("0")) / len(values)
            output.append(
                ExpiryQuote(
                    expiry=expiry,
                    atm_iv=implied.quantize(Decimal("0.0001")),
                    atm_straddle_px=(median(value[2] for value in values) * 2).quantize(
                        Decimal("0.01")
                    ),
                    bid_ask_pct=median(value[1] for value in values).quantize(Decimal("0.0001")),
                )
            )
        return output

    def chain_legs(
        self, underlying: str, expiry: date, spot: Decimal, band: float = 0.05
    ) -> dict[date, list[ChainLeg]]:
        """Return validated active contract quotes near the underlying price."""
        chain = self.option_chain(underlying)
        contracts = self.active_contracts(underlying, expiry)
        observed_at = self.clock()
        legs: list[ChainLeg] = []
        for symbol, option_snapshot in chain.items():
            contract = contracts.get(symbol)
            if contract is None:
                continue
            try:
                parsed = OptionContract.parse(symbol)
            except ValueError:
                continue
            if parsed.expiry != expiry or abs(parsed.strike - spot) / spot > Decimal(str(band)):
                continue
            quote = getattr(option_snapshot, "latest_quote", None)
            try:
                quote_snapshot = OptionQuoteSnapshot.capture(
                    contract_id=contract.contract_id,
                    symbol=symbol,
                    status=contract.status,
                    tradable=contract.tradable,
                    quote_source="alpaca_option_chain",
                    feed=C.OPTION_DATA_FEED,
                    venue_timestamp=getattr(quote, "timestamp", observed_at),
                    observed_at=observed_at,
                    bid=_decimal_or_none(getattr(quote, "bid_price", None)),
                    ask=_decimal_or_none(getattr(quote, "ask_price", None)),
                    max_age_seconds=C.MAX_OPTION_QUOTE_AGE_SECONDS,
                    max_spread_pct=C.MAX_ATM_SPREAD_PCT,
                )
            except CandidateDataInvalid as exc:
                self._record_rejection("OPTION_QUOTE_REJECTED", symbol, str(exc))
                continue
            legs.append(
                ChainLeg(
                    symbol=symbol,
                    strike=parsed.strike,
                    right=parsed.right,
                    ask=quote_snapshot.ask,
                    bid=quote_snapshot.bid,
                    quote_snapshot=quote_snapshot,
                )
            )
        return {expiry: legs}

    def _record_rejection(self, event: str, symbol: str, reason: str) -> None:
        journal = getattr(self.broker, "journal", None)
        if journal is not None:
            journal.append(
                "market_data",
                event,
                {"symbol": symbol, "reason": reason},
            )


def _response_field(response: object, name: str) -> object:
    if isinstance(response, dict):
        return response.get(name)
    return getattr(response, name, None)


def _record_field(record: object, name: str) -> object:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name, None)


def _decimal_or_none(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))
