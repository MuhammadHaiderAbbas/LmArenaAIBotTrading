"""Analytics API over HTTP: shape, refusals, ownership, and the honesty flags."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.market import Instrument, MarketCandle, Timeframe, Venue


def seed_symbol(db, symbol: str = "AAPI/T", price: float = 100.0, bars: int = 60) -> Instrument:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="AAPI", name="Analytics API venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="AAP",
            quote_currency="T",
            is_tradable=True,
            status="active",
        )
        db.add(instrument)
        db.flush()
    newest = datetime.now(UTC)
    for index in range(bars):
        when = newest - timedelta(days=bars - 1 - index)
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=when,
                close_time=when + timedelta(hours=23),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.commit()
    return instrument


def test_analytics_requires_authentication(client) -> None:
    assert client.get("/api/v1/analytics/performance").status_code == 401
    assert client.get("/api/v1/analytics/trades").status_code == 401
    assert client.get("/api/v1/analytics/equity-curve").status_code == 401


def test_a_fresh_account_reports_nothing_rather_than_zeros(client, auth_headers) -> None:
    headers = auth_headers("anafresh@aurex.ai")
    response = client.get("/api/v1/analytics/performance", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["trades"] == []
    assert payload["starting_equity"] > 0
    by_key = {metric["key"]: metric for metric in payload["metrics"]}
    assert by_key["sharpe"]["value"] is None
    assert by_key["win_rate"]["value"] is None
    assert by_key["sharpe"]["note"], "a withheld metric must say why"
    assert payload["notes"]


def test_the_report_declares_it_is_not_forward_looking(client, auth_headers) -> None:
    """A contract flag, so no client can present this as a forecast."""
    headers = auth_headers("anaflag@aurex.ai")
    payload = client.get("/api/v1/analytics/performance", headers=headers).json()
    assert payload["forward_looking"] is False


def test_a_round_trip_appears_in_the_report(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "AAPI/RT")
    finally:
        db.close()

    headers = auth_headers("anart@aurex.ai")
    bought = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "AAPI/RT", "side": "buy", "quantity": 10},
        headers=headers,
    )
    assert bought.status_code == 201 and bought.json()["status"] == "filled"
    sold = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "AAPI/RT", "side": "sell", "quantity": 10},
        headers=headers,
    )
    assert sold.status_code == 201 and sold.json()["status"] == "filled"

    payload = client.get("/api/v1/analytics/performance", headers=headers).json()
    assert len(payload["trades"]) == 1
    trade = payload["trades"][0]
    assert trade["symbol"] == "AAPI/RT"
    assert set(trade) >= {"entry_price", "exit_price", "realised_pnl", "holding_days", "return_pct"}
    assert payload["costs"]["commission"] > 0
    assert payload["attribution"]["by_symbol"]["AAPI/RT"] is not None

    trades = client.get("/api/v1/analytics/trades", headers=headers).json()
    assert len(trades) == 1

    curve = client.get("/api/v1/analytics/equity-curve", headers=headers).json()
    assert curve
    assert all("equity" in point and "at" in point for point in curve)


def test_analytics_are_scoped_to_their_owner(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "AAPI/OWN")
    finally:
        db.close()

    owner = auth_headers("anaowner@aurex.ai")
    client.post(
        "/api/v1/execution/orders",
        json={"symbol": "AAPI/OWN", "side": "buy", "quantity": 1},
        headers=owner,
    )
    intruder = auth_headers("anaintruder@aurex.ai")
    payload = client.get("/api/v1/analytics/performance", headers=intruder).json()
    assert payload["trades"] == []
    assert payload["costs"]["fills"] == 0


def test_trade_limit_is_applied(client, auth_headers) -> None:
    headers = auth_headers("analimit@aurex.ai")
    response = client.get("/api/v1/analytics/performance?trade_limit=1", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["trades"]) <= 1
    assert client.get("/api/v1/analytics/performance?trade_limit=0", headers=headers).status_code == 422


def test_every_metric_carries_a_definition_over_http(client, auth_headers) -> None:
    headers = auth_headers("anadefs@aurex.ai")
    payload = client.get("/api/v1/analytics/performance", headers=headers).json()
    for metric in payload["metrics"]:
        assert metric["definition"], f"{metric['key']} has no definition"
        assert metric["unit"] in {"pct", "ratio", ""}
