"""Audit logging. Append-only; sensitive actions must call `record_audit`."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy.orm import Session

from app.models.operations import AuditLog, Severity, SystemEvent


def _client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def record_audit(
    db: Session,
    *,
    action: str,
    actor_id: str | None = None,
    actor_email: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    outcome: str = "success",
    detail: str = "",
    request: Request | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        actor_email=actor_email,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        outcome=outcome,
        ip_address=_client_ip(request),
        user_agent=(request.headers.get("user-agent") if request else None),
        detail=detail,
        created_at=datetime.now(UTC).isoformat(),
    )
    db.add(entry)
    return entry


def record_system_event(
    db: Session,
    *,
    category: str,
    message: str,
    severity: Severity = Severity.INFO,
    detail: str = "",
) -> SystemEvent:
    event = SystemEvent(
        category=category,
        severity=severity,
        message=message[:400],
        detail=detail,
        created_at=datetime.now(UTC).isoformat(),
    )
    db.add(event)
    return event
