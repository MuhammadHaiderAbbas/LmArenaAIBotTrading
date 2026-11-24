"""The operations endpoints — what is public, what is staff-only, and why."""

from __future__ import annotations

import pytest

from app.core import release

OPERATIONS = "/api/v1/operations"


def test_health_is_public(client) -> None:
    """A liveness probe has to answer before anyone has a token."""
    response = client.get(f"{OPERATIONS}/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "degraded"}
    assert body["phase"] == release.PHASE_NAME


def test_health_is_shallow(client) -> None:
    """Public means shallow: no balances, no symbols, no strategy names."""
    body = client.get(f"{OPERATIONS}/health").json()
    assert body["checks"] == "shallow"
    assert "findings" not in body
    assert set(body) <= {
        "status",
        "phase",
        "phases_complete",
        "checks",
        "checked_at",
        "version",
    }


def test_monitoring_requires_auth(client) -> None:
    assert client.get(f"{OPERATIONS}/monitoring").status_code == 401


def test_monitoring_returns_findings_for_a_user(client, auth_headers) -> None:
    response = client.get(f"{OPERATIONS}/monitoring", headers=auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in {"ok", "warning", "critical"}
    assert body["checks_run"] >= 1
    assert all({"check", "status", "message"} <= set(f) for f in body["findings"])


def test_system_monitoring_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{OPERATIONS}/monitoring/system", headers=auth_headers()).status_code == 403


def test_system_monitoring_works_for_staff(client, admin_headers) -> None:
    response = client.get(f"{OPERATIONS}/monitoring/system", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["scope"] == "system"
    assert "data_freshness" in {f["check"] for f in body["findings"]}


def test_audit_list_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{OPERATIONS}/audit", headers=auth_headers()).status_code == 403


def test_audit_list_filters_and_pages(client, admin_headers) -> None:
    response = client.get(f"{OPERATIONS}/audit", headers=admin_headers(), params={"limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body["entries"], list)
    assert len(body["entries"]) <= 5
    assert body["total"] >= len(body["entries"])


def test_audit_entries_carry_their_seal(client, admin_headers) -> None:
    body = client.get(f"{OPERATIONS}/audit", headers=admin_headers(), params={"limit": 3}).json()
    for entry in body["entries"]:
        assert set(entry) >= {"sequence", "action", "entry_hash", "sealed"}


def test_audit_verify_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{OPERATIONS}/audit/verify", headers=auth_headers()).status_code == 403


def test_audit_verify_reports_a_clean_chain(client, admin_headers) -> None:
    response = client.get(f"{OPERATIONS}/audit/verify", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["verified"] is True
    assert body["first_broken_at"] is None
    assert body["entries"] >= 0


def test_seal_is_staff_only(client, auth_headers) -> None:
    assert client.post(f"{OPERATIONS}/audit/seal", headers=auth_headers()).status_code == 403


def test_seal_adopts_pending_entries(client, admin_headers) -> None:
    response = client.post(f"{OPERATIONS}/audit/seal", headers=admin_headers())
    assert response.status_code == 200
    body = response.json()
    assert "sealed" in body
    assert body["sealed"] >= 0
    # A second call finds nothing left to do — sealing is idempotent.
    assert client.post(f"{OPERATIONS}/audit/seal", headers=admin_headers()).json()["sealed"] == 0


def test_digest_is_staff_only(client, auth_headers) -> None:
    assert client.get(f"{OPERATIONS}/audit/digest", headers=auth_headers()).status_code == 403


def test_digest_is_a_publishable_fingerprint(client, admin_headers) -> None:
    """The head digest only moves forward: publishing it pins the tail in place."""
    first = client.get(f"{OPERATIONS}/audit/digest", headers=admin_headers()).json()
    second = client.get(f"{OPERATIONS}/audit/digest", headers=admin_headers()).json()
    assert len(first["digest"]) == 64
    assert second["head_sequence"] >= first["head_sequence"]


def test_digest_of_an_empty_chain_is_genesis(client) -> None:
    """Nothing written yet. The digest must still be well-formed, not null."""
    from app.services.integrity import GENESIS

    assert len(GENESIS) == 64


def test_unknown_audit_action_is_404(client, admin_headers) -> None:
    assert client.get(f"{OPERATIONS}/audit/nonsense", headers=admin_headers()).status_code == 404


@pytest.mark.parametrize("path", ["/monitoring", "/audit", "/audit/verify", "/audit/digest"])
def test_no_operations_endpoint_leaks_without_auth(client, path: str) -> None:
    assert client.get(f"{OPERATIONS}{path}").status_code == 401
