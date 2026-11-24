"""Execution API: auth, ownership, fill lifecycle and refusals over HTTP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.market import Instrument, MarketCandle, Timeframe, Venue


def seed_symbol(db, symbol: str = "API/EXE", *, price: float = 100.0, bars: int = 40) -> Instrument:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="APIEX", name="API exec venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="API",
            quote_currency="EXE",
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


def account_id_for(client, headers) -> str:
    response = client.get("/api/v1/accounts", headers=headers)
    assert response.status_code == 200, response.text
    accounts = response.json()
    assert accounts, "the fixture user should have a funded paper account"
    return accounts[0]["id"]


def test_execution_endpoints_require_authentication(client) -> None:
    assert client.get("/api/v1/execution/overview").status_code == 401
    assert client.get("/api/v1/execution/orders").status_code == 401
    assert client.get("/api/v1/execution/positions").status_code == 401
    assert (
        client.post(
            "/api/v1/execution/orders", json={"symbol": "GBP/USD", "side": "buy", "quantity": 1}
        ).status_code
        == 401
    )


def test_overview_starts_empty_and_reconciled(client, auth_headers) -> None:
    headers = auth_headers("overview@aurex.ai")
    response = client.get("/api/v1/execution/overview", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["open_positions"] == 0
    assert payload["orders_total"] == 0
    assert payload["rejection_rate"] == 0.0
    assert payload["reconciliation"]["investment_agrees"] is True


def test_a_buy_fills_and_creates_a_position(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/BUY", price=100.0)
    finally:
        db.close()

    headers = auth_headers("buy@aurex.ai")
    response = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/BUY", "side": "buy", "quantity": 10, "rationale": "test buy"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "filled"
    assert order["filled_quantity"] == 10
    assert order["fills"]
    assert order["fills"][0]["commission"] > 0
    assert order["average_fill_price"] is not None
    # The lifecycle is on the order, not just in the database.
    kinds = [event["event_type"] for event in order["events"]]
    assert "submitted" in kinds
    assert "validated" in kinds
    assert "filled" in kinds

    positions = client.get("/api/v1/execution/positions", headers=headers).json()
    assert len(positions) == 1
    assert positions[0]["instrument_symbol"] == "API/BUY"
    assert positions[0]["quantity"] == 10
    assert positions[0]["unrealised_pnl"] is not None


def test_a_stale_market_refuses_the_order_and_says_so(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        instrument = seed_symbol(db, "API/STALE", price=100.0)
        # Age every *open_time* by pushing each bar back individually; a bulk
        # update would collide on the (instrument, timeframe, open_time) key.
        rows = (
            db.query(MarketCandle)
            .filter(MarketCandle.instrument_id == instrument.id)
            .order_by(MarketCandle.open_time.desc())
            .all()
        )
        for offset, row in enumerate(rows):
            row.open_time = datetime.now(UTC) - timedelta(days=30 + offset)
            row.close_time = row.open_time + timedelta(hours=23)
        db.commit()
    finally:
        db.close()

    headers = auth_headers("stale@aurex.ai")
    response = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/STALE", "side": "buy", "quantity": 1},
        headers=headers,
    )
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "rejected"
    assert "stale" in order["reject_reason"].lower()
    assert order["fills"] == []


def test_buy_then_sell_closes_the_position_and_keeps_the_ledger_balanced(
    client, auth_headers
) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/ROUND", price=100.0)
    finally:
        db.close()

    headers = auth_headers("round@aurex.ai")
    bought = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/ROUND", "side": "buy", "quantity": 5},
        headers=headers,
    ).json()
    assert bought["status"] == "filled"

    sold = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/ROUND", "side": "sell", "quantity": 5},
        headers=headers,
    ).json()
    assert sold["status"] == "filled"

    closed = client.get("/api/v1/execution/positions?status=closed", headers=headers).json()
    assert len(closed) == 1
    assert closed[0]["status"] == "closed"
    assert closed[0]["quantity"] == 0

    overview = client.get("/api/v1/execution/overview", headers=headers).json()
    assert overview["open_positions"] == 0
    assert overview["closed_positions"] == 1
    assert overview["reconciliation"]["investment_agrees"] is True
    assert overview["reconciliation"]["realised_agrees"] is True
    # Two round trips means both sides of commission were paid.
    assert overview["fills_total"] == 2
    assert overview["total_commission"] > 0


def test_an_unaffordable_order_is_rejected_with_the_numbers(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/POOR", price=100.0)
    finally:
        db.close()

    headers = auth_headers("poor@aurex.ai")
    response = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/POOR", "side": "buy", "quantity": 1_000_000},
        headers=headers,
    )
    assert response.status_code == 201
    order = response.json()
    assert order["status"] == "rejected"
    assert "Insufficient cash" in order["reject_reason"]


def test_short_selling_is_refused_over_http(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/SHORT", price=100.0)
    finally:
        db.close()

    headers = auth_headers("short@aurex.ai")
    order = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/SHORT", "side": "sell", "quantity": 1},
        headers=headers,
    ).json()
    assert order["status"] == "rejected"
    assert "borrow" in order["reject_reason"] or "Short" in order["reject_reason"]


def test_duplicate_client_order_id_returns_the_same_order(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/DUPE", price=100.0)
    finally:
        db.close()

    headers = auth_headers("dupe@aurex.ai")
    body = {
        "symbol": "API/DUPE",
        "side": "buy",
        "quantity": 1,
        "client_order_id": "api-dupe-1",
    }
    first = client.post("/api/v1/execution/orders", json=body, headers=headers).json()
    second = client.post("/api/v1/execution/orders", json=body, headers=headers).json()
    assert first["id"] == second["id"]

    positions = client.get("/api/v1/execution/positions", headers=headers).json()
    assert len(positions) == 1
    assert positions[0]["quantity"] == 1


def test_limit_order_rests_and_can_be_cancelled(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/LIMIT", price=100.0)
    finally:
        db.close()

    headers = auth_headers("limit@aurex.ai")
    order = client.post(
        "/api/v1/execution/orders",
        json={
            "symbol": "API/LIMIT",
            "side": "buy",
            "quantity": 5,
            "order_type": "limit",
            "limit_price": 10.0,
        },
        headers=headers,
    ).json()
    assert order["status"] == "submitted"
    assert order["fills"] == []

    cancelled = client.post(f"/api/v1/execution/orders/{order['id']}/cancel", headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"


def test_orders_are_scoped_to_their_owner(client, auth_headers) -> None:
    """One user must not be able to read another's order by guessing an id."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/OWN", price=100.0)
    finally:
        db.close()

    owner = auth_headers("owner@aurex.ai")
    order = client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/OWN", "side": "buy", "quantity": 1},
        headers=owner,
    ).json()

    intruder = auth_headers("intruder@aurex.ai")
    assert (
        client.get(f"/api/v1/execution/orders/{order['id']}", headers=intruder).status_code == 404
    )
    assert client.get("/api/v1/execution/orders", headers=intruder).json() == []


def test_events_are_listed_for_the_account(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/EV", price=100.0)
    finally:
        db.close()

    headers = auth_headers("events@aurex.ai")
    client.post(
        "/api/v1/execution/orders",
        json={"symbol": "API/EV", "side": "buy", "quantity": 2},
        headers=headers,
    )
    events = client.get("/api/v1/execution/events", headers=headers).json()
    assert events
    assert {event["event_type"] for event in events} & {"submitted", "validated", "filled"}


def test_strategy_orders_are_refused_until_the_strategy_is_validated(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "API/STRAT", price=100.0, bars=400)
    finally:
        db.close()

    headers = auth_headers("strat@aurex.ai")
    response = client.post(
        "/api/v1/execution/strategy-orders",
        json={"strategy_code": "trend_ma_v1", "symbol": "API/STRAT"},
        headers=headers,
    )
    # Seed the catalogue so the strategy exists, then retry: still refused because
    # it is not validated.
    client.get("/api/v1/strategies", headers=headers)
    response = client.post(
        "/api/v1/execution/strategy-orders",
        json={"strategy_code": "trend_ma_v1", "symbol": "API/STRAT"},
        headers=headers,
    )
    assert response.status_code == 422
    assert "not validated" in response.json()["detail"]


def test_order_request_validates_its_inputs(client, auth_headers) -> None:
    headers = auth_headers("invalid@aurex.ai")
    assert (
        client.post(
            "/api/v1/execution/orders",
            json={"symbol": "GBP/USD", "side": "buy", "quantity": 0},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/execution/orders",
            json={"symbol": "GBP/USD", "side": "sideways", "quantity": 1},
            headers=headers,
        ).status_code
        == 422
    )
    # A limit order without a price is meaningless.
    assert (
        client.post(
            "/api/v1/execution/orders",
            json={"symbol": "GBP/USD", "side": "buy", "quantity": 1, "limit_price": -5},
            headers=headers,
        ).status_code
        == 422
    )
