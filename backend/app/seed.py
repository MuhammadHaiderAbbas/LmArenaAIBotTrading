"""Seed the AUREX AI database with an administrator and a demo paper trader.

Idempotent: safe to run on every container start.

    python -m app.seed
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import select

import app.models  # noqa: F401  registers mappers
from app.core.config import settings
from app.core.security import hash_password
from app.database.session import Base, SessionLocal, engine
from app.models import Environment, TradingAccount, User, UserRole
from app.models.identity import UserSettings
from app.services import account_service, ledger_service
from app.services.market import registry
from app.services.research import registry as research_registry

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger("aurex.seed")

DEMO_EMAIL = os.getenv("DEMO_USER_EMAIL", "demo@aurex.ai")
DEMO_PASSWORD = os.getenv("DEMO_USER_PASSWORD", "Demo!Aurex2026")


def _create_or_get_user(
    db, *, email: str, password: str, full_name: str, role: UserRole, balance: float
) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        logger.info("user %s already exists — skipping", email)
        return user
    user = User(
        email=email,
        full_name=full_name,
        password_hash=hash_password(password),
        role=role,
        is_active=True,
        is_verified=True,
    )
    user.preferences = UserSettings(base_currency=settings.BASE_CURRENCY)
    db.add(user)
    db.flush()
    account_service.create_account(
        db, user=user, name="Paper Trading Account", opening_balance=balance
    )
    logger.info("created %s (%s) with paper account funded at %s", email, role.value, balance)
    return user


def main() -> None:
    if os.getenv("AUREX_AUTO_CREATE_TABLES", "1") == "1":
        Base.metadata.create_all(bind=engine)
        logger.info("schema ensured")

    db = SessionLocal()
    try:
        _create_or_get_user(
            db,
            email=settings.FIRST_ADMIN_EMAIL,
            password=settings.FIRST_ADMIN_PASSWORD,
            full_name="AUREX Administrator",
            role=UserRole.ADMIN,
            balance=settings.DEFAULT_PAPER_BALANCE,
        )
        _create_or_get_user(
            db,
            email=DEMO_EMAIL,
            password=DEMO_PASSWORD,
            full_name="Demo Trader",
            role=UserRole.USER,
            balance=settings.DEFAULT_PAPER_BALANCE,
        )

        # A second, smaller account shows multi-account isolation in the UI.
        demo = db.scalar(select(User).where(User.email == DEMO_EMAIL))
        if demo and not db.scalar(
            select(TradingAccount).where(
                TradingAccount.user_id == demo.id, TradingAccount.name == "Strategy Sandbox"
            )
        ):
            sandbox = TradingAccount(
                user_id=demo.id,
                name="Strategy Sandbox",
                environment=Environment.PAPER,
                base_currency="USD",
            )
            db.add(sandbox)
            db.flush()
            ledger_service.ensure_ledger_accounts(db, sandbox)
            ledger_service.open_account_with_funding(db, account=sandbox, amount=25_000)
            ledger_service.reconcile(db, sandbox)
            logger.info("created demo Strategy Sandbox account (USD 25,000 paper)")

        # Phase 3: instrument registry is declarative and idempotent, so it is
        # reconciled on every start rather than created once.
        # Research sources are declared in code for the same reason instruments
        # are: a fresh database and an existing one must converge.
        research = research_registry.seed_sources(db)
        logger.info(
            "research sources: %s created, %s updated (%s total)",
            research["created"], research["updated"], research["total"],
        )

        created = registry.seed_registry(db)
        logger.info(
            "market registry: %s venues, %s instruments, %s provider mappings",
            created["venues"],
            created["instruments"],
            created["providers"],
        )

        db.commit()
        logger.info("seed complete")
    except Exception:  # pragma: no cover - startup path
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
