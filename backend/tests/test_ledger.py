"""Double-entry ledger invariants — the financial core must never drift."""

from __future__ import annotations

import pytest

from app.core.security import hash_password, verify_password
from app.database.session import SessionLocal
from app.models.account import Environment, TradingAccount
from app.models.identity import User, UserRole
from app.models.ledger import LedgerAccountKind, TransactionType
from app.services import ledger_service
from app.services.errors import DomainError


@pytest.fixture
def account() -> TradingAccount:
    db = SessionLocal()
    try:
        user = User(
            email="ledger@aurex.ai",
            full_name="Ledger Tester",
            password_hash=hash_password("Str0ngPass!"),
            role=UserRole.USER,
        )
        db.add(user)
        db.flush()
        trading = TradingAccount(
            user_id=user.id,
            name="Ledger Account",
            environment=Environment.PAPER,
            base_currency="GBP",
        )
        db.add(trading)
        db.flush()
        ledger_service.ensure_ledger_accounts(db, trading)
        ledger_service.open_account_with_funding(db, account=trading, amount=10_000)
        db.commit()
        db.refresh(trading)
        trading_id = trading.id
    finally:
        db.close()

    session = SessionLocal()
    try:
        return session.get(TradingAccount, trading_id)
    finally:
        session.close()


def test_opening_funding_creates_expected_entries(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        assert float(ledger_service.ledger_cash(db, refreshed)) == 10_000.0
        accounts = ledger_service.ensure_ledger_accounts(db, refreshed)
        assert set(accounts) == set(LedgerAccountKind)
    finally:
        db.close()


def test_unbalanced_transaction_is_rejected(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        with pytest.raises(DomainError, match="Unbalanced"):
            ledger_service.post_transaction(
                db,
                account=refreshed,
                transaction_type=TransactionType.ADJUSTMENT,
                lines=[(LedgerAccountKind.CASH, 100), (LedgerAccountKind.OPENING_EQUITY, -90)],
            )
    finally:
        db.close()


def test_single_line_transaction_is_rejected(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        with pytest.raises(DomainError, match="at least two lines"):
            ledger_service.post_transaction(
                db,
                account=refreshed,
                transaction_type=TransactionType.ADJUSTMENT,
                lines=[(LedgerAccountKind.CASH, 0)],
            )
    finally:
        db.close()


def test_idempotency_key_prevents_double_posting(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        first = ledger_service.virtual_deposit(
            db, account=refreshed, amount=500, description="dup test"
        )
        db.flush()
        second = ledger_service.post_transaction(
            db,
            account=refreshed,
            transaction_type=TransactionType.DEPOSIT,
            lines=[(LedgerAccountKind.CASH, 500), (LedgerAccountKind.OPENING_EQUITY, -500)],
            idempotency_key="deposit:dup-test:1",
        )
        assert second.id != first.id  # a fresh key posts a new transaction
        assert float(ledger_service.ledger_cash(db, refreshed)) == 11_000.0

        # Opening funding is keyed on the account id: replaying it must be a
        # no-op that returns the original transaction, never a second posting.
        replay = ledger_service.open_account_with_funding(db, account=refreshed, amount=10_000)
        assert replay.transaction_type.value == "opening_funding"
        assert replay.id != second.id
        assert float(ledger_service.ledger_cash(db, refreshed)) == 11_000.0
    finally:
        db.close()


def test_withdrawal_never_drives_cash_negative(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        with pytest.raises(DomainError):
            ledger_service.virtual_withdrawal(db, account=refreshed, amount=10_000.01)
        assert float(ledger_service.ledger_cash(db, refreshed)) == 10_000.0
    finally:
        db.close()


def test_every_transaction_sums_to_zero(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        ledger_service.virtual_deposit(db, account=refreshed, amount=250)
        ledger_service.virtual_withdrawal(db, account=refreshed, amount=100)
        assert float(ledger_service.trial_balance(db, refreshed)) == 0.0
    finally:
        db.close()


def test_reconciliation_detects_cached_mismatch(account: TradingAccount) -> None:
    db = SessionLocal()
    try:
        refreshed = db.get(TradingAccount, account.id)
        record = ledger_service.reconcile(db, refreshed)
        assert record.is_balanced is True

        balance = ledger_service.refresh_balance_cache(db, refreshed)
        balance.cash = 1  # simulate corruption of the cache only
        db.flush()
        mismatch = ledger_service.reconcile(db, refreshed)
        assert mismatch.is_balanced is False
    finally:
        db.close()


def test_password_hashing_roundtrip() -> None:
    stored = hash_password("Str0ngPass!")
    assert stored.startswith("pbkdf2_sha256$")
    assert verify_password("Str0ngPass!", stored) is True
    assert verify_password("WrongPass!", stored) is False
    assert verify_password("Str0ngPass!", "garbage") is False
