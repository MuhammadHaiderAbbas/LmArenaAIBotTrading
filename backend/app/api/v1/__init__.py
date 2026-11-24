"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    admin,
    ai,
    analytics,
    auth,
    execution,
    markets,
    models,
    research,
    risk,
    strategies,
    system,
    users,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(accounts.router)
api_router.include_router(markets.router)
api_router.include_router(research.router)
api_router.include_router(ai.router)
api_router.include_router(models.router)
api_router.include_router(strategies.router)
api_router.include_router(execution.router)
api_router.include_router(risk.router)
api_router.include_router(analytics.router)
api_router.include_router(admin.router)
