"""Deterministic offline bars, for installs with no network.

A fresh clone with no internet has an empty `market_candles` table, which makes
monitoring report CRITICAL and the release gate fail on day one. That is technically
honest and practically useless: the platform looks broken when it is merely offline.

This provider fixes the first-run experience **without ever pretending to be a market**:

* Deterministic. The same symbol and timestamp always produce the same bar, so two
  installs agree and re-running ingestion does not rewrite history.
* Labelled. `name = "synthetic"`, every bar records `provider="synthetic"`, and the
  registry's provider mapping marks it as non-real.
* Never automatic. It is not in the default provider map. It only runs when
  `SEED_SYNTHETIC_DATA=1` or `python -m app.seed --synthetic` is used explicitly, and the
  seed prints a warning saying the bars are generated.
* Plainly disclosed. `SYNTHETIC_NOTICE` is surfaced by `/markets/providers` and by the
  seed's log line, so anyone looking at the data can tell what it is.

The alternative — shipping a database file with fake prices in it — is the thing this
project exists not to do.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime

from app.integrations.providers.base import (
    MarketDataProvider,
    NormalisedBar,
    NormalisedQuote,
    ProviderError,
)
from app.models.market import Timeframe

SYNTHETIC_NOTICE = (
    "SYNTHETIC DATA. Bars from this provider are generated deterministically for offline "
    "development and are NOT market data. Do not use them to evaluate a strategy, and do "
    "not present any result computed from them as a performance claim."
)

# A plausible starting level per asset class, so charts are not all the same shape.
_ANCHORS: dict[str, float] = {
    "crypto": 60_000.0,
    "forex": 1.27,
    "equity": 180.0,
}

# Rough per-bar volatility as a fraction of price.
_VOLATILITY: dict[str, float] = {"crypto": 0.018, "forex": 0.0022, "equity": 0.009}


def _digest(symbol: str, bucket: int) -> float:
    """A stable pseudo-random value in [-1, 1) from (symbol, bar index)."""
    raw = hashlib.sha256(f"{symbol}:{bucket}".encode()).digest()
    return (int.from_bytes(raw[:8], "big") / float(1 << 63)) - 1.0


def _asset_class_from_symbol(symbol: str) -> str:
    upper = symbol.upper()
    if "/" in upper:
        base, quote = upper.split("/", 1)
        if quote in {"USD", "GBP", "EUR", "JPY", "CHF", "AUD", "CAD"} and base in {
            "BTC",
            "ETH",
            "SOL",
            "XRP",
            "ADA",
            "DOGE",
        }:
            return "crypto"
        return "forex"
    return "equity"


class SyntheticProvider(MarketDataProvider):
    """Offline, deterministic bars. See the module docstring for the disclosure rules."""

    name = "synthetic"
    display_name = "Synthetic (offline development data)"
    supported_asset_classes = ("crypto", "forex", "equity")
    max_bars_per_request = 1000

    def _bar(self, symbol: str, timeframe: Timeframe, open_time: datetime) -> NormalisedBar:
        asset_class = _asset_class_from_symbol(symbol)
        anchor = _ANCHORS[asset_class]
        volatility = _VOLATILITY[asset_class]
        bucket = int(open_time.timestamp() // timeframe.seconds)

        # A slow trend plus a fast oscillation plus noise, all seeded by the symbol so
        # two instruments never trace the same path.
        phase = _digest(symbol, 0) * math.pi
        trend = math.sin(bucket / 90.0 + phase) * volatility * 6
        cycle = math.sin(bucket / 7.0 + phase * 2) * volatility * 2
        noise = _digest(symbol, bucket) * volatility
        close = anchor * (1.0 + trend + cycle + noise)
        open_ = anchor * (1.0 + trend + cycle)
        wick = abs(_digest(symbol, bucket + 7919)) * volatility

        high = max(open_, close) * (1.0 + wick)
        low = min(open_, close) * (1.0 - wick)
        volume = 1_000.0 * (1.0 + abs(_digest(symbol, bucket + 104_729)) * 10.0)

        return NormalisedBar(
            open_time=open_time,
            open=round(open_, 6),
            high=round(high, 6),
            low=round(low, 6),
            close=round(close, 6),
            volume=round(volume, 2),
            is_final=True,
        )

    def fetch_ohlc(
        self,
        provider_symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[NormalisedBar]:
        if limit <= 0:
            raise ProviderError(self.name, "limit must be positive")
        limit = min(limit, self.max_bars_per_request)
        seconds = timeframe.seconds
        now = datetime.now(UTC)
        # Floor to the current bar, then walk backwards. The most recent bar is excluded
        # so nothing downstream treats a forming bar as closed.
        latest = int(now.timestamp() // seconds) * seconds - seconds
        start = latest - (limit - 1) * seconds
        if since is not None:
            floor = int(since.timestamp() // seconds) * seconds
            start = max(start, floor)

        bars = []
        for epoch in range(start, latest + 1, seconds):
            bar = self._bar(provider_symbol, timeframe, datetime.fromtimestamp(epoch, tz=UTC))
            if since is not None and bar.open_time < since:
                continue
            bars.append(bar)
        return bars

    def fetch_quote(self, provider_symbol: str) -> NormalisedQuote:
        bars = self.fetch_ohlc(provider_symbol, Timeframe.D1, limit=2)
        if not bars:
            raise ProviderError(self.name, f"no bars for {provider_symbol}")
        last = bars[-1].close
        spread = abs(last) * 0.0004
        return NormalisedQuote(
            last=last,
            observed_at=datetime.now(UTC),
            bid=round(last - spread, 6),
            ask=round(last + spread, 6),
            open_24h=bars[0].open,
            change_24h_pct=((last - bars[0].open) / bars[0].open * 100) if bars[0].open else None,
            volume_24h=bars[-1].volume,
        )

    def health(self) -> tuple[bool, str]:
        """Always healthy: it cannot fail, which is exactly why its bars are not trusted."""
        return True, "Synthetic provider is available offline."
