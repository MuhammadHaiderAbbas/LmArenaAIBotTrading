"""API v1 router aggregation."""

from fastapi import APIRouter

from app.api.v1 import accounts, admin, auth, markets, system, users

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(accounts.router)
api_router.include_router(markets.router)
api_router.include_router(admin.router)
