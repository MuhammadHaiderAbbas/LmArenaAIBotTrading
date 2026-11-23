"""Trading account creation, balances and RBAC isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_registration_provisions_funded_paper_account(client: TestClient) -> None:
    client.post(
        "/api/v1/auth/register",
        json={"email": "a@aurex.ai", "full_name": "A Trader", "password": "Str0ngPass!"},
    )
    tokens = client.post(
        "/api/v1/auth/login", json={"email": "a@aurex.ai", "password": "Str0ngPass!"}
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    accounts = client.get("/api/v1/accounts", headers=headers).json()
    assert len(accounts) == 1
    assert accounts[0]["environment"] == "paper"
    assert accounts[0]["leverage_enabled"] is False

    detail = client.get(f"/api/v1/accounts/{accounts[0]['id']}", headers=headers).json()
    balance = detail["balances"][0]
    assert balance["cash"] == 100000.0
    assert balance["currency"] == "GBP"


def test_user_cannot_read_another_users_account(client: TestClient) -> None:
    def register(email: str) -> dict[str, str]:
        client.post(
            "/api/v1/auth/register",
            json={"email": email, "full_name": "Trader X", "password": "Str0ngPass!"},
        )
        token = client.post(
            "/api/v1/auth/login", json={"email": email, "password": "Str0ngPass!"}
        ).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    first, second = register("one@aurex.ai"), register("two@aurex.ai")
    account_id = client.get("/api/v1/accounts", headers=first).json()[0]["id"]

    assert client.get(f"/api/v1/accounts/{account_id}", headers=second).status_code == 404
    assert client.get("/api/v1/accounts/does-not-exist", headers=first).status_code == 404


def test_create_second_account(client: TestClient, auth_headers) -> None:
    headers = auth_headers()
    response = client.post(
        "/api/v1/accounts",
        json={"name": "Strategy Sandbox", "base_currency": "USD", "opening_balance": 25000},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Strategy Sandbox"
    assert body["balances"][0]["cash"] == 25000.0
    assert body["balances"][0]["currency"] == "USD"

    duplicate = client.post(
        "/api/v1/accounts",
        json={"name": "Strategy Sandbox", "base_currency": "USD", "opening_balance": 25000},
        headers=headers,
    )
    assert duplicate.status_code == 409


def test_live_environment_cannot_be_requested(client: TestClient, auth_headers) -> None:
    response = client.post(
        "/api/v1/accounts",
        json={"name": "Live Attempt", "environment": "live", "opening_balance": 1000},
        headers=auth_headers(),
    )
    assert response.status_code == 422


def _primary_account(client: TestClient, headers: dict[str, str]) -> str:
    return client.get("/api/v1/accounts", headers=headers).json()[0]["id"]


def test_virtual_deposit_and_withdrawal_update_balance(client: TestClient, auth_headers) -> None:
    headers = auth_headers()
    account_id = _primary_account(client, headers)

    deposited = client.post(
        f"/api/v1/accounts/{account_id}/deposit", json={"amount": 5000}, headers=headers
    )
    assert deposited.status_code == 200
    assert deposited.json()["balances"][0]["cash"] == 105000.0

    withdrawn = client.post(
        f"/api/v1/accounts/{account_id}/withdraw", json={"amount": 105000}, headers=headers
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["balances"][0]["cash"] == 0.0


def test_withdrawal_beyond_balance_is_rejected(client: TestClient, auth_headers) -> None:
    headers = auth_headers()
    account_id = _primary_account(client, headers)
    response = client.post(
        f"/api/v1/accounts/{account_id}/withdraw", json={"amount": 999999}, headers=headers
    )
    assert response.status_code == 400
    assert "insufficient" in response.json()["detail"].lower()


def test_ledger_statement_balances_to_zero(client: TestClient, auth_headers) -> None:
    headers = auth_headers()
    account_id = _primary_account(client, headers)
    client.post(f"/api/v1/accounts/{account_id}/deposit", json={"amount": 1234.56}, headers=headers)

    statement = client.get(f"/api/v1/accounts/{account_id}/ledger", headers=headers).json()
    assert statement["total"] == 2
    assert statement["trial_balance"] == 0  # every transaction balances

    for transaction in statement["transactions"]:
        assert sum(entry["amount"] for entry in transaction["entries"]) == 0

    kinds = {t["transaction_type"] for t in statement["transactions"]}
    assert kinds == {"opening_funding", "deposit"}


def test_reconciliation_is_balanced(client: TestClient, auth_headers) -> None:
    headers = auth_headers()
    account_id = _primary_account(client, headers)
    client.post(f"/api/v1/accounts/{account_id}/withdraw", json={"amount": 25000}, headers=headers)

    recon = client.post(f"/api/v1/accounts/{account_id}/reconcile", headers=headers).json()
    assert recon["is_balanced"] is True
    assert recon["ledger_cash"] == recon["cached_cash"] == 75000.0


def test_account_creation_is_audited(client: TestClient, admin_headers, auth_headers) -> None:
    auth_headers()
    client.post(
        "/api/v1/accounts",
        json={"name": "Audited Account", "opening_balance": 1000},
        headers=auth_headers(),
    )
    logs = client.get(
        "/api/v1/admin/audit-logs", params={"action": "account.create"}, headers=admin_headers()
    ).json()
    assert len(logs) >= 1
    assert logs[0]["entity_type"] == "trading_account"
