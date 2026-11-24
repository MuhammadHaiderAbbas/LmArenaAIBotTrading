"""Pydantic schemas for the identity and auth API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import settings
from app.core.security import password_policy_errors
from app.models.identity import UserRole


class UserBase(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        errors = password_policy_errors(value)
        if errors:
            raise ValueError(" ".join(errors))
        return value

    @field_validator("full_name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class UserRegister(UserCreate):
    """Public self-registration always yields the standard USER role."""


class AdminUserCreate(UserCreate):
    role: UserRole = UserRole.USER


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    theme: str | None = None
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = None
    email_alerts: bool | None = None
    risk_alerts: bool | None = None


class UserRoleUpdate(BaseModel):
    role: UserRole
    is_active: bool | None = None


class UserRead(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    role: UserRole
    is_active: bool
    is_verified: bool
    mfa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime
    preferences: UserSettingsRead | None = None


class UserSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    theme: str
    base_currency: str
    timezone: str
    email_alerts: bool
    risk_alerts: bool


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    # Present only on the second call, after the server answered `mfa_required`.
    mfa_code: str | None = Field(default=None, max_length=32)


class MFAEnrolRequest(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class MFADisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=32)


class MFAVerifyRequest(BaseModel):
    challenge_token: str = Field(min_length=10)
    code: str = Field(min_length=6, max_length=32)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105 - token type label, not a secret
    expires_in: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _validate_password(cls, value: str) -> str:
        errors = password_policy_errors(value)
        if errors:
            raise ValueError(" ".join(errors))
        return value


class MessageResponse(BaseModel):
    message: str


UserRead.model_rebuild()
