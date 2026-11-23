"""Kraken public market-data adapter (crypto spot).

Uses the unauthenticated `/0/public/OHLC` and `/0/public/Ticker` endpoints, so no
API key is required and no trading permission exists on this path. Prices are
already USD-denominated; no scaling is applied.

Kraken quirks handled here:
* OHLC responses are keyed by Kraken's canonical pair (e.g. ``XXBTZUSD``), not the
  requested pair, so results are read positionally from the result object.
* The final bar is usually still forming — it is marked ``is_final=False`` so
  nothing downstream mistakes a partial candle for a closed one.
* Bar count is capped by the endpoint, so pagination is the caller's concern.
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

KRAKEN_INTERVALS: dict[Timeframe, int] = {
    Timeframe.M1: 1,
    Timeframe.M5: 5,
    Timeframe.M15: 15,
    Timeframe.M30: 30,
    Timeframe.H1: 60,
    Timeframe.H4: 240,
    Timeframe.D1: 1440,
    Timeframe.W1: 10080,
}

BASE_URL = "https://api.kraken.com/0/public"


class KrakenProvider(MarketDataProvider):
    name = "kraken"
    display_name = "Kraken"
    supported_asset_classes = ("crypto",)
    supported_timeframes = tuple(KRAKEN_INTERVALS)
    max_bars_per_request = 720

    def __init__(self, client: httpx.Client | None = None, timeout: float = 10.0) -> None:
        self._client = client
        self._timeout = timeout

    def _get(self, path: str, params: dict[str, object]) -> dict:
        try:
            if self._client is not None:
                response = self._client.get(f"{BASE_URL}{path}", params=params)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(f"{BASE_URL}{path}", params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}", retryable=True) from exc

        if response.status_code == 429:
            raise ProviderError(self.name, "rate limited by Kraken", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"HTTP {response.status_code} from Kraken", retryable=True
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(self.name, "malformed JSON from Kraken") from exc

        if payload.get("error"):
            raise ProviderError(self.name, "; ".join(map(str, payload["error"])))
        return payload.get("result", {})

    def fetch_ohlc(
        self,
        provider_symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[NormalisedBar]:
        interval = KRAKEN_INTERVALS.get(timeframe)
        if interval is None:
            raise ProviderError(self.name, f"unsupported timeframe {timeframe.value}")

        params: dict[str, object] = {"pair": provider_symbol, "interval": interval}
        if since is not None:
            params["since"] = int(since.timestamp())

        result = self._get("/OHLC", params)
        rows = next((value for key, value in result.items() if key != "last"), [])
        if not rows:
            raise ProviderError(self.name, f"no data returned for {provider_symbol}")

        bars: list[NormalisedBar] = []
        for row in rows[-limit:]:
            # [time, open, high, low, close, vwap, volume, count]
            bars.append(
                NormalisedBar(
                    open_time=self._utc(int(row[0])),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    vwap=float(row[5]) if row[5] else None,
                    volume=float(row[6]) if row[6] else 0.0,
                    trades=int(row[7]) if len(row) > 7 and row[7] else None,
                    is_final=False,
                )
            )
        # The last bar may still be forming; everything before it is closed.
        for bar in bars[:-1]:
            bar.is_final = True
        return bars

    def fetch_quote(self, provider_symbol: str) -> NormalisedQuote:
        result = self._get("/Ticker", {"pair": provider_symbol})
        payload = next((value for key, value in result.items() if key != "last"), None)
        if not payload:
            raise ProviderError(self.name, f"no ticker for {provider_symbol}")

        last = float(payload["c"][0])
        # Kraken's `c` array carries [last_trade_price, last_trade_lot_volume] —
        # the second element is *not* a timestamp. Use receipt time as the
        # observation time; the reply is realtime so this is accurate to seconds.
        observed = datetime.now(UTC)
        open_today = float(payload["o"])
        return NormalisedQuote(
            last=last,
            bid=float(payload["b"][0]),
            ask=float(payload["a"][0]),
            open_24h=open_today,
            change_24h_pct=((last - open_today) / open_today * 100) if open_today else None,
            volume_24h=float(payload["v"][1]) if payload.get("v") else None,
            observed_at=observed,
        )

    def health(self) -> tuple[bool, str]:
        try:
            self._get("/Time", {})
            return True, "reachable"
        except ProviderError as exc:
            return False, exc.message
