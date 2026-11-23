"""Market-data provider abstraction.

Every provider implements the same protocol so the ingestion service, strategies
and UI never depend on a venue. Providers return *normalised* bars and quotes;
they never touch the database.

Failures must raise `ProviderError` with a message that is safe to log. Do not
retry inside a provider — the ingestion service owns retry policy so rate limits
are respected in one place.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.models.market import Timeframe


class ProviderError(Exception):
    """A provider could not serve the request (network, rate limit, bad symbol)."""

    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message
        self.retryable = retryable


@dataclass(slots=True)
class NormalisedBar:
    """A single OHLCV bar, already in quote-currency units and UTC."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    vwap: float | None = None
    trades: int | None = None
    is_final: bool = True


@dataclass(slots=True)
class NormalisedQuote:
    last: float
    observed_at: datetime
    bid: float | None = None
    ask: float | None = None
    open_24h: float | None = None
    change_24h_pct: float | None = None
    volume_24h: float | None = None

    @property
    def mid(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.last


@dataclass(slots=True)
class InstrumentSpec:
    """Provider-independent description of a tradable instrument."""

    symbol: str
    name: str
    asset_class: str
    venue_code: str
    venue_name: str
    base_currency: str
    quote_currency: str
    price_scale: float = 1.0
    tick_size: float = 0.0001
    lot_size: float = 0.0001
    is_tradable: bool = False
    description: str = ""
    provider_symbols: dict[str, str] = field(default_factory=dict)


class MarketDataProvider(ABC):
    """Protocol implemented by Kraken, Yahoo and any future venue."""

    name: str = "unknown"
    display_name: str = "Unknown provider"
    supported_asset_classes: tuple[str, ...] = ()
    supported_timeframes: tuple[Timeframe, ...] = (
        Timeframe.M5,
        Timeframe.M15,
        Timeframe.H1,
        Timeframe.H4,
        Timeframe.D1,
    )
    max_bars_per_request: int = 500

    @abstractmethod
    def fetch_ohlc(
        self,
        provider_symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[NormalisedBar]:
        """Return bars in ascending time order, oldest first."""

    @abstractmethod
    def fetch_quote(self, provider_symbol: str) -> NormalisedQuote:
        """Return the latest quote."""

    @abstractmethod
    def health(self) -> tuple[bool, str]:
        """Return (healthy, message). Must not raise."""

    def supports(self, timeframe: Timeframe) -> bool:
        return timeframe in self.supported_timeframes

    @staticmethod
    def _utc(timestamp: float | int) -> datetime:
        return datetime.fromtimestamp(float(timestamp), tz=UTC)


def bar_open_time(ts: datetime, timeframe: Timeframe) -> datetime:
    """Floor a timestamp to the start of its bar, in UTC."""
    seconds = timeframe.seconds
    epoch = int(ts.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % seconds), tz=UTC)
