"""Phase 12 over HTTP: sandbox connections, compliance and the release gate."""

from __future__ import annotations

import pytest

BASE = "/api/v1"


def connect(client, headers, **overrides) -> dict:
    payload = {
        "broker": "simulated",
        "api_key": "test-key-abc",
        "api_secret": "test-secret-xyz",
        **overrides,
    }
    response = client.post(f"{BASE}/brokers/connect", headers=headers, json=payload)
    return {"status": response.status_code, "body": response.json() if response.content else {}}


# ------------------------------------------------------------------ brokers


def test_the_broker_catalogue_states_no_live_support(client, auth_headers) -> None:
    body = client.get(f"{BASE}/brokers", headers=auth_headers()).json()
    assert body["brokers"]
    assert all(not broker["live_supported"] for broker in body["brokers"])
    assert body["live_trading"]["reachable"] is False


def test_connecting_over_http_works_in_sandbox(client, auth_headers) -> None:
    result = connect(client, auth_headers())
    assert result["status"] == 200, result
    assert result["body"]["status"] == "connected"
    assert result["body"]["has_credentials"] is True
    assert result["body"]["mode"] == "sandbox"


def test_a_live_connection_is_refused_over_http(client, auth_headers) -> None:
    """The headline refusal of the whole phase."""
    result = connect(client, auth_headers(), mode="live")
    assert result["status"] == 403
    assert "Live broker execution is not implemented" in result["body"]["detail"]


def test_a_refused_live_connection_stores_nothing(client, auth_headers) -> None:
    headers = auth_headers()
    connect(client, headers, mode="live")
    assert client.get(f"{BASE}/brokers/connections", headers=headers).json()["connections"] == []


def test_connecting_twice_updates_one_row(client, auth_headers) -> None:
    headers = auth_headers()
    connect(client, headers)
    connect(client, headers, label="second")
    assert len(client.get(f"{BASE}/brokers/connections", headers=headers).json()["connections"]) == 1


def test_withdrawal_permission_is_recorded(client, auth_headers) -> None:
    body = connect(client, auth_headers(), withdraw_allowed=True)["body"]
    assert body["withdraw_allowed"] is True


def test_the_handshake_is_logged_with_latency_and_skew(client, auth_headers) -> None:
    body = connect(client, auth_headers())["body"]
    stages = {session["stage"] for session in body["sessions"]}
    assert "handshake" in stages
    assert "clock_probe" in stages
    probe_row = next(s for s in body["sessions"] if s["stage"] == "clock_probe")
    assert probe_row["clock_skew_ms"] is not None


def test_a_probe_can_be_repeated(client, auth_headers) -> None:
    headers = auth_headers()
    connect(client, headers)
    response = client.post(f"{BASE}/brokers/simulated/probe", headers=headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_disconnecting_clears_the_credentials(client, auth_headers) -> None:
    """A link that is off must not still hold a key."""
    headers = auth_headers()
    connect(client, headers)
    body = client.post(f"{BASE}/brokers/simulated/disconnect", headers=headers).json()
    assert body["status"] == "disconnected"
    assert body["has_credentials"] is False


def test_the_stored_secret_is_encrypted(client, auth_headers) -> None:
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models.broker import BrokerConnection

    connect(client, auth_headers())
    db = SessionLocal()
    try:
        connection = db.scalar(select(BrokerConnection))
        stored = connection.api_secret_encrypted
    finally:
        db.close()
    assert stored is not None
    assert "test-secret-xyz" not in stored
    assert stored.startswith("v1.")


def test_probing_an_unknown_broker_is_404(client, auth_headers) -> None:
    assert client.post(f"{BASE}/brokers/nope/probe", headers=auth_headers()).status_code == 404


# --------------------------------------------------------------- sandbox orders


def test_a_sandbox_order_is_accepted_and_does_not_fill(client, auth_headers) -> None:
    headers = auth_headers()
    connect(client, headers)
    body = client.post(
        f"{BASE}/brokers/sandbox-orders",
        headers=headers,
        json={"broker": "simulated", "client_order_id": "c-1", "symbol": "GBP/USD", "side": "buy", "quantity": 10},
    ).json()
    assert body["accepted"] is True
    assert body["filled"] is False
    assert body["money_moved"] is False


def test_a_sandbox_order_needs_a_connection_first(client, auth_headers) -> None:
    response = client.post(
        f"{BASE}/brokers/sandbox-orders",
        headers=auth_headers(),
        json={"broker": "simulated", "client_order_id": "c-2", "symbol": "GBP/USD", "side": "buy", "quantity": 1},
    )
    assert response.status_code == 409


def test_a_sandbox_order_does_not_create_a_paper_order(client, auth_headers) -> None:
    """The sandbox is not a back door into the execution engine."""
    headers = auth_headers()
    connect(client, headers)
    before = client.get(f"{BASE}/execution/orders", headers=headers).json()
    client.post(
        f"{BASE}/brokers/sandbox-orders",
        headers=headers,
        json={"broker": "simulated", "client_order_id": "c-3", "symbol": "GBP/USD", "side": "buy", "quantity": 5},
    )
    after = client.get(f"{BASE}/execution/orders", headers=headers).json()
    assert len(after) == len(before)


# ------------------------------------------------------------------ compliance


def test_the_assessment_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{BASE}/readiness/compliance", headers=auth_headers()).status_code == 403


def test_the_assessment_is_not_live_ready(client, admin_headers) -> None:
    """The honest headline. If this ever passes, the docs must change too."""
    body = client.get(f"{BASE}/readiness/compliance", headers=admin_headers()).json()
    assert body["live_ready"] is False
    assert body["blocking_for_live"]
    assert all(item["unblocked_by"] for item in body["blocking_for_live"])


def test_every_assessment_item_cites_evidence(client, admin_headers) -> None:
    body = client.get(f"{BASE}/readiness/compliance", headers=admin_headers()).json()
    for item in body["items"]:
        assert item["evidence"], item["key"]
        assert item["status"] in {"done", "partial", "blocked", "not_implemented"}


def test_the_assessment_covers_the_blueprint_categories(client, admin_headers) -> None:
    body = client.get(f"{BASE}/readiness/compliance", headers=admin_headers()).json()
    categories = {item["category"] for item in body["items"]}
    for required in ("perimeter", "client_money", "promotions", "kyc", "data_protection", "crypto", "operations"):
        assert required in categories


def test_the_counts_match_the_items(client, admin_headers) -> None:
    body = client.get(f"{BASE}/readiness/compliance", headers=admin_headers()).json()
    assert sum(body["counts"].values()) == len(body["items"])


def test_the_summary_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{BASE}/readiness/compliance/summary", headers=auth_headers()).status_code == 403


# ---------------------------------------------------------------- release gate


def test_the_gate_passes_for_the_paper_scope(client, admin_headers) -> None:
    body = client.get(f"{BASE}/readiness/release/gate", headers=admin_headers()).json()
    assert body["verdict"] == "pass", body
    blocking = [check for check in body["checks"] if check.get("blocking", True)]
    assert blocking
    assert all(check["passed"] for check in blocking)


def test_the_gate_still_reports_the_items_blocking_live(client, admin_headers) -> None:
    """A passing paper-release gate must not read as "ready for live"."""
    body = client.get(f"{BASE}/readiness/release/gate", headers=admin_headers()).json()
    informational = [c for c in body["checks"] if not c.get("blocking", True)]
    assert len(informational) == 1
    assert informational[0]["passed"] is False
    assert body["compliance_blocking_count"] > 0


def test_the_gate_says_what_it_does_not_certify(client, admin_headers) -> None:
    body = client.get(f"{BASE}/readiness/release/gate", headers=admin_headers()).json()
    assert "not a regulatory opinion" in body["statement"]


def test_the_gate_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{BASE}/readiness/release/gate", headers=auth_headers()).status_code == 403


def test_advancing_to_live_is_refused(client, admin_headers) -> None:
    response = client.post(
        f"{BASE}/readiness/release/advance", headers=admin_headers(), json={"stage": "stage_3_live"}
    )
    assert response.status_code == 403
    body = response.json()
    assert body["stage"] == "stage_3_live"
    assert body["blocking"]


def test_a_refused_advance_is_audited(client, admin_headers) -> None:
    client.post(
        f"{BASE}/readiness/release/advance", headers=admin_headers(), json={"stage": "stage_3_live"}
    )
    trail = client.get(
        f"{BASE}/operations/audit", headers=admin_headers(), params={"limit": 20}
    ).json()
    refused = [entry for entry in trail["entries"] if entry["action"] == "release.advance"]
    assert refused
    assert any(entry["outcome"] == "denied" for entry in refused)


def test_an_unknown_stage_is_422(client, admin_headers) -> None:
    response = client.post(
        f"{BASE}/readiness/release/advance", headers=admin_headers(), json={"stage": "stage_9"}
    )
    assert response.status_code == 422


def test_the_stage_report_names_the_three_stages(client, auth_headers) -> None:
    body = client.get(f"{BASE}/readiness/stages", headers=auth_headers()).json()
    keys = [stage["key"] for stage in body["stages"]]
    assert keys == ["stage_1_private", "stage_2_research_saas", "stage_3_live"]
    assert body["current"]["next_reachable"] is False
    live = next(stage for stage in body["stages"] if stage["key"] == "stage_3_live")
    assert live["status"] == "not_implemented"


@pytest.mark.parametrize("path", ["/brokers", "/brokers/connections", "/readiness/stages"])
def test_readiness_endpoints_require_auth(client, path: str) -> None:
    assert client.get(f"{BASE}{path}").status_code == 401
