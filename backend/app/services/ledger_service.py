"""Double-entry ledger service.

Accounting rules implemented here (blueprint section 7):

  Event              Debit                   Credit
  -----------------  ----------------------  ----------------------
  Virtual opening    Cash asset              Opening equity
  Virtual deposit    Cash asset              Opening equity
  Virtual withdrawal Withdrawal clearing     Cash asset
  Buy asset          Investment asset        Cash asset
  Sell asset         Cash asset              Investment asset
  Trading fee        Fee expense             Cash asset
  Realised profit    Cash asset              Realised P&L income

Sign convention: `LedgerEntry.amount` is positive for a debit, negative for a
credit. Every transaction must sum to exactly zero.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import AccountBalance, TradingAccount
from app.models.ledger import (
    LedgerAccount,
    LedgerAccountKind,
    LedgerAccountType,
    LedgerEntry,
    LedgerTransaction,
    ReconciliationRecord,
    TransactionType,
)
from app.services.errors import DomainError

CENT = Decimal("0.00000001")

# kind -> (account_type, normal balance sign)
ACCOUNT_SPEC: dict[LedgerAccountKind, tuple[LedgerAccountType, str]] = {
    LedgerAccountKind.CASH: (LedgerAccountType.ASSET, "debit"),
    LedgerAccountKind.INVESTMENT: (LedgerAccountType.ASSET, "debit"),
    LedgerAccountKind.WITHDRAWAL_CLEARING: (LedgerAccountType.LIABILITY, "credit"),
    LedgerAccountKind.OPENING_EQUITY: (LedgerAccountType.EQUITY, "credit"),
    LedgerAccountKind.REALISED_PNL: (LedgerAccountType.INCOME, "credit"),
    LedgerAccountKind.FEE_EXPENSE: (LedgerAccountType.EXPENSE, "debit"),
}


def _dec(value: float | Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


# --------------------------------------------------------------------------
# Account provisioning
# --------------------------------------------------------------------------
def ensure_ledger_accounts(db: Session, account: TradingAccount) -> dict[LedgerAccountKind, LedgerAccount]:
    existing = {
        la.kind: la
        for la in db.scalars(
            select(LedgerAccount).where(LedgerAccount.trading_account_id == account.id)
        )
    }
    created = False
    for kind, (account_type, _) in ACCOUNT_SPEC.items():
        if kind in existing:
            continue
        ledger_account = LedgerAccount(
            trading_account_id=account.id,
            code=f"{account.base_currency}:{kind.value}",
            kind=kind,
            account_type=account_type,
            currency=account.base_currency,
        )
        db.add(ledger_account)
        existing[kind] = ledger_account
        created = True
    if created:
        db.flush()
    return existing


# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------
def post_transaction(
    db: Session,
    *,
    account: TradingAccount,
    transaction_type: TransactionType,
    lines: list[tuple[LedgerAccountKind, float | Decimal]],
    description: str = "",
    reference: str | None = None,
    idempotency_key: str | None = None,
    actor_id: str | None = None,
    occurred_at: datetime | None = None,
) -> LedgerTransaction:
    """Post a balanced transaction. Raises DomainError if it does not balance."""
    if idempotency_key:
        existing = db.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            return existing

    ledger_accounts = ensure_ledger_accounts(db, account)
    lines = [(kind, _dec(amount)) for kind, amount in lines]
    total = sum((amount for _, amount in lines), Decimal("0"))
    if total != 0:
        raise DomainError(
            f"Unbalanced ledger transaction: debits and credits differ by {total}."
        )
    if len(lines) < 2:
        raise DomainError("A ledger transaction requires at least two lines.")

    now = occurred_at or datetime.now(UTC)
    txn = LedgerTransaction(
        trading_account_id=account.id,
        transaction_type=transaction_type,
        reference=reference,
        description=description,
        idempotency_key=idempotency_key,
        occurred_at=now,
        created_at=now,
        created_by=actor_id,
    )
    db.add(txn)
    db.flush()

    for kind, amount in lines:
        db.add(
            LedgerEntry(
                transaction_id=txn.id,
                ledger_account_id=ledger_accounts[kind].id,
                amount=amount,
                currency=account.base_currency,
            )
        )
    db.flush()
    refresh_balance_cache(db, account)
    return txn


# --------------------------------------------------------------------------
# Derived balances
# --------------------------------------------------------------------------
def ledger_cash(db: Session, account: TradingAccount) -> Decimal:
    """Cash balance derived purely from ledger entries on the CASH account."""
    stmt = (
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.trading_account_id == account.id,
            LedgerAccount.kind == LedgerAccountKind.CASH,
        )
    )
    return _dec(db.scalar(stmt) or 0)


def trial_balance(db: Session, account: TradingAccount) -> Decimal:
    stmt = (
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(LedgerTransaction.trading_account_id == account.id)
    )
    return _dec(db.scalar(stmt) or 0)


def refresh_balance_cache(db: Session, account: TradingAccount) -> AccountBalance:
    cash = ledger_cash(db, account)
    balance = db.scalar(
        select(AccountBalance).where(
            AccountBalance.trading_account_id == account.id,
            AccountBalance.currency == account.base_currency,
        )
    )
    if balance is None:
        balance = AccountBalance(
            trading_account_id=account.id,
            currency=account.base_currency,
            cash=0,
            reserved=0,
            equity=0,
        )
        db.add(balance)
    balance.cash = cash
    balance.equity = cash - _dec(balance.reserved or 0)
    db.flush()
    return balance


def reconcile(db: Session, account: TradingAccount) -> ReconciliationRecord:
    """Compare ledger-derived cash against the cached balance row."""
    cash = ledger_cash(db, account)
    cached = db.scalar(
        select(AccountBalance).where(
            AccountBalance.trading_account_id == account.id,
            AccountBalance.currency == account.base_currency,
        )
    )
    cached_cash = _dec(cached.cash) if cached else Decimal("0")
    record = ReconciliationRecord(
        trading_account_id=account.id,
        currency=account.base_currency,
        ledger_cash=cash,
        cached_cash=cached_cash,
        is_balanced=cash == cached_cash,
        notes="Ledger and cached balance agree." if cash == cached_cash else "Mismatch detected.",
    )
    db.add(record)
    db.flush()
    return record


# --------------------------------------------------------------------------
# Business movements
# --------------------------------------------------------------------------
def open_account_with_funding(
    db: Session, *, account: TradingAccount, amount: float, actor_id: str | None = None
) -> LedgerTransaction:
    value = _dec(amount)
    return post_transaction(
        db,
        account=account,
        transaction_type=TransactionType.OPENING_FUNDING,
        lines=[
            (LedgerAccountKind.CASH, value),
            (LedgerAccountKind.OPENING_EQUITY, -value),
        ],
        description=f"Virtual opening balance of {account.base_currency} {value:,.2f}",
        reference=f"acct:{account.id}",
        idempotency_key=f"opening:{account.id}",
        actor_id=actor_id,
    )


def virtual_deposit(
    db: Session, *, account: TradingAccount, amount: float, description: str = "", actor_id: str | None = None
) -> LedgerTransaction:
    if account.environment.value != "paper":
        raise DomainError("Virtual funding is only available in the paper environment.")
    value = _dec(amount)
    return post_transaction(
        db,
        account=account,
        transaction_type=TransactionType.DEPOSIT,
        lines=[
            (LedgerAccountKind.CASH, value),
            (LedgerAccountKind.OPENING_EQUITY, -value),
        ],
        description=description or f"Virtual deposit of {account.base_currency} {value:,.2f}",
        actor_id=actor_id,
    )


def virtual_withdrawal(
    db: Session, *, account: TradingAccount, amount: float, description: str = "", actor_id: str | None = None
) -> LedgerTransaction:
    if account.environment.value != "paper":
        raise DomainError("Virtual withdrawals are only available in the paper environment.")
    value = _dec(amount)
    available = ledger_cash(db, account)
    if value > available:
        raise DomainError(
            f"Insufficient virtual funds: requested {value:,.2f}, available {available:,.2f}."
        )
    return post_transaction(
        db,
        account=account,
        transaction_type=TransactionType.WITHDRAWAL,
        lines=[
            (LedgerAccountKind.WITHDRAWAL_CLEARING, value),
            (LedgerAccountKind.CASH, -value),
        ],
        description=description or f"Virtual withdrawal of {account.base_currency} {value:,.2f}",
        actor_id=actor_id,
    )
