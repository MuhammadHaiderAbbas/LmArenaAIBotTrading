"""System endpoints: liveness, readiness and platform metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import text

from app.api.deps import DbSession
from app.core import release
from app.core.config import settings
from app.schemas.system import HealthResponse

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(db: DbSession) -> HealthResponse:
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # pragma: no cover - defensive
        database = "unavailable"

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        service=settings.PROJECT_NAME,
        version="0.1.0",
        environment=settings.ENVIRONMENT,
        database=database,
        live_trading_enabled=settings.LIVE_TRADING_ENABLED,
        real_money_enabled=settings.REAL_MONEY_ENABLED,
        timestamp=datetime.now(UTC),
    )


@router.get("/meta")
def meta() -> dict[str, object]:
    """Public metadata consumed by the frontend for environment labelling."""
    return {
        "name": settings.PROJECT_NAME,
        "version": "0.1.0",
        "phase": release.PHASE_NAME,
        "phases_complete": release.PHASES_COMPLETE,
        "environment": settings.ENVIRONMENT,
        "execution_mode": "paper",
        "live_trading_enabled": settings.LIVE_TRADING_ENABLED,
        "real_money_enabled": settings.REAL_MONEY_ENABLED,
        "base_currency": settings.BASE_CURRENCY,
    }
