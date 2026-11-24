"""Health and metadata endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings


def test_root_identifies_phase(client: TestClient) -> None:
    body = client.get("/").json()
    assert body["phase"] == "Phase 11 — Security & reliability"
    assert body["name"] == settings.PROJECT_NAME


def test_health_reports_database_and_guardrails(client: TestClient) -> None:
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["live_trading_enabled"] is False
    assert body["real_money_enabled"] is False


def test_metadata_advertises_paper_only(client: TestClient) -> None:
    body = client.get("/api/v1/system/meta").json()
    assert body["execution_mode"] == "paper"
    assert body["live_trading_enabled"] is False


def test_security_headers_present(client: TestClient) -> None:
    response = client.get("/api/v1/system/meta")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_openapi_schema_is_served(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"].startswith("AUREX AI")
    assert "/api/v1/auth/login" in schema["paths"]
