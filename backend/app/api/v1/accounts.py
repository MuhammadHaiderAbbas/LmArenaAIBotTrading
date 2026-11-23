"""Trading account, balance and ledger endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request, status

from app.api.deps import CurrentUser, DbSession
from app.core.audit import record_audit
from app.models.ledger import LedgerTransaction
from app.schemas.account import (
    AccountCreate,
    AccountDetail,
    AccountRead,
    BalanceRead,
    DepositRequest,
    LedgerAccountRead,
    LedgerStatement,
    LedgerTransactionRead,
    ReconciliationRead,
    WithdrawRequest,
)
from app.services import account_service, ledger_service
from sqlalchemy import select

router = APIRouter(tags=["accounts"])


def _detail(db: DbSession, account) -> AccountDetail:
    balances = ledger_service.refresh_balance_cache(db, account)
    return AccountDetail(
        **AccountRead.model_validate(account).model_dump(),
        balances=[BalanceRead.model_validate(balances)],
        open_positions=0,  # populated from Phase 8 onwards
    )


@router.get("/accounts", response_model=list[AccountRead])
def list_accounts(db: DbSession, user: CurrentUser) -> list[AccountRead]:
    return [AccountRead.model_validate(a) for a in account_service.list_accounts(db, user)]


@router.post("/accounts", response_model=AccountDetail, status_code=status.HTTP_201_CREATED)
def create_account(
    payload: AccountCreate, request: Request, db: DbSession, user: CurrentUser
) -> AccountDetail:
    account = account_service.create_account(
        db,
        user=user,
        name=payload.name,
        base_currency=payload.base_currency,
        opening_balance=payload.opening_balance,
    )
    record_audit(
        db,
        action="account.create",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="trading_account",
        entity_id=account.id,
        detail=f"paper account '{account.name}' funded with {payload.opening_balance:,.2f} {account.base_currency}",
        request=request,
    )
    db.commit()
    db.refresh(account)
    return _detail(db, account)


@router.get("/accounts/{account_id}", response_model=AccountDetail)
def get_account(account_id: str, db: DbSession, user: CurrentUser) -> AccountDetail:
    account = account_service.get_account(db, account_id, user)
    detail = _detail(db, account)
    db.commit()
    return detail


@router.get("/accounts/{account_id}/ledger", response_model=LedgerStatement)
def get_ledger(
    account_id: str,
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> LedgerStatement:
    account = account_service.get_account(db, account_id, user)
    stmt = (
        select(LedgerTransaction)
        .where(LedgerTransaction.trading_account_id == account.id)
        .order_by(LedgerTransaction.occurred_at.desc())
        .limit(limit)
        .offset(offset)
    )
    transactions = list(db.scalars(stmt))
    total = len(list(db.scalars(
        select(LedgerTransaction.id).where(LedgerTransaction.trading_account_id == account.id)
    )))
    return LedgerStatement(
        account_id=account.id,
        currency=account.base_currency,
        transactions=[LedgerTransactionRead.model_validate(t) for t in transactions],
        total=total,
        trial_balance=float(ledger_service.trial_balance(db, account)),
    )


@router.get("/accounts/{account_id}/ledger/accounts", response_model=list[LedgerAccountRead])
def get_ledger_accounts(account_id: str, db: DbSession, user: CurrentUser) -> list[LedgerAccountRead]:
    account = account_service.get_account(db, account_id, user)
    ledger_accounts = ledger_service.ensure_ledger_accounts(db, account)
    db.commit()
    return [LedgerAccountRead.model_validate(a) for a in ledger_accounts.values()]


@router.post("/accounts/{account_id}/deposit", response_model=AccountDetail)
def deposit(
    account_id: str,
    payload: DepositRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> AccountDetail:
    account = account_service.get_account(db, account_id, user)
    ledger_service.virtual_deposit(
        db, account=account, amount=payload.amount, description=payload.description, actor_id=user.id
    )
    record_audit(
        db,
        action="wallet.virtual_deposit",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="trading_account",
        entity_id=account.id,
        detail=f"virtual deposit {payload.amount:,.2f} {account.base_currency}",
        request=request,
    )
    db.commit()
    db.refresh(account)
    return _detail(db, account)


@router.post("/accounts/{account_id}/withdraw", response_model=AccountDetail)
def withdraw(
    account_id: str,
    payload: WithdrawRequest,
    request: Request,
    db: DbSession,
    user: CurrentUser,
) -> AccountDetail:
    account = account_service.get_account(db, account_id, user)
    ledger_service.virtual_withdrawal(
        db, account=account, amount=payload.amount, description=payload.description, actor_id=user.id
    )
    record_audit(
        db,
        action="wallet.virtual_withdrawal",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="trading_account",
        entity_id=account.id,
        detail=f"virtual withdrawal {payload.amount:,.2f} {account.base_currency}",
        request=request,
    )
    db.commit()
    db.refresh(account)
    return _detail(db, account)


@router.post("/accounts/{account_id}/reconcile", response_model=ReconciliationRead)
def reconcile(account_id: str, db: DbSession, user: CurrentUser) -> ReconciliationRead:
    account = account_service.get_account(db, account_id, user)
    record = ledger_service.reconcile(db, account)
    db.commit()
    return ReconciliationRead(
        account_id=account.id,
        currency=record.currency,
        ledger_cash=float(record.ledger_cash),
        cached_cash=float(record.cached_cash),
        is_balanced=record.is_balanced,
        notes=record.notes,
    )
