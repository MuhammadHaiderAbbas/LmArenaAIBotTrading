"""Operations domain: audit trail, system events, notifications, integrations."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import Boolean, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class AuditLog(Base):
    """Append-only audit trail. Never updated, never deleted."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(20), default="success", nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (Index("ix_audit_logs_created_at", "created_at"),)


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    category: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=20), default=Severity.INFO, nullable=False
    )
    message: Mapped[str] = mapped_column(String(400), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False)


class Notification(Base, TimestampMixin):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(40), default="system", nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class IntegrationHealth(Base, TimestampMixin):
    __tablename__ = "integration_health"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="not_configured", nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    message: Mapped[str] = mapped_column(String(255), default="", nullable=False)
