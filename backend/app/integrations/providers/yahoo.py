"""Yahoo Finance chart adapter (equities, ETFs and FX reference rates).

Uses the public `/v8/finance/chart` endpoint. No key is required and the response
is unauthenticated reference data suitable for research and paper trading.

Handling notes
--------------
* A browser-like User-Agent is required; the default client is rejected.
* London-quoted instruments (``.L``) return **pence**, not pounds. The adapter
  normalises to quote currency using the instrument's ``price_scale`` so a
  £35.64 share is never stored as 3564.
* Yahoo omits rows for halted sessions; missing values are dropped rather than
  zero-filled, and the quality checker reports the resulting gap.
* FX pairs (``GBPUSD=X``) report a currency of ``USD`` with no venue session.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.integrations.providers.base import (
    MarketDataProvider,
    NormalisedBar,
    NormalisedQuote,
    ProviderError,
)
from app.models.market import Timeframe

# Yahoo rate-limits per host. Both hosts serve identical data, so the adapter
# fails over between them before reporting the provider as unavailable.
HOSTS = (
    "https://query2.finance.yahoo.com/v8/finance/chart",
    "https://query1.finance.yahoo.com/v8/finance/chart",
)

# Yahoo accepts ranges/intervals rather than bar counts. Map each interval to a
# (range, interval) pair that comfortably covers `max_bars_per_request`.
YAHOO_INTERVALS: dict[Timeframe, tuple[str, str]] = {
    Timeframe.M1: ("1d", "1m"),
    Timeframe.M5: ("5d", "5m"),
    Timeframe.M15: ("1mo", "15m"),
    Timeframe.M30: ("1mo", "30m"),
    Timeframe.H1: ("3mo", "1h"),
    Timeframe.H4: ("1y", "1h"),  # aggregated from hourly bars
    Timeframe.D1: ("5y", "1d"),
    Timeframe.W1: ("10y", "1wk"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}

# Yahoo's anti-scraping layer throttles bare requests. Presenting the same origin
# cookie a browser would hold is usually enough to be served. A persistent cookie
# jar keeps consent state across calls in a long-lived process.
_COOKIE_JAR = httpx.Cookies()
_COOKIE_HOST = "https://fc.yahoo.com"


def _client_for(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers=HEADERS, cookies=_COOKIE_JAR)


class YahooProvider(MarketDataProvider):
    name = "yahoo"
    display_name = "Yahoo Finance"
    supported_asset_classes = ("equity", "etf", "forex")
    supported_timeframes = tuple(YAHOO_INTERVALS)
    max_bars_per_request = 500

    def __init__(self, client: httpx.Client | None = None, timeout: float = 12.0) -> None:
        self._client = client
        self._timeout = timeout

    def _get(self, provider_symbol: str, params: dict[str, str]) -> dict:
        response = None
        last_error: str = "no host attempted"
        for host in HOSTS:
            url = f"{host}/{provider_symbol}"
            try:
                if self._client is not None:
                    response = self._client.get(url, params=params, headers=HEADERS)
                else:
                    with _client_for(self._timeout) as client:
                        response = client.get(url, params=params)
            except httpx.HTTPError as exc:
                last_error = f"network error: {exc}"
                continue
            if response.status_code in (401, 403, 429):
                # Refresh the origin cookie once, then retry this host before
                # failing over — throttling is usually per-cookie, not per-query.
                _prime_cookies(self._timeout)
                if self._client is not None:
                    response = self._client.get(url, params=params, headers=HEADERS)
                else:
                    with _client_for(self._timeout) as client:
                        response = client.get(url, params=params)
                if response.status_code in (401, 403, 429):
                    last_error = (
                        f"rate limited by Yahoo (HTTP {response.status_code})"
                        if response.status_code == 429
                        else f"Yahoo rejected the request (HTTP {response.status_code})"
                    )
                    continue
            break

        if response is None or response.status_code == 429:
            raise ProviderError(self.name, last_error, retryable=True)
        if response.status_code == 404:
            raise ProviderError(self.name, f"symbol {provider_symbol} not found")
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"HTTP {response.status_code} from Yahoo", retryable=True
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(self.name, "malformed JSON from Yahoo", retryable=True) from exc

        chart = payload.get("chart") or {}
        if chart.get("error"):
            error = chart["error"]
            raise ProviderError(self.name, f"{error.get('code')}: {error.get('description')}")
        results = chart.get("result")
        if not results:
            raise ProviderError(self.name, f"no data for {provider_symbol}")
        return results[0]

    def fetch_ohlc(
        self,
        provider_symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
        price_scale: float = 1.0,
    ) -> list[NormalisedBar]:
        mapping = YAHOO_INTERVALS.get(timeframe)
        if mapping is None:
            raise ProviderError(self.name, f"unsupported timeframe {timeframe.value}")
        span, interval = mapping

        result = self._get(provider_symbol, {"range": span, "interval": interval})
        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators") or {}).get("quote") or [{}]
        series = quote[0]

        opens = series.get("open") or []
        highs = series.get("high") or []
        lows = series.get("low") or []
        closes = series.get("close") or []
        volumes = series.get("volume") or []

        bars: list[NormalisedBar] = []
        for index, timestamp in enumerate(timestamps):
            close = closes[index] if index < len(closes) else None
            open_ = opens[index] if index < len(opens) else None
            if close is None or open_ is None:
                continue  # halted or incomplete session — drop, never zero-fill
            high = highs[index] if index < len(highs) else close
            low = lows[index] if index < len(lows) else close
            volume = volumes[index] if index < len(volumes) else 0
            bars.append(
                NormalisedBar(
                    open_time=self._utc(timestamp),
                    open=open_ * price_scale,
                    high=(high or close) * price_scale,
                    low=(low or close) * price_scale,
                    close=close * price_scale,
                    volume=float(volume or 0),
                    is_final=True,
                )
            )

        if timeframe is Timeframe.H4:
            bars = _aggregate(bars, 4)
        if since is not None:
            bars = [bar for bar in bars if bar.open_time >= since]

        bars = sorted(bars, key=lambda bar: bar.open_time)[-limit:]
        if bars:
            bars[-1].is_final = False  # most recent bar may still be forming
        return bars

    def fetch_quote(self, provider_symbol: str) -> NormalisedQuote:
        result = self._get(provider_symbol, {"range": "1d", "interval": "1d"})
        meta = result.get("meta") or {}
        last = meta.get("regularMarketPrice")
        if last is None:
            raise ProviderError(self.name, f"no price for {provider_symbol}")

        previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        change = None
        if previous_close:
            change = (last - previous_close) / previous_close * 100

        volume = None
        quote = (result.get("indicators") or {}).get("quote") or [{}]
        volumes = quote[0].get("volume") or []
        if volumes and volumes[-1] is not None:
            volume = float(volumes[-1])

        observed = meta.get("regularMarketTime")
        return NormalisedQuote(
            last=float(last),
            open_24h=float(previous_close) if previous_close else None,
            change_24h_pct=change,
            volume_24h=volume,
            observed_at=(
                self._utc(observed) if observed else datetime.now(UTC)
            ),
        )

    def health(self) -> tuple[bool, str]:
        try:
            self.fetch_quote("AAPL")
            return True, "reachable"
        except ProviderError as exc:
            return False, exc.message


def _aggregate(bars: list[NormalisedBar], factor: int) -> list[NormalisedBar]:
    """Aggregate consecutive bars into larger buckets (e.g. hourly → 4-hourly)."""
    grouped: list[NormalisedBar] = []
    bucket: list[NormalisedBar] = []
    for bar in bars:
        bucket.append(bar)
        if len(bucket) == factor:
            grouped.append(_combine(bucket))
            bucket = []
    if bucket:
        grouped.append(_combine(bucket))
    return grouped


def _combine(bars: list[NormalisedBar]) -> NormalisedBar:
    return NormalisedBar(
        open_time=bars[0].open_time,
        open=bars[0].open,
        high=max(bar.high for bar in bars),
        low=min(bar.low for bar in bars),
        close=bars[-1].close,
        volume=sum(bar.volume for bar in bars),
        is_final=bars[-1].is_final,
    )


def _prime_cookies(timeout: float) -> None:
    """Collect Yahoo's consent cookie. Failure is non-fatal."""
    try:
        with httpx.Client(timeout=timeout, headers=HEADERS, cookies=_COOKIE_JAR) as client:
            client.get(_COOKIE_HOST, follow_redirects=True)
    except httpx.HTTPError:
        return
