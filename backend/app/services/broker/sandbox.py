"""Sandbox connection lifecycle: connect, probe, submit, disconnect.

Every stage writes a `BrokerSession` row, so "we tested broker connectivity" is a record
with a latency and a clock skew against it rather than a sentence in a document.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import secrets as secret_box
from app.core.audit import record_audit
from app.core.config import settings
from app.models.broker import BrokerConnection, BrokerSession
from app.models.identity import User
from app.services.broker.base import (
    BrokerError,
    SandboxOrder,
    adapter_for,
    live_trading_is_impossible,
    require_sandbox,
)
from app.services.errors import DomainError

MAX_CLOCK_SKEW_MS = 2_000.0
VALID_MODES = {"sandbox", "live"}


def _log(
    db: Session,
    connection: BrokerConnection,
    *,
    stage: str,
    ok: bool,
    detail: str,
    latency_ms: float | None = None,
    clock_skew_ms: float | None = None,
) -> BrokerSession:
    row = BrokerSession(
        connection_id=connection.id,
        stage=stage,
        ok=ok,
        detail=detail,
        latency_ms=latency_ms,
        clock_skew_ms=clock_skew_ms,
        occurred_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def get_or_create(
    db: Session,
    *,
    user: User,
    broker: str,
    account_id: str | None = None,
    label: str = "",
) -> BrokerConnection:
    connection = db.scalar(
        select(BrokerConnection).where(
            BrokerConnection.user_id == user.id, BrokerConnection.broker == broker.lower()
        )
    )
    if connection is not None:
        return connection
    connection = BrokerConnection(
        user_id=user.id,
        trading_account_id=account_id,
        broker=broker.lower(),
        label=label or f"{broker} (sandbox)",
        mode="sandbox",
        status="disconnected",
    )
    db.add(connection)
    db.flush()
    return connection


def connect(
    db: Session,
    *,
    user: User,
    broker: str,
    api_key: str,
    api_secret: str,
    mode: str = "sandbox",
    withdraw_allowed: bool = False,
    scopes: str = "",
    ip_allowlist: str = "",
    account_id: str | None = None,
) -> dict:
    """Run the sandbox handshake and store the credentials encrypted."""
    if mode not in VALID_MODES:
        raise DomainError(f"mode must be one of {sorted(VALID_MODES)}.", status_code=422)
    # The wall. Anything but sandbox stops here, before a credential is read.
    require_sandbox(mode=mode)

    try:
        adapter = adapter_for(broker)
    except BrokerError as exc:
        raise DomainError(str(exc), status_code=404) from exc

    connection = get_or_create(db, user=user, broker=broker, account_id=account_id)
    connection.mode = "sandbox"
    connection.withdraw_allowed = bool(withdraw_allowed)
    connection.scopes = scopes
    connection.ip_allowlist = ip_allowlist

    started = time.perf_counter()
    try:
        ack = adapter.handshake(api_key=api_key, api_secret=api_secret)
    except Exception as exc:  # noqa: BLE001 - adapters may fail in their own ways
        latency = (time.perf_counter() - started) * 1000
        connection.status = "error"
        connection.last_error = str(exc)
        _log(db, connection, stage="handshake", ok=False, detail=str(exc), latency_ms=latency)
        record_audit(
            db,
            action="broker.connect",
            actor_id=user.id,
            actor_email=user.email,
            outcome="failure",
            detail=f"{broker} handshake failed: {exc}",
        )
        db.flush()
        return _snapshot(db, connection)

    latency = (time.perf_counter() - started) * 1000
    if not ack.accepted:
        connection.status = "error"
        connection.last_error = ack.message
        _log(db, connection, stage="handshake", ok=False, detail=ack.message, latency_ms=latency)
        db.flush()
        return _snapshot(db, connection)

    connection.api_key_encrypted = secret_box.encrypt(api_key)
    connection.api_secret_encrypted = secret_box.encrypt(api_secret)
    connection.status = "connected"
    connection.last_connected_at = datetime.now(UTC)
    connection.last_error = None

    skew = adapter.clock_probe()
    _log(
        db,
        connection,
        stage="handshake",
        ok=True,
        detail=ack.message,
        latency_ms=latency,
        clock_skew_ms=skew,
    )
    _log(
        db,
        connection,
        stage="clock_probe",
        ok=abs(skew) <= MAX_CLOCK_SKEW_MS,
        detail=f"Clock skew {skew:.0f} ms (limit {MAX_CLOCK_SKEW_MS:.0f} ms).",
        clock_skew_ms=skew,
    )
    record_audit(
        db,
        action="broker.connect",
        actor_id=user.id,
        actor_email=user.email,
        detail=(
            f"{broker} sandbox connected in {latency:.0f} ms; withdrawal permission "
            f"{'ON' if withdraw_allowed else 'off'}."
        ),
    )
    db.flush()
    return _snapshot(db, connection)


def probe(db: Session, *, user: User, broker: str) -> dict:
    """Re-probe an existing connection: latency and clock skew, recorded."""
    connection = db.scalar(
        select(BrokerConnection).where(
            BrokerConnection.user_id == user.id, BrokerConnection.broker == broker.lower()
        )
    )
    if connection is None:
        raise DomainError("No connection exists for that broker.", status_code=404)
    adapter = adapter_for(connection.broker)
    started = time.perf_counter()
    skew = adapter.clock_probe()
    latency = (time.perf_counter() - started) * 1000
    ok = abs(skew) <= MAX_CLOCK_SKEW_MS
    _log(
        db,
        connection,
        stage="probe",
        ok=ok,
        detail=f"Clock skew {skew:.0f} ms.",
        latency_ms=latency,
        clock_skew_ms=skew,
    )
    db.flush()
    return {"ok": ok, "latency_ms": round(latency, 2), "clock_skew_ms": skew}


def disconnect(db: Session, *, user: User, broker: str) -> dict:
    connection = db.scalar(
        select(BrokerConnection).where(
            BrokerConnection.user_id == user.id, BrokerConnection.broker == broker.lower()
        )
    )
    if connection is None:
        raise DomainError("No connection exists for that broker.", status_code=404)
    connection.status = "disconnected"
    # Credentials are destroyed on disconnect: a link that is off must not still hold a key.
    connection.api_key_encrypted = None
    connection.api_secret_encrypted = None
    _log(
        db, connection, stage="disconnect", ok=True, detail="Disconnected and credentials cleared."
    )
    record_audit(
        db,
        action="broker.disconnect",
        actor_id=user.id,
        actor_email=user.email,
        detail=f"{broker} disconnected; stored credentials removed.",
    )
    db.flush()
    return _snapshot(db, connection)


def submit_to_sandbox(db: Session, *, user: User, broker: str, order: SandboxOrder) -> dict:
    """Hand an order to the sandbox venue. This never fills and never moves money."""
    connection = db.scalar(
        select(BrokerConnection).where(
            BrokerConnection.user_id == user.id, BrokerConnection.broker == broker.lower()
        )
    )
    if connection is None or connection.status != "connected":
        raise DomainError("Connect to the sandbox before sending an order.", status_code=409)
    require_sandbox(mode=connection.mode)
    adapter = adapter_for(connection.broker)
    started = time.perf_counter()
    ack = adapter.submit(order)
    latency = (time.perf_counter() - started) * 1000
    _log(
        db,
        connection,
        stage="submit",
        ok=ack.accepted,
        detail=ack.message,
        latency_ms=latency,
    )
    db.flush()
    return {
        "broker_order_id": ack.broker_order_id,
        "accepted": ack.accepted,
        "message": ack.message,
        "latency_ms": round(latency, 2),
        "filled": False,
        "money_moved": False,
        "received_at": ack.received_at,
    }


def _snapshot(db: Session, connection: BrokerConnection) -> dict:
    sessions = list(
        db.scalars(
            select(BrokerSession)
            .where(BrokerSession.connection_id == connection.id)
            .order_by(BrokerSession.occurred_at.desc())
            .limit(10)
        )
    )
    return {
        "id": connection.id,
        "broker": connection.broker,
        "label": connection.label,
        "mode": connection.mode,
        "status": connection.status,
        "withdraw_allowed": connection.withdraw_allowed,
        "scopes": connection.scopes,
        "ip_allowlist": connection.ip_allowlist,
        "has_credentials": connection.api_key_encrypted is not None,
        "last_connected_at": connection.last_connected_at,
        "last_error": connection.last_error,
        "sessions": [
            {
                "stage": row.stage,
                "ok": row.ok,
                "detail": row.detail,
                "latency_ms": row.latency_ms,
                "clock_skew_ms": row.clock_skew_ms,
                "occurred_at": row.occurred_at,
            }
            for row in sessions
        ],
        "live_trading": live_trading_is_impossible(),
        "settings_live_flag": settings.LIVE_TRADING_ENABLED,
    }


def list_connections(db: Session, *, user: User) -> list[dict]:
    connections = list(
        db.scalars(
            select(BrokerConnection)
            .where(BrokerConnection.user_id == user.id)
            .order_by(BrokerConnection.created_at)
        )
    )
    return [_snapshot(db, connection) for connection in connections]
