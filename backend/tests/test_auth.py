"""Authentication, authorisation and session lifecycle."""

from __future__ import annotations

from fastapi.testclient import TestClient

VALID_USER = {
    "email": "Nadia@Aurex.AI",
    "full_name": "Nadia Rahman",
    "password": "Str0ngPass!",
}


def test_register_creates_user_with_paper_account(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register", json=VALID_USER)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == "nadia@aurex.ai"  # normalised
    assert body["role"] == "user"
    assert body["is_active"] is True


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    response = client.post("/api/v1/auth/register", json=VALID_USER)
    assert response.status_code == 409


def test_register_weak_password_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/register",
        json={**VALID_USER, "password": "alllowercase"},
    )
    assert response.status_code == 422
    assert "uppercase" in response.json()["detail"].lower()


def test_login_returns_token_pair(client: TestClient) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]


def test_login_with_wrong_password_is_401(client: TestClient) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    response = client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": "WrongPass1!"},
    )
    assert response.status_code == 401


def test_refresh_rotates_token_and_revokes_old_one(client: TestClient) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    ).json()

    refreshed = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != tokens["refresh_token"]

    reuse = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401


def test_protected_endpoint_requires_token(client: TestClient) -> None:
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/accounts").status_code == 401


def test_me_returns_profile_and_preferences(client: TestClient) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    ).json()
    response = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Nadia Rahman"
    assert body["preferences"]["theme"] == "dark"


def test_admin_endpoints_blocked_for_standard_user(client: TestClient, auth_headers) -> None:
    headers = auth_headers()
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/audit-logs", headers=headers).status_code == 403


def test_admin_can_list_users_and_audit_trail(client: TestClient, admin_headers) -> None:
    headers = admin_headers()
    users = client.get("/api/v1/admin/users", headers=headers)
    assert users.status_code == 200
    assert any(u["email"] == "root@aurex.ai" for u in users.json())

    audit = client.get("/api/v1/admin/audit-logs", headers=headers)
    assert audit.status_code == 200
    actions = {entry["action"] for entry in audit.json()}
    assert "user.register" in actions
    assert "auth.login" in actions


def test_failed_login_is_audited(client: TestClient, admin_headers) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": "WrongPass1!"},
    )
    logs = client.get(
        "/api/v1/admin/audit-logs", params={"action": "auth.login"}, headers=admin_headers()
    ).json()
    assert any(entry["outcome"] == "failure" for entry in logs)


def test_login_rate_limit_returns_429(client: TestClient) -> None:
    payload = {"email": "flood@aurex.ai", "password": "Wrong1Pass!"}
    statuses = [client.post("/api/v1/auth/login", json=payload).status_code for _ in range(12)]
    assert 429 in statuses


def test_change_password_invalidates_other_sessions(client: TestClient) -> None:
    client.post("/api/v1/auth/register", json=VALID_USER)
    tokens = client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    changed = client.post(
        "/api/v1/users/me/password",
        json={"current_password": VALID_USER["password"], "new_password": "N3wStrongPass!"},
        headers=headers,
    )
    assert changed.status_code == 200

    stale = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert stale.status_code == 401

    relogin = client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": "N3wStrongPass!"},
    )
    assert relogin.status_code == 200
