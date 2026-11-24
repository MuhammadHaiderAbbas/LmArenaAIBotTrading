"""Phase 12 endpoints: broker sandbox, compliance assessment, release gate.

Nothing here can place a real order or move real money. `/readiness/release/gate` is the
endpoint an operator would call before shipping; `/readiness/release/advance` refuses the
live stage by design.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.services import compliance, release
from app.services.broker import base as broker_base
from app.services.broker import sandbox
from app.services.broker.base import SandboxOrder

router = APIRouter(tags=["readiness"])


class BrokerConnectRequest(BaseModel):
    broker: str = Field(min_length=1, max_length=32)
    api_key: str = Field(min_length=1, max_length=256)
    api_secret: str = Field(min_length=1, max_length=512)
    mode: str = "sandbox"
    withdraw_allowed: bool = False
    scopes: str = ""
    ip_allowlist: str = ""
    account_id: str | None = None


class SandboxOrderRequest(BaseModel):
    broker: str = Field(min_length=1, max_length=32)
    client_order_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=32)
    side: str = Field(pattern="^(buy|sell)$")
    quantity: float = Field(gt=0)
    order_type: str = "market"


# ------------------------------------------------------------------- brokers
@router.get("/brokers")
def list_brokers(user: CurrentUser) -> dict:
    """The catalogue, including the plain statement that no live adapter exists."""
    return {
        "brokers": broker_base.available_brokers(),
        "live_trading": broker_base.live_trading_is_impossible(),
    }


@router.get("/brokers/connections")
def list_connections(db: DbSession, user: CurrentUser) -> dict:
    return {"connections": sandbox.list_connections(db, user=user)}


@router.post("/brokers/connect")
def connect_broker(payload: BrokerConnectRequest, db: DbSession, user: CurrentUser) -> dict:
    """Sandbox handshake. `mode="live"` is refused before any credential is stored."""
    result = sandbox.connect(
        db,
        user=user,
        broker=payload.broker,
        api_key=payload.api_key,
        api_secret=payload.api_secret,
        mode=payload.mode,
        withdraw_allowed=payload.withdraw_allowed,
        scopes=payload.scopes,
        ip_allowlist=payload.ip_allowlist,
        account_id=payload.account_id,
    )
    db.commit()
    return result


@router.post("/brokers/{broker}/probe")
def probe_broker(broker: str, db: DbSession, user: CurrentUser) -> dict:
    result = sandbox.probe(db, user=user, broker=broker)
    db.commit()
    return result


@router.post("/brokers/{broker}/disconnect")
def disconnect_broker(broker: str, db: DbSession, user: CurrentUser) -> dict:
    result = sandbox.disconnect(db, user=user, broker=broker)
    db.commit()
    return result


@router.post("/brokers/sandbox-orders")
def submit_sandbox_order(payload: SandboxOrderRequest, db: DbSession, user: CurrentUser) -> dict:
    """Send an order to a sandbox venue. It will not fill and no money will move."""
    result = sandbox.submit_to_sandbox(
        db,
        user=user,
        broker=payload.broker,
        order=SandboxOrder(
            client_order_id=payload.client_order_id,
            symbol=payload.symbol,
            side=payload.side,
            quantity=payload.quantity,
            order_type=payload.order_type,
        ),
    )
    db.commit()
    return result


# ----------------------------------------------------------------- readiness
@router.get("/readiness/compliance")
def compliance_assessment(db: DbSession, staff: StaffUser) -> dict:
    """Every §13 category, resolved from evidence. Staff-only: it names gaps."""
    return compliance.assessment(db)


@router.get("/readiness/compliance/summary")
def compliance_summary(db: DbSession, staff: StaffUser) -> dict:
    return compliance.checklist(db)


@router.get("/readiness/stages")
def stage_report(db: DbSession, user: CurrentUser) -> dict:
    return release.stage_report(db)


@router.get("/readiness/release/gate")
def release_gate(db: DbSession, staff: StaffUser) -> dict:
    """Would the current paper-trading release pass its own gate?"""
    return release.evaluate(db)


@router.post("/readiness/release/advance")
def advance_stage(payload: dict, db: DbSession, staff: StaffUser) -> dict:
    """Attempt a stage transition. `stage_3_live` is refused, by design."""
    target = (payload or {}).get("stage", "")
    return release.advance_to(db, user=staff, target=target)
