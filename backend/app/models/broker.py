"""Broker connections and the sandbox handshake ledger.

Two tables:

* `broker_connections` — one row per (account, broker) link. Credentials are stored
  **encrypted** (`app.core.secrets`), `withdraw_allowed` is recorded because the
  blueprint asks for least privilege and a key *with* withdrawal permission is a
  finding, and `mode` is deliberately constrained: `sandbox` is the only mode this
  codebase can put an order through. `live` exists in the enum so a future adapter has
  somewhere to land, and every code path that reads it treats it as "refuse".
* `broker_sessions` — the sandbox handshake: a connect request, its acknowledgement, a
  clock-sync probe and a disconnect. Kept so "we tested connectivity" is a row rather
  than a claim.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class BrokerMode(str):
    SANDBOX = "sandbox"
    LIVE = "live"


class BrokerConnection(Base, TimestampMixin):
    __tablename__ = "broker_connections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trading_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    broker: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Only "sandbox" is reachable from this codebase; see BrokerAdapter.get_connection.
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="sandbox")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="disconnected")
    api_key_encrypted: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_secret_encrypted: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Least-privilege auditing: the blueprint asks for withdrawal permission to be off.
    withdraw_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scopes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ip_allowlist: Mapped[str] = mapped_column(Text, nullable=False, default="")
    last_connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class BrokerSession(Base, TimestampMixin):
    """One sandbox handshake attempt, so connectivity is verifiable after the fact."""

    __tablename__ = "broker_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("broker_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(24), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    clock_skew_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
