"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.rate_limit import enforce_rate_limit
from app.schemas.user import (
    LoginRequest,
    MessageResponse,
    MFADisableRequest,
    MFAEnrolRequest,
    MFAVerifyRequest,
    RefreshRequest,
    TokenPair,
    UserRead,
    UserRegister,
)
from app.services import auth_service, mfa

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, request: Request, db: DbSession) -> UserRead:
    enforce_rate_limit(request, "register", limit=5)
    user = auth_service.register(
        db,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        request=request,
    )
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenPair:
    enforce_rate_limit(request, "login", limit=settings.MAX_LOGIN_ATTEMPTS_PER_MINUTE)
    _, tokens = auth_service.authenticate(
        db,
        email=payload.email,
        password=payload.password,
        request=request,
        mfa_code=payload.mfa_code,
    )
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, db: DbSession) -> TokenPair:
    enforce_rate_limit(request, "refresh", limit=30)
    tokens = auth_service.refresh(db, payload.refresh_token, request=request)
    db.commit()
    return tokens


@router.post("/logout", response_model=MessageResponse)
def logout(
    request: Request,
    db: DbSession,
    user: CurrentUser,
    payload: RefreshRequest | None = None,
) -> MessageResponse:
    auth_service.logout(db, user, payload.refresh_token if payload else None, request=request)
    db.commit()
    return MessageResponse(message="Signed out.")


@router.get("/me", response_model=UserRead)
def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


# --------------------------------------------------------------------------
# Two-factor authentication
# --------------------------------------------------------------------------
@router.get("/mfa")
def mfa_status(db: DbSession, user: CurrentUser) -> dict:
    """Exactly what is enforced on this account, with no reassurance it has not earned."""
    return mfa.policy_report(db, user=user)


@router.post("/mfa/enrol")
def mfa_enrol(db: DbSession, user: CurrentUser) -> dict:
    """Start enrolment: returns the shared secret and an otpauth:// URL for the app."""
    payload = mfa.begin_enrolment(db, user=user)
    db.commit()
    return payload


@router.post("/mfa/confirm")
def mfa_confirm(payload: MFAEnrolRequest, db: DbSession, user: CurrentUser) -> dict:
    """Verify a code from the app before switching the factor on."""
    result = mfa.confirm_enrolment(db, user=user, code=payload.code)
    db.commit()
    return result


@router.post("/mfa/verify")
def mfa_verify(payload: MFAVerifyRequest, request: Request, db: DbSession) -> TokenPair:
    """Step two of a challenged login: challenge token + code -> session."""
    enforce_rate_limit(request, "mfa_verify", limit=10)
    user = mfa.complete_login(db, challenge_token=payload.challenge_token, code=payload.code)
    tokens = auth_service.issue_session(db, user=user, request=request)
    db.commit()
    return tokens


@router.post("/mfa/disable", response_model=MessageResponse)
def mfa_disable(payload: MFADisableRequest, db: DbSession, user: CurrentUser) -> MessageResponse:
    mfa.disable(db, user=user, password=payload.password, code=payload.code)
    db.commit()
    return MessageResponse(message="Two-factor authentication disabled.")
