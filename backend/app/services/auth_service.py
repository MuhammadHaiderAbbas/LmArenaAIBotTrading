"""Authentication service: registration, login, refresh, revocation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.core.config import settings
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.identity import User, UserRole, UserSession, UserSettings
from app.schemas.user import TokenPair
from app.services import account_service
from app.services.errors import DomainError


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _issue_tokens(db: Session, user: User, request: Request | None) -> TokenPair:
    access = create_access_token(user.id, user.role.value)
    refresh = create_refresh_token(user.id, user.role.value)
    now = datetime.now(UTC)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=_hash_refresh(refresh),
            user_agent=(request.headers.get("user-agent") if request else None),
            ip_address=(
                (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
                if request and request.headers.get("x-forwarded-for")
                else (request.client.host if request and request.client else None)
            ),
            created_at=now,
            expires_at=now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    return TokenPair(access_token=access, refresh_token=refresh)


def register(
    db: Session,
    *,
    email: str,
    full_name: str,
    password: str,
    role: UserRole = UserRole.USER,
    request: Request | None = None,
    create_account: bool = True,
) -> User:
    email = email.lower().strip()
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise DomainError("An account with that email already exists.", status_code=409)

    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        is_verified=False,
    )
    user.preferences = UserSettings(base_currency=settings.BASE_CURRENCY)
    db.add(user)
    db.flush()

    if create_account:
        account_service.ensure_default_account(db, user, settings.DEFAULT_PAPER_BALANCE)

    record_audit(
        db,
        action="user.register",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="user",
        entity_id=user.id,
        detail=f"Registered with role={role.value}",
        request=request,
    )
    return user


class MFARequired(DomainError):
    """Password was correct, but the account needs a second factor before it gets tokens.

    Carries the challenge token in `extra` so the API can hand it to the client; it is
    deliberately *not* a session — it opens exactly one endpoint.
    """

    def __init__(self, challenge_token: str) -> None:
        super().__init__(
            "Two-factor authentication is required for this account.",
            status_code=401,
            extra={"mfa_required": True, "challenge_token": challenge_token},
        )


def authenticate(
    db: Session,
    *,
    email: str,
    password: str,
    request: Request | None = None,
    mfa_code: str | None = None,
    second_factor_verified: bool = False,
) -> tuple[User, TokenPair]:
    email = email.lower().strip()
    user = db.scalar(select(User).where(User.email == email))

    if user is None or not verify_password(password, user.password_hash):
        record_audit(
            db,
            action="auth.login",
            actor_email=email,
            outcome="failure",
            detail="Invalid credentials",
            request=request,
        )
        db.commit()
        raise DomainError("Invalid email or password.", status_code=401)

    if not user.is_active:
        record_audit(
            db,
            action="auth.login",
            actor_id=user.id,
            actor_email=user.email,
            outcome="denied",
            detail="Account inactive",
            request=request,
        )
        db.commit()
        raise DomainError("This account has been suspended.", status_code=403)

    if user.mfa_enabled and not second_factor_verified:
        from app.core.security import create_mfa_challenge
        from app.services import mfa

        if not mfa_code:
            record_audit(
                db,
                action="auth.login",
                actor_id=user.id,
                actor_email=user.email,
                outcome="challenge",
                detail="Second factor requested",
                request=request,
            )
            db.commit()
            raise MFARequired(create_mfa_challenge(user.id, user.role.value))

        if not mfa.verify(db, user=user, code=mfa_code):
            record_audit(
                db,
                action="auth.login",
                actor_id=user.id,
                actor_email=user.email,
                outcome="failure",
                detail="Invalid second factor",
                request=request,
            )
            db.commit()
            raise DomainError("That code is not valid.", status_code=401)

    user.last_login_at = datetime.now(UTC)
    tokens = _issue_tokens(db, user, request)
    record_audit(
        db,
        action="auth.login",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
    return user, tokens


def issue_session(db: Session, *, user: User, request: Request | None = None) -> TokenPair:
    """Mint a session for an identity that has already been authenticated.

    Used by the second leg of an MFA login, where the password was checked on the first
    leg and the challenge token carries that fact.
    """
    user.last_login_at = datetime.now(UTC)
    tokens = _issue_tokens(db, user, request)
    record_audit(
        db,
        action="auth.login",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="user",
        entity_id=user.id,
        detail="Second factor verified",
        request=request,
    )
    return tokens


def refresh(db: Session, refresh_token: str, request: Request | None = None) -> TokenPair:
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except jwt.PyJWTError as exc:
        raise DomainError("Invalid or expired refresh token.", status_code=401) from exc

    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == _hash_refresh(refresh_token))
    )
    now = datetime.now(UTC)
    if session is None or session.revoked_at is not None:
        raise DomainError("This session is no longer valid.", status_code=401)
    if session.expires_at.replace(tzinfo=session.expires_at.tzinfo or UTC) < now:
        raise DomainError("This session has expired. Please sign in again.", status_code=401)

    user = db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise DomainError("This account is not active.", status_code=401)

    # Rotate: revoke the presented refresh token, issue a new pair.
    session.revoked_at = now
    return _issue_tokens(db, user, request)


def logout(
    db: Session, user: User, refresh_token: str | None, request: Request | None = None
) -> None:
    if refresh_token:
        session = db.scalar(
            select(UserSession).where(UserSession.token_hash == _hash_refresh(refresh_token))
        )
        if session is not None and session.user_id == user.id:
            session.revoked_at = datetime.now(UTC)
    record_audit(
        db,
        action="auth.logout",
        actor_id=user.id,
        actor_email=user.email,
        request=request,
    )


def change_password(
    db: Session,
    *,
    user: User,
    current_password: str,
    new_password: str,
    request: Request | None = None,
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise DomainError("Your current password is incorrect.", status_code=400)
    user.password_hash = hash_password(new_password)
    # Invalidate all other sessions after a credential change.
    now = datetime.now(UTC)
    for session in db.scalars(select(UserSession).where(UserSession.user_id == user.id)):
        if session.revoked_at is None:
            session.revoked_at = now
    record_audit(
        db,
        action="user.change_password",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="user",
        entity_id=user.id,
        request=request,
    )
