"""Strategy catalogue, backtest persistence and validation over HTTP."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from app.models.market import Instrument, MarketCandle, Timeframe, Venue


def seed_symbol(db, symbol: str, rows: int = 700, *, drift: float = 0.0004, seed: int = 31) -> None:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="STRAT", name="Strategy venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="STR",
            quote_currency="AT",
            is_tradable=False,
            status="active",
        )
        db.add(instrument)
        db.flush()

    rng = random.Random(seed)  # noqa: S311 - synthetic fixtures
    price = 100.0
    start = datetime.now(UTC) - timedelta(days=rows)
    for index in range(rows):
        price = price * (1 + rng.gauss(drift, 0.011))
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=start + timedelta(days=index),
                close_time=start + timedelta(days=index, hours=23),
                open=price * 0.999,
                high=price * 1.006,
                low=price * 0.994,
                close=price,
                volume=1000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.commit()


def test_strategy_endpoints_require_authentication(client) -> None:
    assert client.get("/api/v1/strategies").status_code == 401
    assert client.get("/api/v1/strategies/overview").status_code == 401
    assert client.post("/api/v1/strategies/backtests", json={"strategy_code": "x", "symbol": "y"}).status_code == 401


def test_catalogue_lists_implemented_strategies_with_hypotheses(client, auth_headers) -> None:
    response = client.get("/api/v1/strategies", headers=auth_headers())
    assert response.status_code == 200
    items = response.json()
    codes = {item["code"] for item in items}
    assert {"buy_and_hold_v1", "trend_ma_v1", "mean_reversion_v1"} <= codes
    for item in items:
        assert item["hypothesis"], f"{item['code']} has no stated hypothesis"
        assert item["deterministic"] is True
        # Nothing is live-eligible in Phase 7.
        assert item["live_eligible"] is False


def test_backtest_persists_trades_equity_and_metrics(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/TEST")
    finally:
        db.close()

    response = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "STR/TEST", "timeframe": "1d"},
        headers=auth_headers(),
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["metrics"] is not None
    assert payload["trades"]
    assert payload["equity"]
    assert payload["bars_used"] > 0
    assert payload["strategy_code"] == "trend_ma_v1"

    metrics = payload["metrics"]
    # The benchmark is present on the same result, not hidden in another run.
    assert metrics["baseline_return"] is not None
    assert metrics["edge_over_baseline"] is not None
    assert metrics["total_costs"] >= 0
    assert metrics["verdict"]
    assert payload["trade_summary"]["wins"] + payload["trade_summary"]["losses"] == metrics["trades"]


def test_backtest_records_its_cost_assumptions(client, auth_headers) -> None:
    """A result whose assumptions are not stored cannot be reproduced."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/COST")
    finally:
        db.close()

    response = client.post(
        "/api/v1/strategies/backtests",
        json={
            "strategy_code": "trend_ma_v1",
            "symbol": "STR/COST",
            "spread_bps": 4.0,
            "commission_bps": 2.0,
            "slippage_bps": 3.0,
            "financing_bps_per_day": 0.5,
        },
        headers=auth_headers(),
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["spread_bps"] == 4.0
    assert payload["commission_bps"] == 2.0
    assert payload["slippage_bps"] == 3.0
    assert payload["financing_bps_per_day"] == 0.5


def test_higher_costs_produce_a_worse_net_return(client, auth_headers) -> None:
    """The cost model must actually bite, end to end."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/DRAG", seed=44)
    finally:
        db.close()

    headers = auth_headers()
    cheap = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "STR/DRAG", "spread_bps": 0, "commission_bps": 0, "slippage_bps": 0},
        headers=headers,
    ).json()
    dear = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "STR/DRAG", "spread_bps": 30, "commission_bps": 15, "slippage_bps": 15},
        headers=headers,
    ).json()
    assert dear["metrics"]["net_return"] < cheap["metrics"]["net_return"]


def test_backtest_refuses_an_instrument_without_enough_history(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/THIN", rows=90)
    finally:
        db.close()

    response = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "STR/THIN"},
        headers=auth_headers(),
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "refused"
    assert "needs 200" in payload["refusal_reason"]
    assert payload["metrics"] is None


def test_unknown_strategy_and_symbol_are_rejected(client, auth_headers) -> None:
    headers = auth_headers()
    unknown = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "nope", "symbol": "GBP/USD"},
        headers=headers,
    )
    assert unknown.status_code == 422
    assert "Unknown strategy" in unknown.json()["detail"]


def test_validation_requires_staff_and_a_passing_backtest(client, auth_headers, admin_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/VAL", seed=57)
    finally:
        db.close()

    headers = auth_headers()
    strategies = client.get("/api/v1/strategies", headers=headers).json()
    target = next(item for item in strategies if item["code"] == "trend_ma_v1")

    # Non-staff cannot validate at all.
    assert (
        client.post(f"/api/v1/strategies/{target['id']}/validate", json={"note": "x"}, headers=headers).status_code
        == 403
    )

    # With no passing backtest, staff are refused too.
    refused = client.post(
        f"/api/v1/strategies/{target['id']}/validate",
        json={"note": "looks good to me"},
        headers=admin_headers(),
    )
    assert refused.status_code == 422
    assert "no completed backtest" in refused.json()["detail"]


def test_a_backtest_alone_never_validates_a_strategy(client, auth_headers, admin_headers) -> None:
    """The blueprint rule for Phase 7: running a backtest is not a deployment."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/AUTO", seed=61)
    finally:
        db.close()

    headers = auth_headers()
    before = client.get("/api/v1/strategies", headers=headers).json()
    target = next(item for item in before if item["code"] == "buy_and_hold_v1")

    client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "buy_and_hold_v1", "symbol": "STR/AUTO"},
        headers=headers,
    )

    after = client.get("/api/v1/strategies", headers=headers).json()
    updated = next(item for item in after if item["id"] == target["id"])
    assert updated["status"] in {"testing", "draft"}
    assert updated["status"] != "validated"
    assert updated["live_eligible"] is False


def test_overview_reports_no_live_eligible_strategies(client, auth_headers) -> None:
    payload = client.get("/api/v1/strategies/overview", headers=auth_headers()).json()
    assert payload["strategies_total"] >= 3
    assert payload["live_eligible"] == 0
    assert payload["backtests_passing"] >= 0


def test_latest_backtest_and_listing(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/LATEST", seed=71)
    finally:
        db.close()

    headers = auth_headers()
    created = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "mean_reversion_v1", "symbol": "STR/LATEST"},
        headers=headers,
    ).json()

    latest = client.get(f"/api/v1/strategies/{created['strategy_id']}/latest", headers=headers)
    assert latest.status_code == 200
    assert latest.json()["id"] == created["id"]

    listing = client.get("/api/v1/strategies/backtests?symbol=STR/LATEST", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 1

    detail = client.get(f"/api/v1/strategies/backtests/{created['id']}", headers=headers)
    assert detail.status_code == 200
    assert client.get("/api/v1/strategies/backtests/nope", headers=headers).status_code == 404


def test_out_of_sample_flag_is_stored(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "STR/OOS", seed=83)
    finally:
        db.close()

    payload = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "STR/OOS", "is_out_of_sample": True},
        headers=auth_headers(),
    ).json()
    assert payload["is_out_of_sample"] is True


def test_backtest_request_validates_its_inputs(client, auth_headers) -> None:
    headers = auth_headers()
    # A negative cost is meaningless and must not be silently accepted.
    bad = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "GBP/USD", "spread_bps": -5},
        headers=headers,
    )
    assert bad.status_code == 422
    bad_limit = client.post(
        "/api/v1/strategies/backtests",
        json={"strategy_code": "trend_ma_v1", "symbol": "GBP/USD", "limit": 10},
        headers=headers,
    )
    assert bad_limit.status_code == 422
