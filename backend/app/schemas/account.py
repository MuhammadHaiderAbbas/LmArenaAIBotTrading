"""Pydantic schemas for accounts, balances and ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.account import AccountStatus, Environment
from app.models.ledger import LedgerAccountKind, LedgerAccountType, TransactionType


class AccountCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    base_currency: str = Field(default="GBP", min_length=3, max_length=3)
    # Paper is the only environment a user may open in Phase 1.
    environment: Literal["paper"] = "paper"
    opening_balance: float = Field(default=100_000.00, gt=0, le=10_000_000)


class BalanceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    currency: str
    cash: float
    reserved: float
    equity: float


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    environment: Environment
    status: AccountStatus
    base_currency: str
    leverage_enabled: bool
    created_at: datetime


class AccountDetail(AccountRead):
    balances: list[BalanceRead] = []
    open_positions: int = 0


class LedgerEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ledger_account_id: str
    amount: float
    currency: str


class LedgerAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    kind: LedgerAccountKind
    account_type: LedgerAccountType
    currency: str


class LedgerTransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    transaction_type: TransactionType
    reference: str | None
    description: str
    occurred_at: datetime
    created_at: datetime
    entries: list[LedgerEntryRead] = []


class LedgerStatement(BaseModel):
    account_id: str
    currency: str
    transactions: list[LedgerTransactionRead]
    total: int
    trial_balance: float


class DepositRequest(BaseModel):
    amount: float = Field(gt=0, le=1_000_000)
    description: str = Field(default="", max_length=160)


class WithdrawRequest(BaseModel):
    amount: float = Field(gt=0, le=1_000_000)
    description: str = Field(default="", max_length=160)


class ReconciliationRead(BaseModel):
    account_id: str
    currency: str
    ledger_cash: float
    cached_cash: float
    is_balanced: bool
    notes: str
