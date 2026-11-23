"""Provider adapter parsing.

Every adapter is exercised against a recorded payload shape with no network
access, so a schema drift in a third party is caught as a test failure rather
than as silently wrong prices in the database.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.integrations.providers.base import ProviderError
from app.integrations.providers.frankfurter import FrankfurterProvider
from app.integrations.providers.kraken import KrakenProvider
from app.integrations.providers.twelvedata import TwelveDataProvider
from app.models.market import Timeframe


class FakeResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeClient:
    def __init__(self, payload, status_code: int = 200) -> None:
        self.response = FakeResponse(payload, status_code)
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, params or {}))
        return self.response


# --------------------------------------------------------------------- Kraken

KRAKEN_OHLC = {
    "error": [],
    "result": {
        "XXBTZUSD": [
            [1700000000, "100.0", "101.0", "99.0", "100.5", "100.5", "12.5", 42],
            [1700003600, "100.5", "102.0", "100.0", "101.5", "101.5", "9.0", 30],
        ],
        "last": 1700003600,
    },
}


def test_kraken_parses_bars_and_marks_the_last_one_forming() -> None:
    bars = KrakenProvider(client=FakeClient(KRAKEN_OHLC)).fetch_ohlc(
        "XBTUSD", Timeframe.H1, limit=10
    )
    assert len(bars) == 2
    assert bars[0].open_time == datetime.fromtimestamp(1700000000, UTC)
    assert bars[0].close == pytest.approx(100.5)
    assert bars[0].volume == pytest.approx(12.5)
    assert bars[0].is_final is True
    assert bars[-1].is_final is False  # last bar is still forming


def test_kraken_surfaces_api_errors() -> None:
    with pytest.raises(ProviderError) as exc:
        KrakenProvider(client=FakeClient({"error": ["EQuery:Unknown asset pair"]})).fetch_ohlc(
            "NOPE", Timeframe.H1, limit=5
        )
    assert "Unknown asset pair" in exc.value.message


# ---------------------------------------------------------------- Twelve Data

TD_SERIES = {
    "meta": {"symbol": "AAPL", "interval": "1day"},
    "values": [
        {"datetime": "2026-11-21", "open": "100.0", "high": "102.0", "low": "99.0",
         "close": "101.0", "volume": "1000"},
        {"datetime": "2026-11-20", "open": "99.0", "high": "101.0", "low": "98.0",
         "close": "100.0", "volume": "900"},
    ],
    "status": "ok",
}


def test_twelvedata_returns_bars_oldest_first() -> None:
    bars = TwelveDataProvider(api_key="test", client=FakeClient(TD_SERIES)).fetch_ohlc(
        "AAPL", Timeframe.D1, limit=10
    )
    assert [bar.open_time.date().isoformat() for bar in bars] == ["2026-11-20", "2026-11-21"]
    assert bars[-1].close == pytest.approx(101.0)
    assert bars[0].open_time.tzinfo is not None


def test_twelvedata_applies_pence_scale() -> None:
    bars = TwelveDataProvider(api_key="test", client=FakeClient(TD_SERIES)).fetch_ohlc(
        "SHEL.L", Timeframe.D1, limit=10, price_scale=0.01
    )
    assert bars[-1].close == pytest.approx(1.01)


def test_twelvedata_treats_200_with_error_body_as_failure() -> None:
    """The API reports bad keys with HTTP 200; that must not look like success."""
    payload = {"code": 401, "message": "apikey is invalid", "status": "error"}
    provider = TwelveDataProvider(api_key="bad", client=FakeClient(payload))
    with pytest.raises(ProviderError) as exc:
        provider.fetch_quote("AAPL")
    assert "apikey" in exc.value.message


def test_twelvedata_rate_limit_is_retryable() -> None:
    provider = TwelveDataProvider(api_key="test", client=FakeClient({}, status_code=429))
    with pytest.raises(ProviderError) as exc:
        provider.fetch_ohlc("AAPL", Timeframe.D1, limit=5)
    assert exc.value.retryable is True


def test_twelvedata_skips_malformed_rows_instead_of_guessing() -> None:
    payload = {
        "values": [
            {"datetime": "2026-11-20", "open": "1", "high": "2", "low": "1", "close": "2"},
            {"datetime": "2026-11-21", "open": None, "high": None, "low": None, "close": None},
        ],
        "status": "ok",
    }
    bars = TwelveDataProvider(api_key="test", client=FakeClient(payload)).fetch_ohlc(
        "AAPL", Timeframe.D1, limit=10
    )
    assert len(bars) == 1


def test_twelvedata_rejects_unsupported_timeframe() -> None:
    provider = TwelveDataProvider(api_key="test", client=FakeClient(TD_SERIES))
    with pytest.raises(ProviderError) as exc:
        provider.fetch_ohlc("AAPL", None, limit=5)  # type: ignore[arg-type]
    assert "unsupported timeframe" in exc.value.message


# --------------------------------------------------------------- Frankfurter

FRANKFURTER_SERIES = {
    "base": "GBP",
    "start_date": "2026-11-18",
    "end_date": "2026-11-21",
    "rates": {
        "2026-11-18": {"USD": 1.3050},
        "2026-11-19": {"USD": 1.3070},
        "2026-11-20": {"USD": 1.3074},
    },
}


def test_frankfurter_builds_flat_ohlc_bars() -> None:
    """ECB publishes one reference rate per day, so OHLC is legitimately flat."""
    bars = FrankfurterProvider(client=FakeClient(FRANKFURTER_SERIES)).fetch_ohlc(
        "GBPUSD", Timeframe.D1, limit=10
    )
    assert len(bars) == 3
    bar = bars[0]
    assert bar.open == bar.high == bar.low == bar.close == pytest.approx(1.3050)
    assert bar.volume == 0  # no volume available — recorded as zero, not invented
    assert bar.is_final is True


def test_frankfurter_refuses_intraday_instead_of_faking_it() -> None:
    provider = FrankfurterProvider(client=FakeClient(FRANKFURTER_SERIES))
    assert provider.supports(Timeframe.H1) is False
    assert provider.supports(Timeframe.D1) is True
    with pytest.raises(ProviderError) as exc:
        provider.fetch_ohlc("GBPUSD", Timeframe.H1, limit=5)
    assert "daily only" in exc.value.message.lower()


def test_frankfurter_quote_uses_the_latest_reference_rate() -> None:
    quote = FrankfurterProvider(
        client=FakeClient({"amount": 1.0, "base": "GBP", "date": "2026-11-20",
                           "rates": {"USD": 1.3074}})
    ).fetch_quote("GBPUSD")
    assert quote.last == pytest.approx(1.3074)
    assert quote.observed_at == datetime(2026, 11, 20, 15, tzinfo=UTC)
