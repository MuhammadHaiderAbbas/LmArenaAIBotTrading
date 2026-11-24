"""MFA: recovery codes.

A recovery code is a password that is used once. It is therefore stored exactly like
one — a sha256 digest, never the plaintext — and marked used the moment it is spent.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class MFARecoveryCode(Base, TimestampMixin):
    __tablename__ = "mfa_recovery_codes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
