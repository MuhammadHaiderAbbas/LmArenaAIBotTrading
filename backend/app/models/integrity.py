"""The hash-chain seal for the audit trail.

One table, one row per sealed audit entry. Kept separate from `audit_logs` so that
deleting an audit row leaves an orphaned seal behind — which is exactly how a deletion
becomes visible.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class AuditSeal(Base, TimestampMixin):
    """One link in the chain.

    `sequence` is contiguous from 1; a gap means rows were removed. `prev_hash` must
    equal the previous seal's `entry_hash`, and `entry_hash` is the sha256 of the
    previous hash plus the canonical form of the audit entry.
    """

    __tablename__ = "audit_seals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    audit_log_id: Mapped[str] = mapped_column(
        ForeignKey("audit_logs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
