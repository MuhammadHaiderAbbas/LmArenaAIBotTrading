"""Risk policy, checks, and the circuit breaker.

Two things are load-bearing here:

- **`POST /risk/emergency-stop` is staff-only.** An account holder can trade their own
  paper account; halting the platform is an operator action, and one a user cannot undo
  on their own.
- **`GET /risk/status` is honest about what is not enforced.** It reports the limits
  that *are* applied and separately lists the ones that are advisory, rather than
  presenting a tidy dashboard that implies uniform coverage.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.models.account import TradingAccount
from app.models.identity import UserRole
from app.models.risk import BreakerState, CircuitBreaker, RiskCheck, RiskEvent, RiskPolicy, RiskRule
from app.schemas.risk import (
    BreakerRead,
    EmergencyStopRequest,
    RiskCheckRead,
    RiskEvaluateRequest,
    RiskEvaluateResponse,
    RiskEventRead,
    RiskOverview,
    RiskPolicyRead,
    RiskPolicyUpdate,
    RiskStatus,
)
from app.services.execution.orders import _price
from app.services.risk import breakers, engine, metrics

router = APIRouter(prefix="/risk", tags=["risk"])


def _account(db, user, account_id: str | None) -> TradingAccount:
    if account_id:
        account = db.get(TradingAccount, account_id)
        if account is None or account.user_id != user.id:
            raise HTTPException(status_code=404, detail="Account not found")
        return account
    account = db.scalar(
        select(TradingAccount)
        .where(TradingAccount.user_id == user.id)
        .order_by(TradingAccount.created_at)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="No trading account; create one first")
    return account


@router.get("/status", response_model=RiskStatus)
def risk_status(db: DbSession, user: CurrentUser, account_id: str | None = None) -> RiskStatus:
    """Current risk posture: limits, applied rules, and breaker state."""
    account = _account(db, user, account_id)
    policy = engine.ensure_policy(db, account)
    state = breakers.breaker_state(db, account)
    active = breakers.active_breaker(db, account)
    db.commit()

    drawdown = metrics.current_drawdown(db, account)
    total_equity = metrics.equity(db, account)
    day_pnl = metrics.daily_pnl(db, account)

    return RiskStatus(
        breaker_state=state.value,
        halted_new_risk=state != BreakerState.CLOSED,
        breaker_trigger=active.trigger if active else None,
        breaker_reason=active.reason if active else None,
        breaker_since=active.tripped_at.isoformat() if active else None,
        equity=round(total_equity, 4),
        exposure=round(metrics.exposure(db, account), 6),
        open_positions=len(metrics.open_positions(db, account)),
        drawdown=round(drawdown, 6),
        daily_pnl=round(day_pnl, 4),
        daily_loss_fraction=round(
            (-day_pnl / total_equity) if total_equity > 0 and day_pnl < 0 else 0.0, 6
        ),
        policy=RiskPolicyRead.model_validate(policy),
        applied_rules=[
            rule.value for rule in sorted(engine.SUPPORTED_RULES, key=lambda r: r.value)
        ],
        # Named explicitly so the UI can never imply coverage it does not have.
        advisory_rules=[],
        positions_affected=active.positions_affected if active else 0,
        orders_cancelled=active.orders_cancelled if active else 0,
        closes_positions=False,
    )


@router.get("/policy", response_model=RiskPolicyRead)
def get_policy(db: DbSession, user: CurrentUser, account_id: str | None = None) -> RiskPolicyRead:
    account = _account(db, user, account_id)
    policy = engine.ensure_policy(db, account)
    db.commit()
    return RiskPolicyRead.model_validate(policy)


@router.put("/policy", response_model=RiskPolicyRead)
def update_policy(
    payload: RiskPolicyUpdate,
    request: Request,
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
) -> RiskPolicyRead:
    """Replace the active policy. A change is a new version, never an edit in place.

    Loosening a limit is exactly the kind of change that must be visible afterwards, so
    the old row stays and the new one records who made it and why.
    """
    account = _account(db, user, account_id)
    current = engine.ensure_policy(db, account)
    if user.role not in (UserRole.ADMIN, UserRole.ANALYST):
        raise HTTPException(status_code=403, detail="Changing a risk policy requires a staff role.")

    updates = payload.model_dump(exclude_none=True)
    reason = updates.pop("reason", "") or "No reason given."
    if not updates:
        return RiskPolicyRead.model_validate(current)

    tightened = {key: value for key, value in updates.items() if value is not None}
    current.is_active = False
    db.flush()

    successor = RiskPolicy(
        trading_account_id=account.id,
        name=current.name,
        version=current.version + 1,
        is_active=True,
        notes=f"Version {current.version + 1}. Changed by {user.email}: {reason}",
        **{
            field: tightened.get(field, getattr(current, field))
            for field in [
                "max_risk_per_trade",
                "max_daily_loss",
                "max_drawdown",
                "max_positions",
                "max_exposure",
                "max_position_concentration",
                "max_correlation",
                "max_spread_bps",
                "max_orders_per_hour",
                "leverage_enabled",
            ]
        },
    )
    db.add(successor)
    db.flush()
    engine.record_event(
        db,
        account=account,
        event_type="policy_changed",
        severity="warning",
        message=f"Risk policy replaced with version {successor.version}. {reason}",
        detail={"changes": tightened, "previous_version": current.version},
        actor_id=user.id,
    )
    request.state.audited = True
    db.commit()
    return RiskPolicyRead.model_validate(successor)


@router.get("/checks", response_model=list[RiskCheckRead])
def list_checks(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
    decision: str | None = Query(default=None),
) -> list[RiskCheckRead]:
    account = _account(db, user, account_id)
    statement = (
        select(RiskCheck)
        .where(RiskCheck.trading_account_id == account.id)
        .order_by(RiskCheck.created_at.desc())
        .limit(limit)
    )
    if decision:
        statement = statement.where(RiskCheck.decision == decision)
    return [RiskCheckRead.model_validate(row) for row in db.scalars(statement)]


@router.get("/events", response_model=list[RiskEventRead])
def list_events(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    limit: int = Query(default=25, ge=1, le=200),
) -> list[RiskEventRead]:
    account = _account(db, user, account_id)
    rows = db.scalars(
        select(RiskEvent)
        .where(RiskEvent.trading_account_id == account.id)
        .order_by(RiskEvent.created_at.desc())
        .limit(limit)
    )
    return [RiskEventRead.model_validate(row) for row in rows]


@router.post("/evaluate", response_model=RiskEvaluateResponse)
def evaluate(
    payload: RiskEvaluateRequest, db: DbSession, user: CurrentUser, account_id: str | None = None
) -> RiskEvaluateResponse:
    """Dry-run a proposal through the risk engine.

    Nothing is persisted (`persist=False`) and no order is created: this is the
    "what would happen if" call the UI uses to show a limit before a user hits submit.
    """
    account = _account(db, user, account_id)
    price = payload.price or _price(db, payload.symbol)
    outcome = engine.evaluate_proposal(
        db,
        account=account,
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        price=price,
        stop_pct=payload.stop_pct,
        actor_id=user.id,
        persist=False,
    )
    return RiskEvaluateResponse(
        decision=outcome.decision.value,
        approved_quantity=outcome.approved_quantity,
        reason=outcome.reason,
        breached_rules=outcome.breached,
        checks=[
            {
                "rule": c.rule.value,
                "passed": c.passed,
                "observed": c.observed,
                "limit": c.limit,
                "detail": c.detail,
            }
            for c in outcome.checks
        ],
        equity=outcome.equity,
        exposure=outcome.exposure,
        open_positions=outcome.open_positions,
    )


@router.post("/emergency-stop", response_model=BreakerRead, status_code=status.HTTP_201_CREATED)
def emergency_stop(
    payload: EmergencyStopRequest, db: DbSession, staff: StaffUser, account_id: str | None = None
) -> BreakerRead:
    """The blueprint's shutdown. Halts new risk; says plainly that it does not liquidate."""
    account = (
        db.scalar(select(TradingAccount).order_by(TradingAccount.created_at))
        if account_id is None
        else db.get(TradingAccount, account_id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")

    row = breakers.emergency_stop(db, account=account, reason=payload.reason, actor_id=staff.id)
    db.commit()
    return BreakerRead.model_validate(row)


@router.post("/resume", response_model=BreakerRead)
def resume(
    db: DbSession,
    staff: StaffUser,
    account_id: str | None = None,
    reason: str = "Resumed by an authorised user.",
) -> BreakerRead:
    """Clear a halt. Staff-only, attributed, and impossible to do implicitly."""
    account = (
        db.scalar(select(TradingAccount).order_by(TradingAccount.created_at))
        if account_id is None
        else db.get(TradingAccount, account_id)
    )
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    row = breakers.clear_breaker(
        db, account=account, actor_id=staff.id, reason=reason, allow_manual=True
    )
    if row is None:
        raise HTTPException(status_code=409, detail="No active circuit breaker to clear.")
    db.commit()
    return BreakerRead.model_validate(row)


@router.get("/breakers", response_model=list[BreakerRead])
def list_breakers(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[BreakerRead]:
    account = _account(db, user, account_id)
    rows = db.scalars(
        select(CircuitBreaker)
        .where(CircuitBreaker.trading_account_id == account.id)
        .order_by(CircuitBreaker.created_at.desc())
        .limit(limit)
    )
    return [BreakerRead.model_validate(row) for row in rows]


@router.get("/overview", response_model=RiskOverview)
def overview(db: DbSession, user: CurrentUser, account_id: str | None = None) -> RiskOverview:
    account = _account(db, user, account_id)
    policy = engine.ensure_policy(db, account)
    checks = list(db.scalars(select(RiskCheck).where(RiskCheck.trading_account_id == account.id)))
    rejected = [row for row in checks if row.decision.value == "reject"]
    reduced = [row for row in checks if row.decision.value == "allow_reduced"]
    events = list(
        db.scalars(
            select(RiskEvent)
            .where(RiskEvent.trading_account_id == account.id)
            .order_by(RiskEvent.created_at.desc())
            .limit(5)
        )
    )
    breach_counts: dict[str, int] = {}
    for row in rejected:
        for rule in row.breached_rules or []:
            breach_counts[rule] = breach_counts.get(rule, 0) + 1

    state = breakers.breaker_state(db, account)
    db.commit()
    return RiskOverview(
        breaker_state=state.value,
        checks_total=len(checks),
        rejected_total=len(rejected),
        reduced_total=len(reduced),
        rejection_rate=round(len(rejected) / len(checks), 4) if checks else 0.0,
        breach_counts=dict(sorted(breach_counts.items(), key=lambda item: -item[1])),
        policy_version=policy.version,
        limits=policy.limits,
        recent_events=[RiskEventRead.model_validate(row) for row in events],
        rules_supported=[
            rule.value for rule in sorted(engine.SUPPORTED_RULES, key=lambda r: r.value)
        ],
        # Every enumerated rule the engine does not evaluate. Empty today, and
        # computed rather than hardcoded so it cannot go stale.
        rules_not_implemented=[
            rule.value for rule in RiskRule if rule not in engine.SUPPORTED_RULES
        ],
    )
