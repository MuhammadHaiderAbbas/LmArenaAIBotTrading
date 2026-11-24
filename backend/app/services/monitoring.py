"""Operational health signals.

Monitoring exists to answer four questions the blueprint names explicitly:

> Monitoring for stale data, API failures, abnormal order activity and model degradation.

Each check here returns a *finding* rather than a boolean, because "is the system
healthy?" is a useless answer without "which part, since when, and how bad". Nothing
here mutates state: monitoring that acts is a risk engine, and Phase 9 already has one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import Order, OrderStatus
from app.models.market import DataQualityEvent, Instrument, MarketCandle, QualitySeverity, Timeframe
from app.models.operations import SystemEvent
from app.models.risk import CircuitBreaker
from app.services import integrity
from app.services.ai.agents import aware

#: A daily bar older than this means ingestion has stopped.
STALE_DATA_HOURS = 36
#: More than this many orders in an hour is abnormal for a paper account.
ABNORMAL_ORDER_BURST = 40
#: Rejection rate above this suggests a broken strategy or a mis-set limit.
ABNORMAL_REJECTION_RATE = 0.5


@dataclass
class Finding:
    check: str
    status: str  # ok | warning | critical
    message: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }


def check_data_freshness(db: Session, *, now: datetime | None = None) -> Finding:
    moment = now or datetime.now(UTC)
    instruments = list(db.scalars(select(Instrument).where(Instrument.status == "active")))
    if not instruments:
        return Finding("data_freshness", "warning", "No active instruments are registered.")

    stale: list[dict] = []
    for instrument in instruments:
        newest = db.scalar(
            select(MarketCandle.close_time)
            .where(
                MarketCandle.instrument_id == instrument.id, MarketCandle.timeframe == Timeframe.D1
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(1)
        )
        if newest is None:
            stale.append({"symbol": instrument.symbol, "reason": "no bars"})
            continue
        age_hours = (moment - aware(newest)).total_seconds() / 3600
        if age_hours > STALE_DATA_HOURS:
            stale.append({"symbol": instrument.symbol, "age_hours": round(age_hours, 1)})

    if not stale:
        return Finding(
            "data_freshness",
            "ok",
            f"All {len(instruments)} active instruments have bars newer than {STALE_DATA_HOURS}h.",
        )
    status = "critical" if len(stale) == len(instruments) else "warning"
    return Finding(
        "data_freshness",
        status,
        f"{len(stale)} of {len(instruments)} instruments have stale or missing bars.",
        {"instruments": stale[:20]},
    )


def check_quality_events(db: Session, *, now: datetime | None = None, hours: int = 24) -> Finding:
    moment = now or datetime.now(UTC)
    since = moment - timedelta(hours=hours)
    critical = db.scalar(
        select(func.count(DataQualityEvent.id)).where(
            DataQualityEvent.severity == QualitySeverity.CRITICAL,
            DataQualityEvent.created_at >= since,
        )
    )
    total = db.scalar(
        select(func.count(DataQualityEvent.id)).where(DataQualityEvent.created_at >= since)
    )
    if critical:
        return Finding(
            "data_quality",
            "critical",
            f"{critical} critical data-quality event(s) in the last {hours}h. Orders are refused for affected instruments.",
            {"critical": critical, "total": total},
        )
    return Finding(
        "data_quality",
        "ok" if not total else "warning",
        f"{total or 0} quality event(s) in the last {hours}h.",
        {"total": total},
    )


def check_order_activity(
    db: Session, *, account: TradingAccount | None = None, now: datetime | None = None
) -> Finding:
    """Abnormal order activity: a burst, or a rejection rate that suggests breakage."""
    moment = now or datetime.now(UTC)
    since = moment - timedelta(hours=1)
    statement = select(Order).where(Order.created_at >= since)
    if account is not None:
        statement = statement.where(Order.trading_account_id == account.id)
    orders = list(db.scalars(statement))
    rejected = [order for order in orders if order.status == OrderStatus.REJECTED]

    if len(orders) > ABNORMAL_ORDER_BURST:
        return Finding(
            "order_activity",
            "critical",
            f"{len(orders)} orders in the last hour exceeds the {ABNORMAL_ORDER_BURST} burst threshold.",
            {"orders_last_hour": len(orders), "rejected": len(rejected)},
        )
    if orders and len(rejected) / len(orders) > ABNORMAL_REJECTION_RATE:
        return Finding(
            "order_activity",
            "warning",
            f"{len(rejected)} of {len(orders)} orders in the last hour were rejected; the risk or data layer may be mis-set.",
            {"orders_last_hour": len(orders), "rejected": len(rejected)},
        )
    return Finding(
        "order_activity",
        "ok",
        f"{len(orders)} order(s) in the last hour, {len(rejected)} rejected.",
        {"orders_last_hour": len(orders), "rejected": len(rejected)},
    )


def check_breakers(db: Session) -> Finding:
    open_breakers = db.scalar(
        select(func.count(CircuitBreaker.id)).where(CircuitBreaker.cleared_at.is_(None))
    )
    if open_breakers:
        return Finding(
            "circuit_breakers",
            "critical",
            f"{open_breakers} account(s) have an active circuit breaker; new risk is halted there.",
            {"active": open_breakers},
        )
    return Finding("circuit_breakers", "ok", "No active circuit breakers.")


def check_model_degradation(db: Session, *, days: int = 7) -> Finding:
    """Drift signals. Advisory only: nothing here promotes, demotes or retrains."""
    from app.models.ml import DriftEvent, DriftSeverity

    since = datetime.now(UTC) - timedelta(days=days)
    severe = db.scalar(
        select(func.count(DriftEvent.id)).where(
            DriftEvent.severity == DriftSeverity.CRITICAL, DriftEvent.created_at >= since
        )
    )
    if severe:
        return Finding(
            "model_degradation",
            "warning",
            f"{severe} critical drift signal(s) in the last {days}d. Advisory: no automatic demotion.",
            {"critical_drift": severe},
        )
    return Finding("model_degradation", "ok", f"No critical drift signals in the last {days}d.")


def check_audit_chain(db: Session) -> Finding:
    report = integrity.verify_chain(db)
    if not report.verified:
        return Finding(
            "audit_chain",
            "critical",
            f"The audit chain does not verify: {report.reason}",
            {
                "entries": report.entries,
                "sealed": report.sealed,
                "broken_at": report.first_broken_at,
            },
        )
    return Finding(
        "audit_chain",
        "ok",
        f"Audit chain verified across {report.entries} entries.",
        {"entries": report.entries, "digest": report.digest},
    )


def check_execution_reconciliation(db: Session, account: TradingAccount) -> Finding:
    from app.services.execution import broker

    report = broker.reconcile_positions(db, account=account)
    if report.get("investment_agrees") and report.get("realised_agrees"):
        return Finding(
            "reconciliation",
            "ok",
            "Positions reconcile against the ledger.",
            {"positions": report.get("open_positions", 0)},
        )
    return Finding(
        "reconciliation",
        "critical",
        "Positions and the ledger disagree — the position view is wrong, the ledger is the truth.",
        {"report": report},
    )


def check_recent_system_events(db: Session, *, hours: int = 24) -> Finding:
    since = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
    critical = db.scalar(
        select(func.count(SystemEvent.id)).where(
            SystemEvent.severity == "critical", SystemEvent.created_at >= since
        )
    )
    if critical:
        return Finding(
            "system_events",
            "critical",
            f"{critical} critical system event(s) in the last {hours}h.",
            {"critical": critical},
        )
    return Finding("system_events", "ok", f"No critical system events in the last {hours}h.")


def health_report(db: Session, account: TradingAccount | None = None) -> dict:
    """Run every check and summarise. `account` enables the reconciliation check."""
    findings: list[Finding] = [
        check_data_freshness(db),
        check_quality_events(db),
        check_order_activity(db, account=account),
        check_breakers(db),
        check_model_degradation(db),
        check_audit_chain(db),
        check_recent_system_events(db),
    ]
    if account is not None:
        findings.append(check_execution_reconciliation(db, account))

    severities = [finding.status for finding in findings]
    overall = (
        "critical" if "critical" in severities else ("warning" if "warning" in severities else "ok")
    )
    return {
        "status": overall,
        "checked_at": datetime.now(UTC).isoformat(),
        "findings": [finding.as_dict() for finding in findings],
        "counts": {
            "ok": severities.count("ok"),
            "warning": severities.count("warning"),
            "critical": severities.count("critical"),
        },
        "checks_run": len(findings),
    }
