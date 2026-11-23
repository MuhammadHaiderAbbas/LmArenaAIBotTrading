"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import CurrentUser, DbSession
from app.core.config import settings
from app.core.rate_limit import enforce_rate_limit
from app.schemas.user import (
    LoginRequest,
    MessageResponse,
    RefreshRequest,
    TokenPair,
    UserRead,
    UserRegister,
)
from app.services import auth_service

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
        db, email=payload.email, password=payload.password, request=request
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
