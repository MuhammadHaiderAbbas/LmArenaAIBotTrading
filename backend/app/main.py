"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import api_router
from app.core import release
from app.core.config import settings
from app.services.errors import DomainError

logger = logging.getLogger("aurex.api")


def _seal_legacy_audit() -> None:
    """Adopt audit rows written before the hash chain existed.

    Deliberately best-effort: an unmigrated or unreachable database must not stop the
    process from booting. Failures are logged and the chain reports them honestly later.
    """
    from app.database.session import SessionLocal
    from app.services import integrity

    db = SessionLocal()
    try:
        sealed = integrity.seal_pending(db)
        db.commit()
        if sealed:
            logger.info("Adopted %s pre-existing audit entr(y/ies) into the hash chain", sealed)
    except Exception:  # noqa: BLE001 - startup must survive a cold database
        db.rollback()
        logger.exception("Audit-chain adoption skipped; /operations/audit/verify will report it")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "AUREX AI starting | env=%s | live_trading=%s | real_money=%s",
        settings.ENVIRONMENT,
        settings.LIVE_TRADING_ENABLED,
        settings.REAL_MONEY_ENABLED,
    )
    _seal_legacy_audit()
    yield
    logger.info("AUREX AI shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title=f"{settings.PROJECT_NAME} API",
        description=(
            "Autonomous trading and financial intelligence platform.\n\n"
            f"**{release.PHASE_NAME}.** Paper trading only. No real money, no live "
            "broker execution, no profit claims."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_origin_regex=r"https://.*\.(e2b\.app|vercel\.app|netlify\.app)$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if not settings.DEBUG:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message, **exc.extra}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"][1:]) or "body"
            message = error["msg"].replace("Value error, ", "")
            errors.append({"field": location, "message": message})
        return JSONResponse(
            status_code=422,
            content={
                "detail": errors[0]["message"] if errors else "Invalid request.",
                "errors": errors,
            },
        )

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/", tags=["system"])
    def root() -> dict[str, object]:
        return {
            **release.identity(),
            "docs": "/docs",
            "health": f"{settings.API_V1_PREFIX}/system/health",
            "openapi": "/openapi.json",
        }

    return app


app = create_app()
