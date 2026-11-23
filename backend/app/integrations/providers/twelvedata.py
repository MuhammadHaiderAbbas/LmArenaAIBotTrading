"""Twelve Data adapter (equities, ETFs, FX) — optional API key.

Why this provider exists
------------------------
Yahoo's public chart endpoint aggressively rate-limits by IP and will throttle a
host for extended periods. That is a real availability risk, not a theoretical
one, so equities and ETFs need a second source. Twelve Data serves a `demo` key
with limited symbol coverage; supplying `TWELVEDATA_API_KEY` unlocks normal
symbols. When the key is missing or invalid the provider reports itself unhealthy
and the ingestion service falls through to the next candidate.

Free-tier limits (8 requests/minute, 800/day) are respected by the ingestion
service's rate limiter rather than by retry loops in here.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from app.integrations.providers.base import (
    MarketDataProvider,
    NormalisedBar,
    NormalisedQuote,
    ProviderError,
)
from app.models.market import Timeframe

BASE_URL = "https://api.twelvedata.com"
DEMO_KEY = "demo"

INTERVALS: dict[Timeframe, str] = {
    Timeframe.M1: "1min",
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.M30: "30min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1day",
    Timeframe.W1: "1week",
}


class TwelveDataProvider(MarketDataProvider):
    name = "twelvedata"
    display_name = "Twelve Data"
    supported_asset_classes = ("equity", "etf", "forex")
    supported_timeframes = tuple(INTERVALS)
    max_bars_per_request = 5000

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None,
                 timeout: float = 15.0) -> None:
        self._api_key = api_key or os.getenv("TWELVEDATA_API_KEY", DEMO_KEY)
        self._client = client
        self._timeout = timeout

    @property
    def using_demo_key(self) -> bool:
        return self._api_key == DEMO_KEY

    def _get(self, path: str, params: dict[str, object]) -> dict:
        params = {**params, "apikey": self._api_key}
        try:
            if self._client is not None:
                response = self._client.get(f"{BASE_URL}{path}", params=params)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(f"{BASE_URL}{path}", params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}", retryable=True) from exc

        if response.status_code == 429:
            raise ProviderError(self.name, "rate limited by Twelve Data", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"HTTP {response.status_code} from Twelve Data", retryable=True
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(self.name, "malformed JSON from Twelve Data") from exc

        # Twelve Data returns HTTP 200 with an error body for bad keys/symbols.
        if payload.get("status") == "error" or payload.get("code"):
            message = payload.get("message") or payload.get("code") or "unknown error"
            raise ProviderError(self.name, str(message))
        return payload

    def fetch_ohlc(
        self,
        provider_symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
        price_scale: float = 1.0,
    ) -> list[NormalisedBar]:
        interval = INTERVALS.get(timeframe)
        if interval is None:
            label = getattr(timeframe, "value", timeframe)
            raise ProviderError(self.name, f"unsupported timeframe {label}")

        payload = self._get(
            "/time_series",
            {
                "symbol": provider_symbol,
                "interval": interval,
                "outputsize": min(limit, self.max_bars_per_request),
                "order": "ASC",
            },
        )
        values = payload.get("values") or []
        if not values:
            raise ProviderError(self.name, f"no data for {provider_symbol}")

        bars: list[NormalisedBar] = []
        for row in values:
            try:
                open_time = _parse_datetime(row["datetime"])
                bars.append(
                    NormalisedBar(
                        open_time=open_time,
                        open=float(row["open"]) * price_scale,
                        high=float(row["high"]) * price_scale,
                        low=float(row["low"]) * price_scale,
                        close=float(row["close"]) * price_scale,
                        volume=float(row.get("volume") or 0),
                        is_final=True,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue  # skip malformed rows rather than inventing values

        if not bars:
            raise ProviderError(self.name, f"no parsable bars for {provider_symbol}")

        bars.sort(key=lambda bar: bar.open_time)
        if since is not None:
            bars = [bar for bar in bars if bar.open_time >= since]
        return bars[-limit:]

    def fetch_quote(self, provider_symbol: str) -> NormalisedQuote:
        payload = self._get("/quote", {"symbol": provider_symbol})
        last = payload.get("close")
        if last in (None, ""):
            raise ProviderError(self.name, f"no quote for {provider_symbol}")

        previous = payload.get("previous_close")
        change_pct = None
        if previous:
            try:
                change_pct = (float(last) - float(previous)) / float(previous) * 100
            except (TypeError, ValueError, ZeroDivisionError):
                change_pct = None

        return NormalisedQuote(
            last=float(last),
            open_24h=float(previous) if previous else None,
            change_24h_pct=change_pct,
            volume_24h=float(payload["volume"]) if payload.get("volume") else None,
            observed_at=_parse_datetime(payload.get("datetime"))
            if payload.get("datetime")
            else datetime.now(UTC),
        )

    def health(self) -> tuple[bool, str]:
        try:
            self._get("/quote", {"symbol": "AAPL"})
        except ProviderError as exc:
            return False, exc.message
        if self.using_demo_key:
            return True, "reachable (demo key: limited symbols)"
        return True, "reachable"


def _parse_datetime(value: str | None) -> datetime:
    """Twelve Data returns 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS' in UTC."""
    if not value:
        raise ValueError("missing datetime")
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    raise ValueError(f"unrecognised datetime '{value}'")
