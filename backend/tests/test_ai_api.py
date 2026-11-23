"""AI orchestration API behaviour."""

from __future__ import annotations

import pytest


def test_agents_are_listed_with_unbuilt_ones_marked(client, auth_headers) -> None:
    response = client.get("/api/v1/ai/agents", headers=auth_headers())
    assert response.status_code == 200
    agents = response.json()
    assert len(agents) == 8

    by_code = {agent["code"]: agent for agent in agents}
    assert by_code["technical"]["implemented"] is True
    # The platform must not imply it has specialists it has not built.
    assert by_code["fundamental"]["implemented"] is False
    assert by_code["sentiment"]["implemented"] is False
    assert by_code["quantitative"]["implemented"] is False
    for agent in agents:
        assert agent["deterministic"] is True


def test_ai_endpoints_require_authentication(client) -> None:
    assert client.get("/api/v1/ai/agents").status_code == 401
    assert client.get("/api/v1/ai/runs").status_code == 401
    assert client.post("/api/v1/ai/run", json={"symbol": "GBP/USD"}).status_code == 401


def test_agents_seed_is_staff_only(client, auth_headers) -> None:
    assert client.post("/api/v1/ai/agents/seed", headers=auth_headers()).status_code == 403


def test_run_returns_findings_and_never_a_tradeable_proposal(client, auth_headers) -> None:
    headers = auth_headers()
    response = client.post(
        "/api/v1/ai/run", json={"symbol": "GBP/USD", "timeframe": "1d"}, headers=headers
    )
    assert response.status_code == 201, response.text
    run = response.json()

    assert run["status"] in {"completed", "partial", "refused"}
    assert run["tradeable"] is False
    assert run["blocked_reason"]
    assert run["findings"]

    for finding in run["findings"]:
        assert finding["confidence_kind"] == "rule_score"
        assert finding["evidence"], f"{finding['agent_code']} has no evidence"
        # Facts and interpretation are separate fields, never merged upstream.
        assert "facts" in finding and "interpretation" in finding


def test_run_without_data_refuses_rather_than_inventing_a_view(client, auth_headers) -> None:
    headers = auth_headers()
    response = client.post(
        "/api/v1/ai/run", json={"symbol": "NOT/REAL", "timeframe": "1d"}, headers=headers
    )
    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "refused"
    assert run["proposal_stance"] == "unknown"
    assert run["proposal_confidence"] is None
    assert run["tradeable"] is False


def test_run_is_retrievable_by_id(client, auth_headers) -> None:
    headers = auth_headers()
    created = client.post(
        "/api/v1/ai/run", json={"symbol": "EUR/USD"}, headers=headers
    ).json()
    response = client.get(f"/api/v1/ai/runs/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert len(response.json()["findings"]) == len(created["findings"])


def test_unknown_run_returns_404(client, auth_headers) -> None:
    assert client.get("/api/v1/ai/runs/nope", headers=auth_headers()).status_code == 404


def test_run_rejects_an_unknown_timeframe(client, auth_headers) -> None:
    response = client.post(
        "/api/v1/ai/run", json={"symbol": "GBP/USD", "timeframe": "3s"}, headers=auth_headers()
    )
    assert response.status_code == 422


def test_run_rejects_another_users_account(client, auth_headers, user_factory) -> None:
    """Account context must not leak across users."""
    from app.database.session import SessionLocal
    from app.models.account import Environment, TradingAccount
    from app.services import ledger_service

    user_factory(email="owner@aurex.ai")
    db = SessionLocal()
    try:
        from app.models.identity import User

        owner = db.query(User).filter(User.email == "owner@aurex.ai").one()
        account = TradingAccount(
            user_id=owner.id, name="Owner paper", environment=Environment.PAPER
        )
        db.add(account)
        db.flush()
        ledger_service.open_account_with_funding(db, account=account, amount=1000)
        db.commit()
        account_id = account.id
    finally:
        db.close()

    # A different user may not attach someone else's account.
    response = client.post(
        "/api/v1/ai/run",
        json={"symbol": "GBP/USD", "account_id": account_id},
        headers=auth_headers(email="intruder@aurex.ai"),
    )
    assert response.status_code == 404


def test_overview_counts_runs_and_refusals(client, auth_headers) -> None:
    headers = auth_headers()
    client.get("/api/v1/ai/agents", headers=headers)  # seeds the registry
    client.post("/api/v1/ai/run", json={"symbol": "AAA/BBB"}, headers=headers)
    client.post("/api/v1/ai/run", json={"symbol": "CCC/DDD"}, headers=headers)

    overview = client.get("/api/v1/ai/overview", headers=headers).json()
    assert overview["runs_total"] == 2
    assert overview["runs_refused"] == 2
    assert overview["agents_total"] == 8
    assert overview["agents_implemented"] == 5
    # The invariant the whole phase rests on.
    assert overview["tradeable_proposals"] == 0
    assert len(overview["latest_runs"]) == 2
    assert overview["stance_breakdown"].get("unknown") == 2


def test_proposals_endpoint_only_returns_directional_runs(client, auth_headers) -> None:
    headers = auth_headers()
    client.post("/api/v1/ai/run", json={"symbol": "XXX/YYY"}, headers=headers)
    proposals = client.get("/api/v1/ai/proposals", headers=headers).json()
    # Nothing directional from an empty corpus, so nothing is proposed.
    assert proposals == []


def test_runs_can_be_filtered_by_symbol(client, auth_headers) -> None:
    headers = auth_headers()
    client.post("/api/v1/ai/run", json={"symbol": "FILTER/A"}, headers=headers)
    client.post("/api/v1/ai/run", json={"symbol": "FILTER/B"}, headers=headers)

    response = client.get("/api/v1/ai/runs?symbol=FILTER/A", headers=headers)
    assert response.status_code == 200
    assert [run["instrument_symbol"] for run in response.json()] == ["FILTER/A"]


def test_run_is_rate_limited(client, auth_headers) -> None:
    headers = auth_headers()
    statuses = [
        client.post("/api/v1/ai/run", json={"symbol": "RATE/LIMIT"}, headers=headers).status_code
        for _ in range(14)
    ]
    assert 429 in statuses


def test_run_records_the_question(client, auth_headers) -> None:
    headers = auth_headers()
    run = client.post(
        "/api/v1/ai/run",
        json={"symbol": "GBP/USD", "question": "Should we add to sterling exposure?"},
        headers=headers,
    ).json()
    assert run["question"] == "Should we add to sterling exposure?"
    # The question does not change the answer: it is recorded, not obeyed.
    assert run["tradeable"] is False


@pytest.mark.parametrize("symbol", ["GBP/USD", "SPX500", "BTC/USD"])
def test_runs_work_across_asset_classes(client, auth_headers, symbol) -> None:
    run = client.post(
        "/api/v1/ai/run", json={"symbol": symbol}, headers=auth_headers()
    ).json()
    assert run["instrument_symbol"] == symbol
    assert run["tradeable"] is False
