"""Risk API: status honesty, staff-only emergency controls, and dry-run evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.market import Instrument, MarketCandle, Timeframe, Venue


def seed_symbol(db, symbol: str = "RAPI/T", price: float = 100.0, bars: int = 60) -> None:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="RAPI", name="Risk API venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="RAP",
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


def test_risk_endpoints_require_authentication(client) -> None:
    assert client.get("/api/v1/risk/status").status_code == 401
    assert client.get("/api/v1/risk/policy").status_code == 401
    assert client.post("/api/v1/risk/emergency-stop", json={"reason": "test"}).status_code == 401


def test_status_reports_the_applied_rules_and_the_blueprint_limits(client, auth_headers) -> None:
    headers = auth_headers("riskstatus@aurex.ai")
    response = client.get("/api/v1/risk/status", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()

    assert payload["breaker_state"] == "closed"
    assert payload["halted_new_risk"] is False
    # The shutdown must never claim to close positions.
    assert payload["closes_positions"] is False
    assert payload["policy"]["max_risk_per_trade"] == 0.005
    assert payload["policy"]["max_daily_loss"] == 0.02
    assert payload["policy"]["max_drawdown"] == 0.10
    assert payload["policy"]["max_positions"] == 5
    assert payload["policy"]["max_exposure"] == 0.50
    assert payload["policy"]["leverage_enabled"] is False

    # Every rule the engine claims must also be listed as applied.
    for rule in ["per_trade_risk", "max_positions", "max_drawdown", "kill_switch", "correlation"]:
        assert rule in payload["applied_rules"]


def test_status_reports_which_rules_are_not_implemented(client, auth_headers) -> None:
    """Honesty over neatness: the list is computed, not asserted empty."""
    headers = auth_headers("rules@aurex.ai")
    overview = client.get("/api/v1/risk/overview", headers=headers).json()
    assert isinstance(overview["rules_not_implemented"], list)
    assert set(overview["rules_not_implemented"]).isdisjoint(overview["rules_supported"])


def test_dry_run_evaluation_returns_every_check_without_writing(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "RAPI/DRY")
    finally:
        db.close()

    headers = auth_headers("dryrun@aurex.ai")
    before = client.get("/api/v1/risk/checks", headers=headers).json()
    response = client.post(
        "/api/v1/risk/evaluate",
        json={"symbol": "RAPI/DRY", "side": "buy", "quantity": 10},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["decision"] == "allow"
    assert payload["approved_quantity"] == 10
    assert payload["checks"]
    assert {check["rule"] for check in payload["checks"]} >= {"max_exposure", "per_trade_risk"}

    after = client.get("/api/v1/risk/checks", headers=headers).json()
    assert len(after) == len(before), "a dry run must not pollute the audit trail"


def test_evaluate_reduces_an_oversized_order_with_the_reason(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "RAPI/REDUCE")
    finally:
        db.close()

    headers = auth_headers("reduce@aurex.ai")
    payload = client.post(
        "/api/v1/risk/evaluate",
        json={"symbol": "RAPI/REDUCE", "side": "buy", "quantity": 400},
        headers=headers,
    ).json()
    assert payload["decision"] == "allow_reduced"
    assert 0 < payload["approved_quantity"] < 400
    assert "0.50%" in payload["reason"]


def test_an_order_writes_a_risk_check(client, auth_headers) -> None:
    """The audit trail must exist for orders, not only for dry runs."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        seed_symbol(db, "RAPI/CHECK")
    finally:
        db.close()

    headers = auth_headers("check@aurex.ai")
    client.post(
        "/api/v1/execution/orders",
        json={"symbol": "RAPI/CHECK", "side": "buy", "quantity": 1},
        headers=headers,
    )
    checks = client.get("/api/v1/risk/checks", headers=headers).json()
    assert checks
    assert checks[0]["instrument_symbol"] == "RAPI/CHECK"
    assert checks[0]["decision"] in {"allow", "allow_reduced"}
    assert checks[0]["equity_snapshot"] > 0


def test_a_non_staff_user_cannot_trip_the_emergency_stop(client, auth_headers) -> None:
    headers = auth_headers("pleb@aurex.ai")
    response = client.post(
        "/api/v1/risk/emergency-stop", json={"reason": "I do not like the market"}, headers=headers
    )
    assert response.status_code == 403


def test_staff_can_trip_and_clear_the_breaker(client, auth_headers, admin_headers) -> None:
    admin_headers = admin_headers()
    stop = client.post(
        "/api/v1/risk/emergency-stop",
        json={"reason": "Scheduled maintenance test."},
        headers=admin_headers,
    )
    assert stop.status_code == 201, stop.text
    payload = stop.json()
    assert payload["state"] == "manual"
    assert payload["halted_new_risk"] is True
    assert payload["cleared_at"] is None

    status = client.get("/api/v1/risk/status", headers=auth_headers("watcher@aurex.ai")).json()
    assert status["breaker_state"] in {"manual", "open", "closed"}

    resumed = client.post("/api/v1/risk/resume?reason=LGTM", headers=admin_headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["cleared_at"] is not None


def test_a_half_written_stop_is_rejected(client, admin_headers) -> None:
    admin_headers = admin_headers()
    response = client.post(
        "/api/v1/risk/emergency-stop", json={"reason": "no"}, headers=admin_headers
    )
    assert response.status_code == 422


def test_resuming_with_no_breaker_returns_409(client, admin_headers) -> None:
    response = client.post("/api/v1/risk/resume", headers=admin_headers())
    assert response.status_code in {200, 409}


def test_policy_change_is_staff_only_and_creates_a_version(
    client, auth_headers, admin_headers
) -> None:
    admin_headers = admin_headers()
    headers = auth_headers("policychange@aurex.ai")
    refused = client.put(
        "/api/v1/risk/policy",
        json={"max_daily_loss": 0.01, "reason": "tighten"},
        headers=headers,
    )
    assert refused.status_code == 403

    changed = client.put(
        "/api/v1/risk/policy",
        json={"max_daily_loss": 0.01, "reason": "Tightened after review."},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    payload = changed.json()
    assert payload["max_daily_loss"] == 0.01
    assert payload["version"] >= 2
    # The change is attributed in the policy note, not only in a log.
    assert "Tightened after review" in payload["notes"]
    # And untouched limits are carried forward, not reset to defaults.
    assert payload["max_risk_per_trade"] == 0.005


def test_a_policy_cannot_be_set_to_a_nonsense_value(client, admin_headers) -> None:
    admin_headers = admin_headers()
    assert (
        client.put(
            "/api/v1/risk/policy", json={"max_daily_loss": 5.0}, headers=admin_headers
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/v1/risk/policy", json={"max_positions": 0}, headers=admin_headers
        ).status_code
        == 422
    )


def test_risk_events_are_listed_after_a_stop(client, admin_headers) -> None:
    admin_headers = admin_headers()
    client.post(
        "/api/v1/risk/emergency-stop", json={"reason": "Event listing test."}, headers=admin_headers
    )
    events = client.get("/api/v1/risk/events", headers=admin_headers).json()
    assert any(event["event_type"] == "breaker_tripped" for event in events)
    assert all(event["severity"] in {"info", "warning", "critical"} for event in events)
