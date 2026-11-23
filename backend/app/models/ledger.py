"""Double-entry ledger.

Every movement of value is a `LedgerTransaction` containing two or more
`LedgerEntry` lines whose signed amounts sum to zero. Corrections are made with
compensating transactions; rows are never edited or deleted.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin
from app.models.identity import _uuid

if TYPE_CHECKING:
    from app.models.account import TradingAccount


class LedgerAccountType(str, enum.Enum):
    ASSET = "asset"          # cash held for the account
    LIABILITY = "liability"  # withdrawal clearing
    EQUITY = "equity"        # opening equity / P&L attributable to owner
    INCOME = "income"        # realised trading profit
    EXPENSE = "expense"      # fees


class LedgerAccountKind(str, enum.Enum):
    CASH = "cash"
    INVESTMENT = "investment"
    OPENING_EQUITY = "opening_equity"
    WITHDRAWAL_CLEARING = "withdrawal_clearing"
    FEE_EXPENSE = "fee_expense"
    REALISED_PNL = "realised_pnl"


class TransactionType(str, enum.Enum):
    OPENING_FUNDING = "opening_funding"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRADE_BUY = "trade_buy"
    TRADE_SELL = "trade_sell"
    FEE = "fee"
    ADJUSTMENT = "adjustment"


class LedgerAccount(Base, TimestampMixin):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint("trading_account_id", "kind", "currency", name="uq_ledger_account_kind"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    kind: Mapped[LedgerAccountKind] = mapped_column(
        Enum(LedgerAccountKind, native_enum=False, length=30), nullable=False
    )
    account_type: Mapped[LedgerAccountType] = mapped_column(
        Enum(LedgerAccountType, native_enum=False, length=20), nullable=False
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    trading_account: Mapped[TradingAccount] = relationship(back_populates="ledger_accounts")
    entries: Mapped[list[LedgerEntry]] = relationship(back_populates="ledger_account")


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), index=True
    )
    transaction_type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, native_enum=False, length=30), nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    entries: Mapped[list[LedgerEntry]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan", lazy="selectin"
    )


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    transaction_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="CASCADE"), index=True
    )
    ledger_account_id: Mapped[str] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), index=True
    )
    # Signed amount: positive = debit, negative = credit.
    amount: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    transaction: Mapped[LedgerTransaction] = relationship(back_populates="entries")
    ledger_account: Mapped[LedgerAccount] = relationship(back_populates="entries")


class ReconciliationRecord(Base, TimestampMixin):
    __tablename__ = "reconciliation_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    ledger_cash: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    cached_cash: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    is_balanced: Mapped[bool] = mapped_column(nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
