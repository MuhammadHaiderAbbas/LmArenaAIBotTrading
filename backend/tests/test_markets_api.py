"""Market-data API behaviour.

Providers are stubbed here so the suite is deterministic and never depends on a
third party being reachable. Live-provider behaviour is exercised separately by
`scripts/verify_market_data.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.database.session import SessionLocal
from app.integrations.providers.base import (
    MarketDataProvider,
    NormalisedBar,
    NormalisedQuote,
    ProviderError,
)
from app.models.market import Timeframe
from app.services.market import ingestion, registry


class StubProvider(MarketDataProvider):
    name = "stub"
    display_name = "Stub provider"
    supported_asset_classes = ("crypto", "equity", "etf", "forex")
    supported_timeframes = tuple(Timeframe)
    max_bars_per_request = 1000

    def __init__(self) -> None:
        self.fail = False
        self.calls = 0
        self.quotes_served = 0

    def fetch_ohlc(self, provider_symbol, timeframe, limit=200, since=None, **kwargs):
        self.calls += 1
        if self.fail:
            raise ProviderError(self.name, "stubbed outage")
        # Fixed epoch: identical calls must return identical bars so the
        # idempotency guarantees can be tested.
        now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
        step = timedelta(seconds=timeframe.seconds)
        bars = []
        for index in range(limit):
            open_time = now - step * (limit - index)
            base = 100 + index * 0.25
            bars.append(
                NormalisedBar(
                    open_time=open_time,
                    open=base,
                    high=base + 1,
                    low=base - 1,
                    close=base + 0.5,
                    volume=100 + index,
                    is_final=index < limit - 1,
                )
            )
        return bars

    def fetch_quote(self, provider_symbol):
        self.quotes_served += 1
        if self.fail:
            raise ProviderError(self.name, "stubbed outage")
        return NormalisedQuote(
            last=123.45,
            bid=123.4,
            ask=123.5,
            open_24h=120.0,
            change_24h_pct=2.875,
            volume_24h=1000.0,
            observed_at=datetime.now(UTC),
        )

    def health(self):
        return (not self.fail, "stubbed")


@pytest.fixture
def stub(monkeypatch) -> StubProvider:
    provider = StubProvider()
    monkeypatch.setitem(ingestion.PROVIDERS, "stub", provider)

    def _register(db=None) -> None:
        session = db or SessionLocal()
        try:
            registry.seed_registry(session)
            from app.models.market import Instrument, InstrumentProvider

            instruments = list(session.query(Instrument).limit(3))
            for instrument in instruments:
                for mapping in list(instrument.providers):
                    session.delete(mapping)
                session.add(
                    InstrumentProvider(
                        instrument_id=instrument.id,
                        provider="stub",
                        provider_symbol=instrument.symbol,
                        priority=0,
                    )
                )
            session.commit()
        finally:
            if db is None:
                session.close()

    _register()
    return provider


def test_registry_is_seeded_and_idempotent(client, admin_headers) -> None:
    headers = admin_headers()
    first = client.post("/api/v1/markets/registry/seed", headers=headers).json()
    second = client.post("/api/v1/markets/registry/seed", headers=headers).json()

    assert first["instruments"] >= 1
    assert second["instruments"] == 0  # re-running creates nothing new

    instruments = client.get("/api/v1/markets/instruments", headers=headers).json()
    assert len(instruments) >= 15
    symbols = {row["symbol"] for row in instruments}
    assert {"BTC/USD", "AAPL", "SHEL.L", "GBP/USD", "SPY"} <= symbols


def test_instruments_are_not_tradable_in_phase_3(client, admin_headers) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    instruments = client.get("/api/v1/markets/instruments", headers=headers).json()
    assert all(row["is_tradable"] is False for row in instruments)


def test_pence_quoted_instruments_carry_a_scale(client, admin_headers) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    shel = client.get("/api/v1/markets/instruments/SHEL.L", headers=headers).json()
    assert shel["quote_currency"] == "GBP"
    assert shel["price_scale"] == pytest.approx(0.01)


def test_markets_require_authentication(client) -> None:
    assert client.get("/api/v1/markets/instruments").status_code == 401
    assert client.get("/api/v1/markets/overview").status_code == 401


def test_sync_is_restricted_to_staff(client, auth_headers, stub) -> None:
    response = client.post(
        "/api/v1/markets/sync", json={"symbols": ["BTC/USD"]}, headers=auth_headers()
    )
    assert response.status_code == 403


def test_sync_writes_candles_and_quotes(client, admin_headers, stub) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)

    response = client.post(
        "/api/v1/markets/sync",
        json={"symbols": ["BTC/USD"], "timeframe": "1h", "limit": 50, "with_quotes": True},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # one candle result + one quote result for the same instrument
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert body["results"][0]["bars_written"] == 50

    series = client.get(
        "/api/v1/markets/BTC/USD/candles?timeframe=1h&limit=50", headers=headers
    ).json()
    assert len(series["candles"]) == 50
    assert series["candles"][0]["open_time"] < series["candles"][-1]["open_time"]
    assert series["candles"][-1]["is_final"] is False
    assert series["indicators"]["sma_20"] is not None
    assert series["session"]["state"] == "always_on"


def test_sync_is_idempotent(client, admin_headers, stub) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    payload = {"symbols": ["BTC/USD"], "timeframe": "1d", "limit": 40, "with_quotes": False}

    first = client.post("/api/v1/markets/sync", json=payload, headers=headers).json()
    second = client.post("/api/v1/markets/sync", json=payload, headers=headers).json()

    assert first["results"][0]["bars_written"] == 40
    # Re-ingesting the same window must not duplicate or overwrite closed bars.
    assert second["results"][0]["bars_written"] <= 1
    assert second["results"][0]["bars_skipped"] >= 39

    series = client.get("/api/v1/markets/BTC/USD/candles?limit=200", headers=headers).json()
    assert len(series["candles"]) == 40


def test_failing_provider_falls_back_to_the_next_one(client, admin_headers, stub) -> None:
    """A stubbed outage must not stop ingestion when a real provider can serve."""
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    stub.fail = True

    body = client.post(
        "/api/v1/markets/sync",
        json={"symbols": ["BTC/USD"], "timeframe": "1d", "limit": 10, "with_quotes": False},
        headers=headers,
    ).json()

    assert body["succeeded"] >= 1  # kraken picked up the request
    assert body["results"][0]["provider"] == "kraken"

    events = client.get("/api/v1/markets/quality/events?symbol=BTC/USD", headers=headers).json()
    assert any(event["event_type"] == "provider_error" for event in events)


def test_total_provider_outage_is_reported_not_swallowed(client, admin_headers, stub) -> None:
    """When no provider can serve, the failure is explicit and auditable."""
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)

    # A dedicated instrument whose only mapping is the failing stub.
    from app.database.session import SessionLocal
    from app.models.market import AssetClass, Instrument, InstrumentProvider, Venue

    session = SessionLocal()
    try:
        venue = session.query(Venue).filter(Venue.code == "KRAKEN").one()
        instrument = Instrument(
            symbol="TEST/USD",
            name="Test instrument",
            asset_class=AssetClass.CRYPTO,
            venue_id=venue.id,
            base_currency="TEST",
            quote_currency="USD",
        )
        session.add(instrument)
        session.flush()
        session.add(
            InstrumentProvider(
                instrument_id=instrument.id,
                provider="stub",
                provider_symbol="TEST/USD",
                priority=0,
            )
        )
        session.commit()
    finally:
        session.close()

    stub.fail = True
    body = client.post(
        "/api/v1/markets/sync",
        json={"symbols": ["TEST/USD"], "timeframe": "1h", "limit": 20},
        headers=headers,
    ).json()

    # candle sync + quote sync both fail with no fallback available
    assert body["failed"] == 2
    assert body["succeeded"] == 0
    assert all("stubbed outage" in row["error"] for row in body["results"])


def test_unknown_symbol_returns_404(client, auth_headers) -> None:
    headers = auth_headers()
    assert client.get("/api/v1/markets/NOPE/candles", headers=headers).status_code == 404
    response = client.post("/api/v1/markets/sync", json={"symbols": ["NOPE"]}, headers=headers)
    assert response.status_code in (400, 403)  # 403 for non-staff before validation


def test_overview_includes_quote_indicators_and_quality(client, admin_headers, stub) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    client.post(
        "/api/v1/markets/sync",
        json={"symbols": ["BTC/USD"], "timeframe": "1d", "limit": 60},
        headers=headers,
    )

    rows = client.get("/api/v1/markets/overview", headers=headers).json()
    assert rows
    btc = next(row for row in rows if row["instrument"]["symbol"] == "BTC/USD")
    assert btc["quote"]["last"] == pytest.approx(123.45)
    assert btc["quote"]["change_24h_pct"] == pytest.approx(2.875)
    assert btc["quote"]["spread_bps"] is not None
    assert btc["indicators"]["rsi_14"] is not None
    assert len(btc["sparkline"]) > 0
    assert btc["data_quality"] in {"ok", "warning", "critical", "no_data"}


def test_provider_health_endpoint(client, auth_headers) -> None:
    health = client.get("/api/v1/markets/providers", headers=auth_headers()).json()
    names = {row["provider"] for row in health}
    assert {"kraken", "yahoo", "frankfurter"} <= names
    for row in health:
        assert isinstance(row["healthy"], bool)
        assert row["timeframes"]


def test_quality_summary_shape(client, admin_headers, stub) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    client.post(
        "/api/v1/markets/sync",
        json={"symbols": ["BTC/USD"], "timeframe": "1h", "limit": 30},
        headers=headers,
    )
    summary = client.get("/api/v1/markets/quality/summary", headers=headers).json()
    assert summary["total_open"] >= 0
    assert isinstance(summary["by_severity"], dict)
    assert isinstance(summary["stale_instruments"], list)


def test_ingestion_runs_are_auditable(client, admin_headers, stub) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    client.post(
        "/api/v1/markets/sync",
        json={"symbols": ["BTC/USD"], "timeframe": "1d", "limit": 25},
        headers=headers,
    )
    runs = client.get("/api/v1/markets/ingestion-runs", headers=headers).json()
    assert runs
    assert runs[0]["provider"] == "stub"
    assert runs[0]["status"] == "success"
    assert runs[0]["finished_at"] is not None


def test_sync_is_rate_limited(client, admin_headers, stub) -> None:
    headers = admin_headers()
    client.post("/api/v1/markets/registry/seed", headers=headers)
    payload = {"symbols": ["BTC/USD"], "timeframe": "1d", "limit": 10, "with_quotes": False}
    statuses = [
        client.post("/api/v1/markets/sync", json=payload, headers=headers).status_code
        for _ in range(8)
    ]
    assert 429 in statuses
