"""Operational endpoints: health, monitoring and audit integrity.

`/operations/health` is deliberately unauthenticated and shallow — it answers "is the
process serving requests?" so a load balancer can ask it. Everything that reveals
account state or system internals requires authentication, and the audit-integrity
endpoint is staff-only: verifying the chain is an operator action, not a user feature.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core import release
from app.core.audit import record_audit
from app.models.account import TradingAccount
from app.models.integrity import AuditSeal
from app.models.operations import AuditLog
from app.services import backup, integrity, monitoring
from app.services.errors import DomainError

router = APIRouter(prefix="/operations", tags=["operations"])


@router.get("/health")
def health() -> dict:
    """Liveness only. No database, no account state, no internals."""
    return {
        "status": "ok",
        "phase": release.PHASE_NAME,
        "phases_complete": release.PHASES_COMPLETE,
        "version": release.VERSION,
        "checks": "shallow",
        "checked_at": datetime.now(UTC).isoformat(),
    }


@router.get("/monitoring")
def monitoring_report(db: DbSession, user: CurrentUser) -> dict:
    """Full operational report for the caller's own account."""
    account = db.scalar(
        select(TradingAccount)
        .where(TradingAccount.user_id == user.id)
        .order_by(TradingAccount.created_at)
    )
    return monitoring.health_report(db, account=account)


@router.get("/monitoring/system")
def monitoring_system(db: DbSession, staff: StaffUser) -> dict:
    """System-wide report, staff only: includes every account's breaker and order activity."""
    return {**monitoring.health_report(db, account=None), "scope": "system"}


@router.get("/audit")
def list_audit(
    db: DbSession,
    staff: StaffUser,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = Query(default=None),
) -> dict:
    statement = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if action:
        statement = statement.where(AuditLog.action == action)
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    statement = statement.limit(limit).offset(offset)
    entries = list(db.scalars(statement))
    seals = {
        seal.audit_log_id: seal
        for seal in db.scalars(
            select(AuditSeal).where(AuditSeal.audit_log_id.in_([e.id for e in entries] or [""]))
        )
    }
    rows = [
        {
            "id": entry.id,
            "sequence": seals[entry.id].sequence if entry.id in seals else None,
            "sealed": entry.id in seals,
            "entry_hash": seals[entry.id].entry_hash if entry.id in seals else None,
            "action": entry.action,
            "actor_email": entry.actor_email,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "outcome": entry.outcome,
            "detail": entry.detail,
            "ip_address": entry.ip_address,
            "created_at": entry.created_at,
        }
        for entry in entries
    ]
    return {"entries": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/audit/verify")
def verify_audit(db: DbSession, staff: StaffUser) -> dict:
    """Walk the hash chain and report the first break. Read-only: never repairs."""
    return integrity.verify_chain(db).as_dict()


@router.post("/audit/seal")
def seal_audit(db: DbSession, staff: StaffUser) -> dict:
    """Seal any entries written before the chain existed. Staff-only and audited."""
    sealed = integrity.seal_pending(db)
    record_audit(
        db,
        action="audit.seal",
        actor_id=staff.id,
        actor_email=staff.email,
        detail=f"Sealed {sealed} pending audit entr(y/ies).",
    )
    db.commit()
    return {"sealed": sealed, **integrity.export_chain(db)}


@router.get("/audit/digest")
def audit_digest(db: DbSession, staff: StaffUser) -> dict:
    """A digest to publish elsewhere, so wholesale re-sealing becomes detectable."""
    return integrity.export_chain(db)


# --------------------------------------------------------------------------
# Backups and recovery
# --------------------------------------------------------------------------
@router.get("/backups")
def list_backups(db: DbSession, staff: StaffUser) -> dict:
    """What exists on disk. A backup you cannot enumerate is a rumour."""
    entries = backup.list_backups()
    return {"backups": entries, "count": len(entries), "directory": str(backup.BACKUP_DIR)}


@router.post("/backups")
def create_backup(db: DbSession, staff: StaffUser) -> dict:
    """Take a backup now, and record who asked for it."""
    entry = backup.backup_database(label="manual")
    record_audit(
        db,
        action="backup.created",
        actor_id=staff.id,
        actor_email=staff.email,
        detail=f"Backup written to {entry['path']} ({entry['bytes']} bytes).",
    )
    db.commit()
    return entry


@router.post("/backups/verify")
def verify_backup(payload: dict, db: DbSession, staff: StaffUser) -> dict:
    """Open a named backup and report whether it is intact and trustworthy."""
    path = (payload or {}).get("path")
    if not path:
        raise DomainError("A backup path is required.", status_code=422)
    return backup.verify_backup(path)


@router.post("/backups/drill")
def recovery_drill(db: DbSession, staff: StaffUser) -> dict:
    """Back up, restore into a scratch copy, verify — and say whether it passed."""
    result = backup.recovery_drill()
    record_audit(
        db,
        action="backup.drill",
        actor_id=staff.id,
        actor_email=staff.email,
        outcome="success" if result["passed"] else "failure",
        detail="Recovery drill passed." if result["passed"] else "Recovery drill failed.",
    )
    db.commit()
    return result
