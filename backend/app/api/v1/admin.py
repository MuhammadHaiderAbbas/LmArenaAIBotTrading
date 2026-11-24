"""Administration endpoints: users, audit trail, system status, integrations."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import AdminUser, DbSession
from app.core.audit import record_audit
from app.core.config import settings
from app.models.account import AccountBalance, TradingAccount
from app.models.identity import User, UserRole
from app.models.ledger import LedgerTransaction
from app.models.operations import AuditLog, IntegrationHealth, Notification
from app.schemas.system import AuditLogRead, IntegrationRead, SystemStatus
from app.schemas.user import AdminUserCreate, UserRead, UserRoleUpdate
from app.services import auth_service
from app.services.errors import DomainError

router = APIRouter(tags=["admin"])

STARTED_AT = time.time()

DEFAULT_INTEGRATIONS = [
    ("market_data", "market_data", "Market data provider (Phase 3)"),
    ("news_feed", "research", "News and events feed (Phase 4)"),
    ("llm_provider", "ai", "LLM research provider (Phase 4)"),
    ("broker_adapter", "execution", "Broker / exchange adapter (Phase 8)"),
    ("payment_provider", "payments", "Payment gateway — disabled in Phase 1"),
]


@router.get("/admin/users", response_model=list[UserRead])
def list_users(
    db: DbSession,
    _: AdminUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[UserRead]:
    users = db.scalars(select(User).order_by(User.created_at.desc()).limit(limit).offset(offset))
    return [UserRead.model_validate(u) for u in users]


@router.post("/admin/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate, request: Request, db: DbSession, admin: AdminUser
) -> UserRead:
    user = auth_service.register(
        db,
        email=payload.email,
        full_name=payload.full_name,
        password=payload.password,
        role=payload.role,
        request=request,
    )
    record_audit(
        db,
        action="admin.user_create",
        actor_id=admin.id,
        actor_email=admin.email,
        entity_type="user",
        entity_id=user.id,
        detail=f"created {user.email} as {user.role.value}",
        request=request,
    )
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.patch("/admin/users/{user_id}", response_model=UserRead)
def update_user_role(
    user_id: str, payload: UserRoleUpdate, request: Request, db: DbSession, admin: AdminUser
) -> UserRead:
    user = db.get(User, user_id)
    if user is None:
        raise DomainError("User not found.", status_code=404)
    if user.id == admin.id and payload.role != UserRole.ADMIN:
        raise DomainError("You cannot remove your own administrator role.", status_code=400)

    previous_role = user.role
    user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    record_audit(
        db,
        action="admin.user_update",
        actor_id=admin.id,
        actor_email=admin.email,
        entity_type="user",
        entity_id=user.id,
        detail=f"role {previous_role.value} -> {user.role.value}; active={user.is_active}",
        request=request,
    )
    db.commit()
    db.refresh(user)
    return UserRead.model_validate(user)


@router.get("/admin/audit-logs", response_model=list[AuditLogRead])
def list_audit_logs(
    db: DbSession,
    _: AdminUser,
    action: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[AuditLogRead]:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    return [AuditLogRead.model_validate(log) for log in db.scalars(stmt)]


@router.get("/admin/integrations", response_model=list[IntegrationRead])
def list_integrations(db: DbSession, _: AdminUser) -> list[IntegrationRead]:
    existing = {i.provider: i for i in db.scalars(select(IntegrationHealth))}
    result: list[IntegrationRead] = []
    for provider, category, message in DEFAULT_INTEGRATIONS:
        record = existing.get(provider)
        if record is None:
            record = IntegrationHealth(
                provider=provider, category=category, status="not_configured", message=message
            )
            db.add(record)
        result.append(IntegrationRead.model_validate(record))
    db.commit()
    return result


@router.get("/admin/system-status", response_model=SystemStatus)
def system_status(db: DbSession, _: AdminUser) -> SystemStatus:
    counts = {
        "users": db.scalar(select(func.count()).select_from(User)) or 0,
        "trading_accounts": db.scalar(select(func.count()).select_from(TradingAccount)) or 0,
        "balances": db.scalar(select(func.count()).select_from(AccountBalance)) or 0,
        "ledger_transactions": db.scalar(select(func.count()).select_from(LedgerTransaction)) or 0,
        "audit_logs": db.scalar(select(func.count()).select_from(AuditLog)) or 0,
        "notifications": db.scalar(select(func.count()).select_from(Notification)) or 0,
    }
    return SystemStatus(
        environment=settings.ENVIRONMENT,
        version=settings.PROJECT_NAME + " v0.1.0",
        uptime_seconds=round(time.time() - STARTED_AT, 2),
        counts=counts,
        guardrails={
            "live_trading_enabled": settings.LIVE_TRADING_ENABLED,
            "real_money_enabled": settings.REAL_MONEY_ENABLED,
            "execution_mode": "paper",
            "checked_at": datetime.now(UTC).isoformat(),
        },
    )
