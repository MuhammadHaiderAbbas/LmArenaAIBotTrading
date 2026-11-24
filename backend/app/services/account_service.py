"""Trading account lifecycle."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import AccountStatus, Environment, TradingAccount
from app.models.identity import User
from app.services import ledger_service
from app.services.errors import DomainError, NotFoundError


def list_accounts(db: Session, user: User) -> list[TradingAccount]:
    return list(
        db.scalars(
            select(TradingAccount)
            .where(TradingAccount.user_id == user.id)
            .order_by(TradingAccount.created_at.asc())
        )
    )


def get_account(
    db: Session, account_id: str, user: User, *, allow_admin: bool = True
) -> TradingAccount:
    account = db.get(TradingAccount, account_id)
    if account is None:
        raise NotFoundError("Trading account not found.")
    is_admin = allow_admin and user.role.value == "admin"
    if account.user_id != user.id and not is_admin:
        raise NotFoundError("Trading account not found.")
    return account


def get_primary_account(db: Session, user: User) -> TradingAccount | None:
    return db.scalar(
        select(TradingAccount)
        .where(TradingAccount.user_id == user.id, TradingAccount.status == AccountStatus.ACTIVE)
        .order_by(TradingAccount.created_at.asc())
    )


def create_account(
    db: Session,
    *,
    user: User,
    name: str,
    base_currency: str = "GBP",
    opening_balance: float = 100_000.0,
) -> TradingAccount:
    # Phase 1 guardrail: paper trading only (blueprint section 18, principle 2).
    environment = Environment.PAPER
    duplicate = db.scalar(
        select(TradingAccount).where(TradingAccount.user_id == user.id, TradingAccount.name == name)
    )
    if duplicate is not None:
        raise DomainError(f"You already have an account named '{name}'.", status_code=409)

    account = TradingAccount(
        user_id=user.id,
        name=name,
        environment=environment,
        base_currency=base_currency.upper(),
        status=AccountStatus.ACTIVE,
        leverage_enabled=False,
    )
    db.add(account)
    db.flush()

    ledger_service.ensure_ledger_accounts(db, account)
    ledger_service.open_account_with_funding(
        db, account=account, amount=opening_balance, actor_id=user.id
    )
    ledger_service.reconcile(db, account)
    return account


def ensure_default_account(db: Session, user: User, opening_balance: float) -> TradingAccount:
    existing = get_primary_account(db, user)
    if existing is not None:
        return existing
    return create_account(
        db,
        user=user,
        name="Paper Trading Account",
        base_currency=user.preferences.base_currency if user.preferences else "GBP",
        opening_balance=opening_balance,
    )
