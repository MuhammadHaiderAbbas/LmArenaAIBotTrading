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
from app.services.ai import orchestrator as ai_orchestrator
from app.services.market import ingestion, registry
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


def _seed_synthetic_market_data(db) -> dict:
    """Ingest deterministic offline bars so a fresh install is not empty.

    Opt-in, and loud about it: `AUREX_SEED_SYNTHETIC_DATA=1`. Without it this function
    does nothing and the platform reports its own staleness — the correct behaviour for
    an install whose data feeds have not run yet.

    Provider mappings for the real feeds are disabled for the duration of this step, so
    the result is *only* synthetic. Overlapping synthetic and live bars in one series
    would be worse than either: a chart that quietly mixes a real quote with a generated
    one cannot be reasoned about.
    """
    from app.integrations.providers.synthetic import SYNTHETIC_NOTICE, SyntheticProvider
    from app.models.market import Instrument, InstrumentProvider, Timeframe

    ingestion.register_opt_in("synthetic", SyntheticProvider())
    logger.warning(SYNTHETIC_NOTICE)

    # The registry keeps `is_tradable` false on purpose (it describes what exists, not
    # what may be traded), so ingestion targets active instruments, not tradable ones.
    # Status is stored as the enum *name* (`ACTIVE`), not its value.
    instruments = list(db.scalars(select(Instrument).where(Instrument.status == "ACTIVE")))
    if not instruments:
        logger.warning("no active instruments; run the registry seed first")
        return {"synced": 0, "failed": 0, "provider": "synthetic", "is_real_data": False}

    # Silence the real feeds while this step runs, and remember what to restore.
    disabled: list[InstrumentProvider] = []
    for mapping in db.scalars(
        select(InstrumentProvider).where(InstrumentProvider.provider != "synthetic")
    ):
        if mapping.is_enabled:
            mapping.is_enabled = False
            disabled.append(mapping)
    db.flush()

    for instrument in instruments:
        existing = db.scalar(
            select(InstrumentProvider).where(
                InstrumentProvider.instrument_id == instrument.id,
                InstrumentProvider.provider == "synthetic",
            )
        )
        if existing is None:
            db.add(
                InstrumentProvider(
                    instrument_id=instrument.id,
                    provider="synthetic",
                    provider_symbol=instrument.symbol,
                    priority=99,
                    is_enabled=True,
                )
            )
    db.flush()
    synced = 0
    failed = 0
    errors: list[str] = []
    for instrument in instruments:
        db.refresh(instrument)
        result = ingestion.sync_candles(db, instrument, Timeframe.D1, limit=120)
        if result.error:
            failed += 1
            errors.append(f"{instrument.symbol}: {result.error}")
        else:
            synced += 1

    # Restore the real provider mappings. A synthetic install is a temporary state.
    for mapping in disabled:
        mapping.is_enabled = True
    db.flush()

    logger.info("synthetic market data: %s instrument(s) synced, %s failed", synced, failed)
    for error in errors[:5]:
        logger.warning("  %s", error)
    return {"synced": synced, "failed": failed, "provider": "synthetic", "is_real_data": False}


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
        # Agent metadata is declared in code so the UI can distinguish built
        # specialists from planned ones rather than showing a static list.
        agent_result = ai_orchestrator.seed_agents(db)
        logger.info(
            "ai agents: %s created, %s updated", agent_result["created"], agent_result["updated"]
        )

        # Research sources are declared in code for the same reason instruments
        # are: a fresh database and an existing one must converge.
        research = research_registry.seed_sources(db)
        logger.info(
            "research sources: %s created, %s updated (%s total)",
            research["created"],
            research["updated"],
            research["total"],
        )

        created = registry.seed_registry(db)
        logger.info(
            "market registry: %s venues, %s instruments, %s provider mappings",
            created["venues"],
            created["instruments"],
            created["providers"],
        )

        if os.getenv("AUREX_SEED_SYNTHETIC_DATA", "0") == "1":
            _seed_synthetic_market_data(db)
        else:
            logger.info(
                "no market data requested. Run with AUREX_SEED_SYNTHETIC_DATA=1 for offline "
                "development bars, or POST /markets/sync once providers are reachable."
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
