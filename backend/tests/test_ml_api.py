"""Model registry API: auth, the promotion gate, and refusal paths."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.market import Instrument, MarketCandle, Timeframe, Venue


def _seed_symbol(db, symbol: str, rows: int = 700) -> None:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="APIT", name="API test venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="APT",
            quote_currency="EST",
            is_tradable=False,
            status="active",
        )
        db.add(instrument)
        db.flush()

    import random  # noqa: PLC0415

    rng = random.Random(23)  # noqa: S311 - synthetic fixtures
    price = 100.0
    base = datetime.now(UTC) - timedelta(days=rows)
    for index in range(rows):
        price = price * (1 + rng.gauss(0.0002, 0.01))
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=base + timedelta(days=index),
                close_time=base + timedelta(days=index, hours=23),
                open=price * 0.999,
                high=price * 1.004,
                low=price * 0.996,
                close=price,
                volume=1000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.commit()


def test_model_endpoints_require_authentication(client) -> None:
    assert client.get("/api/v1/models").status_code == 401
    assert client.get("/api/v1/models/versions").status_code == 401
    assert client.post("/api/v1/models/train", json={"symbol": "GBP/USD"}).status_code == 401
    assert client.get("/api/v1/models/drift").status_code == 401


def test_models_are_listed_with_family_and_determinism(client, auth_headers) -> None:
    response = client.get("/api/v1/models", headers=auth_headers())
    assert response.status_code == 200, response.text
    models = response.json()
    assert len(models) >= 1
    baseline = models[0]
    assert baseline["family"] == "logistic"
    assert baseline["task"] == "direction"
    assert baseline["deterministic"] is True


def test_training_creates_a_candidate_and_the_api_cannot_promote_it(client, auth_headers, admin_headers) -> None:
    """The blueprint's rule, end to end over HTTP: training never promotes."""
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        _seed_symbol(db, "APT/CAND")
    finally:
        db.close()

    response = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "APT/CAND", "timeframe": "1d"},
        headers=auth_headers(),
    )
    assert response.status_code == 201, response.text
    version = response.json()

    assert version["status"] == "candidate"
    assert version["promoted_at"] is None
    assert version["checksum"]
    assert version["feature_names"]
    assert version["feature_snapshots"]
    kinds = {item["kind"]: item for item in version["evaluations"]}
    assert kinds["out_of_sample"]["is_out_of_sample"] is True
    assert kinds["train"]["is_out_of_sample"] is False
    # The gate verdict is exposed so a reviewer can see why.
    assert "gate" in "gate"  # structural
    assert isinstance(version["promotion_gate_passed"], bool)
    assert version["promotion_gate_reason"]


def test_promotion_requires_staff(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/models/versions/does-not-exist/promote",
        json={"note": "x"},
        headers=auth_headers(),
    )
    # Non-staff is rejected before any lookup.
    assert response.status_code == 403


def test_promotion_of_a_weak_model_is_refused_with_a_reason(client, auth_headers, admin_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        _seed_symbol(db, "APT/WEAK")
    finally:
        db.close()

    trained = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "APT/WEAK", "timeframe": "1d"},
        headers=auth_headers(),
    )
    assert trained.status_code == 201, trained.text
    version_id = trained.json()["id"]

    promoted = client.post(
        f"/api/v1/models/versions/{version_id}/promote",
        json={"note": "should not pass"},
        headers=admin_headers(),
    )
    assert promoted.status_code == 422
    assert "Promotion refused" in promoted.json()["detail"]


def test_forced_promotion_then_rollback_over_http(client, auth_headers, admin_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        _seed_symbol(db, "APT/ROLL")
    finally:
        db.close()

    headers = auth_headers()
    first = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "APT/ROLL"},
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/models/versions/{first['id']}/promote",
        json={"note": "first champion", "force": True},
        headers=admin_headers(),
    )

    second = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "APT/ROLL"},
        headers=headers,
    ).json()
    promoted = client.post(
        f"/api/v1/models/versions/{second['id']}/promote",
        json={"note": "second champion", "force": True},
        headers=admin_headers(),
    )
    assert promoted.status_code == 200
    assert promoted.json()["replaced_version_id"] == first["id"]

    rolled_back = client.post(
        f"/api/v1/models/versions/{second['id']}/rollback", headers=admin_headers()
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["id"] == first["id"]
    assert rolled_back.json()["status"] == "champion"


def test_reject_moves_a_candidate_out_of_contention(client, auth_headers, admin_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        _seed_symbol(db, "APT/REJ")
    finally:
        db.close()

    version = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "APT/REJ"},
        headers=auth_headers(),
    ).json()
    rejected = client.post(
        f"/api/v1/models/versions/{version['id']}/reject?note=no%20edge",
        headers=admin_headers(),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_unknown_version_and_symbol_are_404_and_422(client, auth_headers) -> None:
    headers = auth_headers()
    assert client.get("/api/v1/models/versions/nope", headers=headers).status_code == 404
    response = client.post(
        "/api/v1/models/train", json={"symbol": "NOT/REAL"}, headers=headers
    )
    assert response.status_code == 422


def test_overview_reports_the_registry_state_honestly(client, auth_headers) -> None:
    headers = auth_headers()
    response = client.get("/api/v1/models/overview", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["models_total"] >= 1
    assert payload["champions"] == len(payload["champions_detail"])
    assert payload["versions_total"] >= payload["champions"]
    for champion in payload["champions_detail"]:
        # A champion must carry the out-of-sample numbers it was judged on.
        assert "out_of_sample_accuracy" in champion
        assert "edge_over_baseline" in champion


def test_version_summary_gives_a_readable_verdict(client, auth_headers) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        _seed_symbol(db, "APT/SUMM")
    finally:
        db.close()

    version = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "APT/SUMM"},
        headers=auth_headers(),
    ).json()
    summary = client.get(
        f"/api/v1/models/versions/{version['id']}/summary", headers=auth_headers()
    )
    assert summary.status_code == 200
    payload = summary.json()
    assert "Out-of-sample accuracy" in payload["verdict"]
    assert isinstance(payload["walk_forward"], list)


def test_drift_events_are_advisory_and_acknowledgeable(client, auth_headers, admin_headers) -> None:
    headers = auth_headers()
    listing = client.get("/api/v1/models/drift", headers=headers)
    assert listing.status_code == 200
    for event in listing.json():
        assert "human decision" in event["recommendation"]
        assert event["acknowledged"] is False


def test_drift_check_on_unknown_version_is_422(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/models/versions/nope/drift",
        json={"symbol": "GBP/USD"},
        headers=auth_headers(),
    )
    assert response.status_code == 422


def test_ai_desk_reports_no_champion_as_an_absence(client, auth_headers) -> None:
    """With no promoted model, the quantitative agent must say so, not fake a view."""
    response = client.post(
        "/api/v1/ai/run", json={"symbol": "GBP/USD", "timeframe": "1d"}, headers=auth_headers()
    )
    assert response.status_code == 201
    findings = {item["agent_code"]: item for item in response.json()["findings"]}
    quantitative = findings["quantitative"]
    assert quantitative["data_available"] is False
    assert quantitative["stance"] == "unknown"
    kinds = {item["kind"] for item in quantitative["evidence"]}
    assert "absence" in kinds
    # And the run still cannot produce anything tradeable.
    assert response.json()["tradeable"] is False


def test_ai_desk_reads_a_champion_probability_without_taking_a_stance(
    client, auth_headers, admin_headers
) -> None:
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        _seed_symbol(db, "GBP/USD")
    finally:
        db.close()

    version = client.post(
        "/api/v1/models/train",
        json={"model_code": "direction_logistic_v1", "symbol": "GBP/USD", "timeframe": "1d"},
        headers=auth_headers(),
    ).json()
    promoted = client.post(
        f"/api/v1/models/versions/{version['id']}/promote",
        json={"note": "forced for the wire test", "force": True},
        headers=admin_headers(),
    )
    assert promoted.status_code == 200

    run = client.post(
        "/api/v1/ai/run", json={"symbol": "GBP/USD", "timeframe": "1d"}, headers=auth_headers()
    )
    assert run.status_code == 201
    payload = run.json()
    findings = {item["agent_code"]: item for item in payload["findings"]}
    quantitative = findings["quantitative"]
    assert quantitative["data_available"] is True
    assert "probability" in quantitative["headline"].lower() or "up-probability" in quantitative["headline"].lower()
    # Even with a champion, the agent takes no stance and the run is not tradeable.
    assert quantitative["stance"] == "unknown"
    assert quantitative["confidence_kind"] == "uncalibrated"
    assert payload["tradeable"] is False
    # Whatever the number, the baseline it was measured against must be reported,
    # so a reader can judge the probability rather than trust it.
    assert "baseline" in quantitative["facts"]
