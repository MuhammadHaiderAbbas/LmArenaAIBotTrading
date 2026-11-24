"""MFA, secrets-at-rest, and what the platform does *not* pretend to have.

The blueprint's §12 line is "MFA, secure session handling and role-based access control".
The first two tests below are the whole point: a password alone must not open a
two-factor account, and turning the factor off must be as hard as using it.
"""

from __future__ import annotations

import pyotp
import pytest

from app.core import secrets as secret_box
from app.services import mfa

AUTH = "/api/v1/auth"


def enrol(client, auth_headers, admin_headers=None) -> tuple[dict, str, list[str]]:
    """Enrol the standard test user and return (headers, secret, recovery_codes)."""
    headers = auth_headers()
    started = client.post(f"{AUTH}/mfa/enrol", headers=headers)
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    confirmed = client.post(
        f"{AUTH}/mfa/confirm", headers=headers, json={"code": pyotp.TOTP(secret).now()}
    )
    assert confirmed.status_code == 200, confirmed.text
    return headers, secret, confirmed.json()["recovery_codes"]


def test_a_password_alone_does_not_open_a_two_factor_account(client, auth_headers) -> None:
    """The regression that matters. Before this, `mfa_enabled` was a decorative flag."""
    enrol(client, auth_headers)
    response = client.post(
        f"{AUTH}/login", json={"email": "trader@aurex.ai", "password": "Str0ngPass!"}
    )
    assert response.status_code == 401
    body = response.json()
    assert body["mfa_required"] is True
    assert "access_token" not in body
    assert len(body["challenge_token"]) > 20


def test_a_challenge_token_is_not_a_session_token(client, auth_headers) -> None:
    enrol(client, auth_headers)
    challenge = client.post(
        f"{AUTH}/login", json={"email": "trader@aurex.ai", "password": "Str0ngPass!"}
    ).json()["challenge_token"]
    # It must not work as a bearer token against a protected endpoint.
    assert (
        client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {challenge}"}).status_code
        == 401
    )


def test_the_second_leg_issues_a_session(client, auth_headers) -> None:
    _, secret, _ = enrol(client, auth_headers)
    challenge = client.post(
        f"{AUTH}/login", json={"email": "trader@aurex.ai", "password": "Str0ngPass!"}
    ).json()["challenge_token"]
    response = client.post(
        f"{AUTH}/mfa/verify", json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()}
    )
    assert response.status_code == 200, response.text
    assert "access_token" in response.json()


def test_a_wrong_code_does_not_issue_a_session(client, auth_headers) -> None:
    enrol(client, auth_headers)
    challenge = client.post(
        f"{AUTH}/login", json={"email": "trader@aurex.ai", "password": "Str0ngPass!"}
    ).json()["challenge_token"]
    response = client.post(
        f"{AUTH}/mfa/verify", json={"challenge_token": challenge, "code": "000000"}
    )
    assert response.status_code == 401


def test_a_recovery_code_works_exactly_once(client, auth_headers) -> None:
    """A recovery code is a password that burns. Two uses must not both succeed."""
    _, _, codes = enrol(client, auth_headers)
    code = codes[0]

    def attempt() -> int:
        challenge = client.post(
            f"{AUTH}/login", json={"email": "trader@aurex.ai", "password": "Str0ngPass!"}
        ).json()["challenge_token"]
        return client.post(
            f"{AUTH}/mfa/verify", json={"challenge_token": challenge, "code": code}
        ).status_code

    assert attempt() == 200
    assert attempt() == 401


def test_confirming_without_enrolling_is_refused(client, auth_headers) -> None:
    response = client.post(f"{AUTH}/mfa/confirm", headers=auth_headers(), json={"code": "123456"})
    assert response.status_code == 409


def test_confirming_with_the_wrong_code_leaves_mfa_off(client, auth_headers) -> None:
    headers = auth_headers()
    client.post(f"{AUTH}/mfa/enrol", headers=headers)
    response = client.post(f"{AUTH}/mfa/confirm", headers=headers, json={"code": "000000"})
    assert response.status_code == 400
    assert client.get(f"{AUTH}/mfa", headers=headers).json()["mfa_enabled"] is False


def test_disabling_needs_the_password_and_a_code(client, auth_headers) -> None:
    headers, secret, _ = enrol(client, auth_headers)
    assert (
        client.post(
            f"{AUTH}/mfa/disable",
            headers=headers,
            json={"password": "WrongPass1", "code": pyotp.TOTP(secret).now()},
        ).status_code
        == 401
    )
    assert (
        client.post(
            f"{AUTH}/mfa/disable",
            headers=headers,
            json={"password": "Str0ngPass!", "code": "000000"},
        ).status_code
        == 401
    )
    assert client.get(f"{AUTH}/mfa", headers=headers).json()["mfa_enabled"] is True
    assert (
        client.post(
            f"{AUTH}/mfa/disable",
            headers=headers,
            json={"password": "Str0ngPass!", "code": pyotp.TOTP(secret).now()},
        ).status_code
        == 200
    )
    assert client.get(f"{AUTH}/mfa", headers=headers).json()["mfa_enabled"] is False


def test_the_policy_report_is_honest_about_an_unenrolled_account(client, auth_headers) -> None:
    body = client.get(f"{AUTH}/mfa", headers=auth_headers()).json()
    assert body["mfa_enabled"] is False
    assert body["method"] is None
    assert body["recovery_codes_remaining"] == 0


def test_admin_is_marked_as_requiring_mfa(client, admin_headers, auth_headers) -> None:
    """Staff accounts are told the factor is mandatory for them, not merely available."""
    assert client.get(f"{AUTH}/mfa", headers=admin_headers()).json()["required_for_role"] is True
    assert client.get(f"{AUTH}/mfa", headers=auth_headers()).json()["required_for_role"] is False


def test_enrolment_is_refused_once_mfa_is_on(client, auth_headers) -> None:
    headers, _, _ = enrol(client, auth_headers)
    assert client.post(f"{AUTH}/mfa/enrol", headers=headers).status_code == 409


def test_the_otpauth_uri_names_the_issuer(client, auth_headers) -> None:
    body = client.post(f"{AUTH}/mfa/enrol", headers=auth_headers()).json()
    assert body["otpauth_url"].startswith("otpauth://totp/")
    assert "issuer=AUREX" in body["otpauth_url"]


def test_mfa_endpoints_require_authentication(client) -> None:
    assert client.get(f"{AUTH}/mfa").status_code == 401
    assert client.post(f"{AUTH}/mfa/enrol").status_code == 401


def test_enabling_mfa_is_written_to_the_audit_trail(client, auth_headers, admin_headers) -> None:
    enrol(client, auth_headers)
    entries = client.get("/api/v1/operations/audit", headers=admin_headers(), params={"limit": 50})
    actions = {entry["action"] for entry in entries.json()["entries"]}
    assert "auth.mfa_enabled" in actions


# ------------------------------------------------------------------ secrets


def test_a_secret_round_trips() -> None:
    token = secret_box.encrypt("sk-live-abcdef123456")
    assert token.startswith("v1.")
    assert "sk-live" not in token
    assert secret_box.decrypt(token) == "sk-live-abcdef123456"


def test_the_same_plaintext_encrypts_differently_each_time() -> None:
    """A per-value nonce, so equal keys are not visible as equal ciphertext."""
    assert secret_box.encrypt("same") != secret_box.encrypt("same")


def test_tampering_is_detected() -> None:
    version, nonce, ciphertext, tag = secret_box.encrypt("bank-key").split(".")
    with pytest.raises(ValueError, match="authentication"):
        secret_box.decrypt(".".join([version, nonce, ciphertext[:-2] + "AA", tag]))


def test_a_different_key_cannot_decrypt() -> None:
    token = secret_box.encrypt("x", secret="key-one")
    with pytest.raises(ValueError, match="authentication"):
        secret_box.decrypt(token, secret="key-two")


def test_redact_keeps_only_the_tail() -> None:
    assert secret_box.redact("ABCD1234EFGH") == "…EFGH"
    assert secret_box.redact(None) == "—"


def test_the_stored_totp_secret_is_not_readable(client, auth_headers) -> None:
    """Encryption at rest is only worth anything if the column is actually encrypted."""
    from sqlalchemy import select

    from app.database.session import SessionLocal
    from app.models.identity import User

    headers = auth_headers()
    secret = client.post(f"{AUTH}/mfa/enrol", headers=headers).json()["secret"]
    db = SessionLocal()
    try:
        stored = db.scalar(select(User).where(User.email == "trader@aurex.ai")).mfa_secret
    finally:
        db.close()
    assert stored is not None
    assert secret not in stored
    assert stored.startswith("v1.")


def test_a_privileged_role_is_marked_required() -> None:
    from app.models.identity import User, UserRole

    assert mfa.requires_mfa(User(role=UserRole.ADMIN)) is True
    assert mfa.requires_mfa(User(role=UserRole.USER)) is False
