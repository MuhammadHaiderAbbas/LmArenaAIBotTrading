"""User profile endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.deps import CurrentUser, DbSession
from app.core.audit import record_audit
from app.models.identity import UserSettings
from app.schemas.user import ChangePasswordRequest, MessageResponse, UserRead, UserUpdate
from app.services import auth_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserRead)
def get_profile(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("/me", response_model=UserRead)
def update_profile(
    payload: UserUpdate, request: Request, db: DbSession, user: CurrentUser
) -> UserRead:
    if payload.full_name is not None:
        user.full_name = " ".join(payload.full_name.split())

    prefs = user.preferences or UserSettings(user_id=user.id)
    for field in ("theme", "base_currency", "timezone", "email_alerts", "risk_alerts"):
        value = getattr(payload, field)
        if value is not None:
            setattr(prefs, field, value.upper() if field == "base_currency" else value)
    if user.preferences is None:
        db.add(prefs)
        user.preferences = prefs

    record_audit(
        db,
        action="user.update_profile",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="user",
        entity_id=user.id,
        detail=", ".join(payload.model_dump(exclude_none=True).keys()),
        request=request,
    )
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.post("/me/password", response_model=MessageResponse)
def change_password(
    payload: ChangePasswordRequest, request: Request, db: DbSession, user: CurrentUser
) -> MessageResponse:
    auth_service.change_password(
        db,
        user=user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        request=request,
    )
    db.commit()
    return MessageResponse(message="Password updated. Please sign in again on other devices.")
