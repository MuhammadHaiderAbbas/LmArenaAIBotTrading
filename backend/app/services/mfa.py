"""Two-factor authentication: TOTP enrolment, confirmation and verification.

The blueprint asks for MFA. A `mfa_enabled` boolean that nobody ever sets is not MFA —
it is a hint that MFA is missing. This module makes the flag real:

1. `begin_enrolment` mints a TOTP secret (RFC 6238, 30-second step, SHA-1 — what every
   authenticator app supports) and stores it **encrypted** via `app.core.secrets`.
2. `confirm_enrolment` requires a valid code before the flag flips, so a user cannot
   lock themselves out with a secret they never scanned.
3. `complete_login` — once the flag is on, a correct password alone returns a challenge
   token, not a session.
4. `disable` requires both the password and a live code: turning off your second factor
   should be at least as hard as using it.

Recovery codes are single-use and stored hashed, because a recovery code *is* a password.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime

import pyotp
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import secrets as secret_box
from app.core.audit import record_audit
from app.core.security import decode_token, verify_password
from app.models.identity import User, UserRole
from app.models.mfa import MFARecoveryCode
from app.services.errors import DomainError

ISSUER = "AUREX AI"
RECOVERY_CODE_COUNT = 8
VALID_WINDOW = 1  # accept the adjacent 30 s step for clock skew

# Roles that must use a second factor once one exists on the account.
PRIVILEGED_ROLES = {UserRole.ADMIN, UserRole.ANALYST}


def _send_audit(db: Session, user: User, action: str, detail: str) -> None:
    record_audit(db, action=action, actor_id=user.id, actor_email=user.email, detail=detail)


def _secret_for(user: User) -> str:
    if not user.mfa_secret:
        raise DomainError("MFA is not enrolled for this account.", status_code=409)
    return secret_box.decrypt(user.mfa_secret)


def _code_is_valid(user: User, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not code:
        return False
    totp = pyotp.TOTP(_secret_for(user))
    # `valid_window=1` tolerates one step of clock drift in either direction.
    return totp.verify(code, valid_window=VALID_WINDOW)


def begin_enrolment(db: Session, *, user: User) -> dict:
    """Start enrolment. Safe to call twice: an unconfirmed secret is replaced."""
    if user.mfa_enabled and user.mfa_secret:
        raise DomainError("MFA is already enabled for this account.", status_code=409)
    secret = pyotp.random_base32()
    user.mfa_secret = secret_box.encrypt(secret)
    user.mfa_enrolled_at = None
    db.flush()
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return {"secret": secret, "otpauth_url": uri, "issuer": ISSUER, "digits": 6, "period": 30}


def confirm_enrolment(db: Session, *, user: User, code: str) -> dict:
    """Verify a code and turn MFA on. Returns the one-time recovery codes."""
    if not user.mfa_secret:
        raise DomainError("Start MFA enrolment before confirming it.", status_code=409)
    if user.mfa_enabled:
        raise DomainError("MFA is already enabled for this account.", status_code=409)
    if not _code_is_valid(user, code):
        raise DomainError(
            "That code is not valid. Check the clock on your device.", status_code=400
        )
    user.mfa_enabled = True
    user.mfa_enrolled_at = datetime.now(UTC)
    codes = _issue_recovery_codes(db, user)
    _send_audit(db, user, "auth.mfa_enabled", "Two-factor authentication enabled.")
    db.flush()
    return {"mfa_enabled": True, "recovery_codes": codes}


def _issue_recovery_codes(db: Session, user: User) -> list[str]:
    for existing in db.scalars(select(MFARecoveryCode).where(MFARecoveryCode.user_id == user.id)):
        db.delete(existing)
    db.flush()
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}"
        codes.append(raw)
        db.add(MFARecoveryCode(user_id=user.id, code_hash=hashlib.sha256(raw.encode()).hexdigest()))
    db.flush()
    return codes


def verify(db: Session, *, user: User, code: str) -> bool:
    """A TOTP code or an unused recovery code. Consumes a recovery code on success."""
    if not user.mfa_enabled:
        raise DomainError("MFA is not enabled for this account.", status_code=409)
    if _code_is_valid(user, code):
        return True
    normalised = (code or "").strip().lower()
    digest = hashlib.sha256(normalised.encode()).hexdigest()
    row = db.scalar(
        select(MFARecoveryCode).where(
            MFARecoveryCode.user_id == user.id,
            MFARecoveryCode.code_hash == digest,
            MFARecoveryCode.used_at.is_(None),
        )
    )
    if row is None:
        return False
    row.used_at = datetime.now(UTC)
    db.flush()
    _send_audit(db, user, "auth.mfa_recovery_used", "A recovery code was used to sign in.")
    return True


def complete_login(db: Session, *, challenge_token: str, code: str) -> User:
    """Exchange a challenge token plus a valid second factor for a real session."""
    payload = decode_token(challenge_token, expected_type="mfa_challenge")
    user = db.scalar(select(User).where(User.id == payload["sub"]))
    if user is None or not user.is_active:
        raise DomainError("This challenge is no longer valid.", status_code=401)
    if not verify(db, user=user, code=code):
        _send_audit(db, user, "auth.mfa_failed", "An incorrect second factor was supplied.")
        raise DomainError("That code is not valid.", status_code=401)
    return user


def disable(db: Session, *, user: User, password: str, code: str) -> None:
    """Turning a second factor off needs the password *and* a live code."""
    if not user.mfa_enabled:
        raise DomainError("MFA is not enabled for this account.", status_code=409)
    if not verify_password(password, user.password_hash):
        raise DomainError("That password is not correct.", status_code=401)
    if not verify(db, user=user, code=code):
        raise DomainError("That code is not valid.", status_code=401)
    user.mfa_enabled = False
    user.mfa_secret = None
    user.mfa_enrolled_at = None
    for row in db.scalars(select(MFARecoveryCode).where(MFARecoveryCode.user_id == user.id)):
        db.delete(row)
    _send_audit(db, user, "auth.mfa_disabled", "Two-factor authentication disabled.")
    db.flush()


def requires_mfa(user: User) -> bool:
    """Is this account's participation in MFA mandatory rather than voluntary?"""
    return user.role in PRIVILEGED_ROLES


def policy_report(db: Session, *, user: User) -> dict:
    """An honest statement of what is enforced for this account *right now*."""
    remaining = (
        len(
            list(
                db.scalars(
                    select(MFARecoveryCode).where(
                        MFARecoveryCode.user_id == user.id, MFARecoveryCode.used_at.is_(None)
                    )
                )
            )
        )
        if user.mfa_enabled
        else 0
    )
    return {
        "mfa_enabled": user.mfa_enabled,
        "mfa_enrolled_at": user.mfa_enrolled_at,
        "required_for_role": requires_mfa(user),
        "recovery_codes_remaining": remaining,
        "method": "TOTP (RFC 6238)" if user.mfa_enabled else None,
    }
