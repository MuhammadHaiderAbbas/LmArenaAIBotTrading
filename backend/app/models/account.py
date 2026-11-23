"""Account domain: trading accounts and derived balances."""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin
from app.models.identity import _uuid

if TYPE_CHECKING:
    from app.models.identity import User
    from app.models.ledger import LedgerAccount


class Environment(str, enum.Enum):
    """The single most important label in the UI (blueprint section 8)."""

    RESEARCH = "research"
    PAPER = "paper"
    LIVE = "live"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CLOSED = "closed"


class TradingAccount(Base, TimestampMixin):
    __tablename__ = "trading_accounts"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_account_user_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    environment: Mapped[Environment] = mapped_column(
        Enum(Environment, native_enum=False, length=20),
        default=Environment.PAPER,
        nullable=False,
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, native_enum=False, length=20),
        default=AccountStatus.ACTIVE,
        nullable=False,
    )
    base_currency: Mapped[str] = mapped_column(String(3), default="GBP", nullable=False)
    leverage_enabled: Mapped[bool] = mapped_column(default=False, nullable=False)

    user: Mapped["User"] = relationship(back_populates="accounts")
    ledger_accounts: Mapped[list["LedgerAccount"]] = relationship(
        back_populates="trading_account", cascade="all, delete-orphan"
    )


class AccountBalance(Base, TimestampMixin):
    """Cached, ledger-derived balance snapshot.

    This row is a materialised view of `ledger_entries` for fast reads. It is
    always recalculated from the ledger by `ledger_service`, never edited
    directly (blueprint section 7).
    """

    __tablename__ = "account_balances"
    __table_args__ = (
        UniqueConstraint("trading_account_id", "currency", name="uq_balance_account_currency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    cash: Mapped[float] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    reserved: Mapped[float] = mapped_column(Numeric(20, 8), default=0, nullable=False)
    equity: Mapped[float] = mapped_column(Numeric(20, 8), default=0, nullable=False)
