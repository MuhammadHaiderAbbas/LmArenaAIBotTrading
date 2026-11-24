"""Circuit breaker and emergency shutdown.

The distinction the blueprint insists on, and which this module encodes in types
rather than in prose:

> The shutdown must distinguish stopping new risk from managing existing exposure.

So a trip **halts new risk**. It does not build a liquidation order — closing exposure
in an illiquid or disrupted market can create the very loss the breaker exists to
prevent, and it is a policy decision a human should make with the book in front of
them. `positions_affected` records what is still exposed so that decision can be made
knowingly, rather than by a machine in a loop.

Two states, deliberately not one:

- `OPEN` — tripped by a metric. May be cleared automatically when the metric recovers,
  or by a human.
- `MANUAL` — a human pressed the button. Only a human clears it. Collapsing these
  would let an automatic reset silently undo an emergency stop.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import Order, OrderStatus, PositionStatus
from app.models.risk import BreakerState, CircuitBreaker, RiskEvent
from app.services.risk import metrics

#: Statuses a shutdown may cancel. A partially filled order is included: its
#: remaining quantity is outstanding risk, and halting new risk means halting it.
CANCELLABLE_STATUSES = {
    OrderStatus.NEW,
    OrderStatus.VALIDATED,
    OrderStatus.SUBMITTED,
    OrderStatus.PARTIALLY_FILLED,
}


def breaker_state(db: Session, account: TradingAccount) -> BreakerState:
    """Current breaker state. The latest row wins; a cleared row means closed."""
    row = db.scalar(
        select(CircuitBreaker)
        .where(CircuitBreaker.trading_account_id == account.id)
        .order_by(CircuitBreaker.created_at.desc())
        .limit(1)
    )
    if row is None:
        return BreakerState.CLOSED
    if row.cleared_at is not None:
        return BreakerState.CLOSED
    return row.state


def active_breaker(db: Session, account: TradingAccount) -> CircuitBreaker | None:
    if breaker_state(db, account) == BreakerState.CLOSED:
        return None
    return db.scalar(
        select(CircuitBreaker)
        .where(CircuitBreaker.trading_account_id == account.id)
        .order_by(CircuitBreaker.created_at.desc())
        .limit(1)
    )


def trip_breaker(
    db: Session,
    *,
    account: TradingAccount,
    trigger: str,
    reason: str,
    state: BreakerState = BreakerState.OPEN,
    actor_id: str | None = None,
    cancel_pending: bool = True,
    now: datetime | None = None,
) -> CircuitBreaker:
    """Halt new risk for an account.

    Cancelling pending orders is attempted but never fatal: an order that cannot be
    cancelled safely is reported in the event rather than forcing the trip to fail.
    A breaker that refuses to trip because a cancel errored is not a breaker.
    """
    moment = now or datetime.now(UTC)
    cancelled = 0
    failed: list[str] = []

    if cancel_pending:
        pending = list(
            db.scalars(
                select(Order).where(
                    Order.trading_account_id == account.id,
                    Order.status.in_(CANCELLABLE_STATUSES),
                )
            )
        )
        for order in pending:
            if order.filled_quantity > 0:
                # Partially filled: withdrawing the remainder is safe in this
                # simulator because nothing is resting at a venue, but it is recorded
                # so a real adapter would know it needs care.
                failed.append(f"{order.id}: partially filled, remainder cancelled")
            order.status = OrderStatus.CANCELLED
            order.reject_reason = f"Cancelled by circuit breaker: {trigger}"
            cancelled += 1

    open_book = metrics.open_positions(db, account)
    positions_affected = len(open_book)
    exposure = metrics.positions_value(db, account)

    row = CircuitBreaker(
        trading_account_id=account.id,
        state=state,
        trigger=trigger,
        reason=reason,
        halted_new_risk=True,
        positions_affected=positions_affected,
        orders_cancelled=cancelled,
        tripped_at=moment,
        actor_id=actor_id,
    )
    db.add(row)
    db.flush()

    db.add(
        RiskEvent(
            trading_account_id=account.id,
            event_type="breaker_tripped",
            severity="critical" if state == BreakerState.MANUAL else "warning",
            message=reason,
            detail={
                "breaker_id": row.id,
                "trigger": trigger,
                "state": state.value,
                "orders_cancelled": cancelled,
                "positions_affected": positions_affected,
                "open_exposure": round(exposure, 4),
                "cancel_notes": failed,
                "closing_positions": "not attempted: stopping new risk is a separate act from closing exposure",
            },
            actor_id=actor_id,
        )
    )
    db.flush()
    return row


def clear_breaker(
    db: Session,
    *,
    account: TradingAccount,
    actor_id: str | None = None,
    reason: str = "Cleared by an authorised user.",
    now: datetime | None = None,
    allow_manual: bool = True,
) -> CircuitBreaker | None:
    """Resume trading. Explicit action, always attributed, always logged."""
    row = active_breaker(db, account)
    if row is None:
        return None
    if row.state == BreakerState.MANUAL and not allow_manual:
        raise PermissionError("A manual emergency stop requires an authorised human to clear it.")

    row.cleared_at = now or datetime.now(UTC)
    row.cleared_by = actor_id
    db.add(
        RiskEvent(
            trading_account_id=account.id,
            event_type="breaker_cleared",
            severity="warning" if row.state == BreakerState.MANUAL else "info",
            message=reason,
            detail={"breaker_id": row.id, "previous_state": row.state.value},
            actor_id=actor_id,
        )
    )
    db.flush()
    return row


def emergency_stop(
    db: Session,
    *,
    account: TradingAccount,
    reason: str,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> CircuitBreaker:
    """The blueprint's shutdown, item by item.

    1. Reject all new orders and strategy submissions — via `MANUAL`, which no
       metric can clear.
    2. Cancel eligible pending orders where safe — done, with failures recorded.
    3. Freeze autonomous execution — there is no autonomous loop to freeze (Phase 8
       deliberately shipped none), so this is a statement the API can make truthfully
       rather than a flag toggled for show.
    4. Reconcile open positions and outstanding orders — counted and stored.
    5. Notify and audit — a `RiskEvent` with severity `critical`.
    6. Require explicit authorised action to resume — `clear_breaker` is staff-only.
    """
    return trip_breaker(
        db,
        account=account,
        trigger="emergency_stop",
        reason=reason,
        state=BreakerState.MANUAL,
        actor_id=actor_id,
        cancel_pending=True,
        now=now,
    )


def enforce_breaker(db: Session, account: TradingAccount) -> None:
    """Raise when the account is halted. Called immediately before submission."""
    state = breaker_state(db, account)
    if state == BreakerState.CLOSED:
        return
    from app.services.errors import DomainError

    if state == BreakerState.MANUAL:
        raise DomainError(
            "A manual emergency stop is in force. An authorised user must clear it before any order."
        )
    raise DomainError(
        "The circuit breaker is open: a risk limit was breached. Clear it before submitting orders."
    )


def evaluate_breakers(
    db: Session, account: TradingAccount, *, now: datetime | None = None
) -> str | None:
    """Trip the breaker automatically when a portfolio limit is breached.

    Called after fills, so a breach is caught even when no new order is being
    proposed. Returns the trigger name when it tripped, else None.
    """
    from app.services.risk.engine import ensure_policy

    if breaker_state(db, account) != BreakerState.CLOSED:
        return None

    policy = ensure_policy(db, account)
    drawdown = metrics.current_drawdown(db, account)
    if drawdown >= policy.max_drawdown:
        trip_breaker(
            db,
            account=account,
            trigger="max_drawdown",
            reason=f"Drawdown reached {drawdown:.2%}, at or beyond the {policy.max_drawdown:.2%} limit.",
            now=now,
        )
        return "max_drawdown"

    total_equity = metrics.equity(db, account)
    day_pnl = metrics.daily_pnl(db, account, now=now)
    if total_equity > 0 and day_pnl < 0 and (-day_pnl / total_equity) >= policy.max_daily_loss:
        trip_breaker(
            db,
            account=account,
            trigger="daily_loss",
            reason=(
                f"Realised loss today reached {(-day_pnl / total_equity):.2%}, at or beyond the "
                f"{policy.max_daily_loss:.2%} daily limit."
            ),
            now=now,
        )
        return "daily_loss"

    return None


def open_position_count(db: Session, account: TradingAccount) -> int:
    from app.models.execution import Position

    return len(
        list(
            db.scalars(
                select(Position).where(
                    Position.trading_account_id == account.id,
                    Position.status == PositionStatus.OPEN,
                )
            )
        )
    )
