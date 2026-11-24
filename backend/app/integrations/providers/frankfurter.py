"""Frankfurter (ECB reference rate) adapter — daily FX, no API key.

Frankfurter republishes European Central Bank reference rates. It is a *fallback*
for forex when a richer provider is unavailable, and it is honest about its
limits, which are enforced here rather than papered over:

* **Daily only.** ECB publishes one fixing per working day, so intraday
  timeframes are rejected outright instead of being fabricated from a daily bar.
* **No volume.** FX reference rates have no traded volume; bars carry volume 0
  and the quality checker treats that as expected rather than a gap.
* **No OHLC.** Only a fixing exists, so open = high = low = close. Spread and
  range are not observable from this source and strategies requiring them must
  use a different provider.

Every bar is therefore marked ``is_final=True`` for the previous working day and
``is_final=False`` for the current one.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx

from app.integrations.providers.base import (
    MarketDataProvider,
    NormalisedBar,
    NormalisedQuote,
    ProviderError,
)
from app.models.market import Timeframe

BASE_URL = "https://api.frankfurter.dev/v1"

# Frankfurter only quotes against a base currency. Cross rates are derived by
# triangulating through the euro when a direct quote is unavailable.
TRIANGULATE = "EUR"


class FrankfurterProvider(MarketDataProvider):
    name = "frankfurter"
    display_name = "ECB reference rates (Frankfurter)"
    supported_asset_classes = ("forex",)
    supported_timeframes = (Timeframe.D1,)
    max_bars_per_request = 400

    def __init__(self, client: httpx.Client | None = None, timeout: float = 12.0) -> None:
        self._client = client
        self._timeout = timeout

    # -- plumbing ---------------------------------------------------------
    def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        try:
            if self._client is not None:
                response = self._client.get(f"{BASE_URL}{path}", params=params or {})
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(f"{BASE_URL}{path}", params=params or {})
        except httpx.HTTPError as exc:
            raise ProviderError(self.name, f"network error: {exc}", retryable=True) from exc

        if response.status_code == 429:
            raise ProviderError(self.name, "rate limited", retryable=True)
        if response.status_code >= 400:
            raise ProviderError(
                self.name, f"HTTP {response.status_code} from Frankfurter", retryable=True
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError(self.name, "malformed JSON", retryable=True) from exc

    @staticmethod
    def _split(provider_symbol: str) -> tuple[str, str]:
        """`GBPUSD` or `GBP/USD` -> ('GBP', 'USD')."""
        cleaned = provider_symbol.replace("/", "").replace("=X", "").upper()
        if len(cleaned) != 6:
            raise ProviderError("frankfurter", f"cannot parse FX pair '{provider_symbol}'")
        return cleaned[:3], cleaned[3:]

    # -- protocol ---------------------------------------------------------
    def fetch_ohlc(
        self,
        provider_symbol: str,
        timeframe: Timeframe,
        limit: int = 200,
        since: datetime | None = None,
    ) -> list[NormalisedBar]:
        if timeframe not in self.supported_timeframes:
            raise ProviderError(
                self.name,
                f"ECB reference rates are daily only; {timeframe.value} is not available",
            )
        base, quote = self._split(provider_symbol)

        start = (since.date() if since else date.today() - timedelta(days=limit * 2)).isoformat()
        end = date.today().isoformat()

        payload = self._get(f"/{start}..{end}", {"base": base, "symbols": quote})
        rates = payload.get("rates") or {}
        if not rates:
            raise ProviderError(self.name, f"no reference rates for {base}/{quote} in the window")

        series: list[tuple[date, float]] = []
        for day, values in sorted(rates.items()):
            value = values.get(quote)
            if value is None:
                continue
            series.append((date.fromisoformat(day), float(value)))

        if not series:
            raise ProviderError(self.name, f"{quote} not quoted against {base}")

        today = date.today()
        bars: list[NormalisedBar] = []
        for day, rate in series[-limit:]:
            bars.append(
                NormalisedBar(
                    open_time=datetime(day.year, day.month, day.day, tzinfo=UTC),
                    open=rate,
                    high=rate,
                    low=rate,
                    close=rate,
                    volume=0.0,
                    is_final=day < today,
                )
            )
        return bars

    def fetch_quote(self, provider_symbol: str) -> NormalisedQuote:
        base, quote = self._split(provider_symbol)
        payload = self._get("/latest", {"base": base, "symbols": quote})
        rate = (payload.get("rates") or {}).get(quote)
        if rate is None:
            # Triangulate: base->EUR->quote
            usd_leg = self._get("/latest", {"base": base, "symbols": TRIANGULATE})
            other_leg = self._get("/latest", {"base": TRIANGULATE, "symbols": quote})
            try:
                rate = float(usd_leg["rates"][TRIANGULATE]) * float(other_leg["rates"][quote])
            except (KeyError, TypeError) as exc:
                raise ProviderError(self.name, f"no rate for {base}/{quote}") from exc

        return NormalisedQuote(
            last=float(rate),
            # The observation time is the ECB fixing date, not the request time.
            # Using "now" would report a Friday rate as seconds old on a Sunday
            # and mask a genuinely dead feed.
            observed_at=_fixing_datetime(payload),
        )

    def health(self) -> tuple[bool, str]:
        try:
            self.fetch_quote("GBPUSD")
            return True, "reachable"
        except ProviderError as exc:
            return False, exc.message


def _fixing_datetime(payload: dict) -> datetime:
    """Date the ECB reference rate was fixed, expressed as 16:00 CET (15:00 UTC).

    The API returns a date, not an instant. Stamping the request time would make
    a Friday fixing look seconds old on a Sunday and hide a dead feed.
    """
    raw = payload.get("date")
    if isinstance(raw, str):
        try:
            return datetime.strptime(raw, "%Y-%m-%d").replace(hour=15, tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)
