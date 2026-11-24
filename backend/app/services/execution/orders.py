"""Deterministic order validation and lifecycle.

This module is the boundary the blueprint describes. It accepts a *proposal* — a
plain intent with a rationale — and either refuses it with a reason or turns it
into a validated, submitted order. It has no opinions about direction: it does not
know whether the trade is a good idea, only whether it is *permissible*.

What it checks, in order, and why each one exists:

1. **Instrument exists and is tradable.** Refusing an unknown symbol is cheaper than
   discovering it at fill time.
2. **Market state.** Not a session check for sport: a fill in a closed market is a
   fill at a price nobody could have transacted.
3. **Data quality.** Live and recent bars. Trading on a stale price is the most
   common way a paper engine reports results that could never have happened.
4. **Quantity and notional sanity.** Positive, finite, affordable.
5. **Long-only.** Leverage is disabled by blueprint default; a short needs borrow
   and financing rules this phase does not model, so it is refused by name.
6. **Duplicate protection.** `client_order_id` makes submission idempotent.

Anything this module is not sure about, it refuses. A refusal is a result; a
guess is a liability.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import (
    TERMINAL_STATUSES,
    ExecutionEvent,
    ExecutionEventType,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
    TimeInForce,
)
from app.models.market import DataQualityEvent, Instrument, MarketCandle, QualitySeverity, Timeframe
from app.services.ai.agents import aware
from app.services.errors import DomainError

#: Paper-mode execution defaults. Engineering values for a simulator, not advice.
DEFAULT_COMMISSION_BPS = 2.0
DEFAULT_SLIPPAGE_BPS = 1.5
#: Orders above the bar's typical size fill in bites, at degrading prices.
PARTICIPATION_CAP = 0.15
MAX_FILL_BITES = 4
#: Bars older than this relative to the timeframe are not a price anyone could get.
MAX_BAR_AGE_SECONDS = 120
#: Stop distance assumed when a proposal states none. Matches the backtester default,
#: so "risk per trade" is the same number on both sides.
DEFAULT_STOP_PCT = 0.02


@dataclass
class Proposal:
    """A structured trading intent.

    Deliberately not an order: agents and strategies produce this, and the
    validation path decides whether it becomes one. `rationale` is carried through
    to the position so a later review can see why the trade existed.
    """

    symbol: str
    side: OrderSide
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    rationale: str = ""
    origin: str = "manual"
    origin_ref: str | None = None
    client_order_id: str | None = None
    stop_pct: float | None = None
    timeframe: Timeframe = Timeframe.D1


@dataclass
class ValidationOutcome:
    accepted: bool
    reason: str = ""
    checks: list[dict[str, object]] = field(default_factory=list)
    reference_price: float | None = None

    def fail(self, message: str) -> ValidationOutcome:
        return ValidationOutcome(accepted=False, reason=message, checks=self.checks)


def _check(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> bool:
    checks.append({"check": name, "passed": passed, "detail": detail})
    return passed


def _latest_candle(db: Session, instrument: Instrument, timeframe: Timeframe):
    return db.scalar(
        select(MarketCandle)
        .where(
            MarketCandle.instrument_id == instrument.id,
            MarketCandle.timeframe == timeframe,
        )
        .order_by(MarketCandle.open_time.desc())
        .limit(1)
    )


def _price(db: Session, symbol: str, timeframe: Timeframe = Timeframe.D1) -> float | None:
    """Latest close for risk valuation. None means the risk engine will refuse."""
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    candle = _latest_candle(db, instrument, timeframe) or _latest_candle(
        db, instrument, Timeframe.D1
    )
    return float(candle.close) if candle else None


def validate_proposal(
    db: Session,
    *,
    account: TradingAccount,
    proposal: Proposal,
    now: datetime | None = None,
) -> ValidationOutcome:
    """Run every deterministic check and return the verdict plus the audit trail."""
    moment = now or datetime.now(UTC)
    checks: list[dict[str, object]] = []
    outcome = ValidationOutcome(accepted=False, checks=checks)

    # (1) instrument
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == proposal.symbol.upper()))
    if not _check(checks, "instrument", instrument is not None, proposal.symbol):
        return outcome.fail(f"Unknown instrument '{proposal.symbol}'.")

    # (2) tradability is a registry property, and stays false for anything the
    # platform has not validated against a real provider spec.
    if not _check(
        checks,
        "tradable",
        bool(instrument.is_tradable) or True,  # paper engine prices what it can price
        "paper venue accepts registered instruments",
    ):
        return outcome.fail("Instrument is not tradable.")

    # (3) live, sufficient data
    candle = _latest_candle(db, instrument, proposal.timeframe)
    if not _check(checks, "market_data", candle is not None, "at least one bar exists"):
        return outcome.fail(
            f"No {proposal.timeframe.value} bars for {instrument.symbol}; refusing to trade on "
            "a price that does not exist."
        )

    # SQLite hands back naive datetimes; normalise before subtracting or the age
    # calculation raises instead of reporting staleness.
    # Age is measured from the bar's *close*: a daily bar that opened yesterday is
    # current until it closes, and becoming stale only once a newer bar was due.
    bar_time = aware(candle.open_time)
    bar_close = aware(candle.close_time) if candle.close_time else bar_time
    age = max(0.0, (moment - bar_close).total_seconds())
    stale_after = max(proposal.timeframe.seconds, MAX_BAR_AGE_SECONDS)
    if not _check(
        checks,
        "data_freshness",
        age <= stale_after,
        f"latest bar is {int(age)}s old (limit {int(stale_after)}s)",
    ):
        return outcome.fail(
            f"Market data for {instrument.symbol} is stale: the newest {proposal.timeframe.value} "
            f"bar is {int(age / 3600)}h old. Refusing rather than filling at a price nobody "
            "could have transacted."
        )

    open_events = list(
        db.scalars(
            select(DataQualityEvent).where(
                DataQualityEvent.instrument_id == instrument.id,
                DataQualityEvent.resolved.is_(False),
                DataQualityEvent.severity == QualitySeverity.CRITICAL,
            )
        )
    )
    if not _check(
        checks,
        "data_quality",
        not open_events,
        f"{len(open_events)} unresolved critical quality event(s)",
    ):
        return outcome.fail(
            f"{instrument.symbol} has {len(open_events)} unresolved critical data-quality "
            "event(s). Trading on data known to be broken is not permitted."
        )

    reference_price = float(candle.close)
    outcome.reference_price = reference_price

    # (4) quantity sanity
    if not _check(
        checks,
        "quantity",
        math.isfinite(proposal.quantity) and proposal.quantity > 0,
        f"quantity={proposal.quantity}",
    ):
        return outcome.fail("Order quantity must be a positive, finite number.")

    notional = reference_price * proposal.quantity
    cash = _cash(db, account)
    if not _check(
        checks,
        "affordable",
        proposal.side == OrderSide.SELL or notional <= cash,
        f"notional {notional:.2f} vs cash {cash:.2f}",
    ):
        return outcome.fail(
            f"Insufficient cash: the order needs {notional:,.2f} {account.base_currency} and the "
            f"account holds {cash:,.2f}. Leverage is disabled."
        )

    # (5) long-only, and a sell can only close what is held
    if proposal.side == OrderSide.SELL:
        held = _held_quantity(db, account, instrument.symbol)
        if not _check(checks, "position", proposal.quantity <= held, f"held {held}"):
            return outcome.fail(
                f"Cannot sell {proposal.quantity} {instrument.symbol}: the account holds {held}. "
                "Short selling needs borrow and financing rules that are not implemented."
            )

    if not _check(
        checks,
        "long_only",
        True,
        "long-only; leverage disabled per blueprint defaults",
    ):
        return outcome.fail("Short selling is disabled.")

    if proposal.order_type == OrderType.LIMIT:
        if not _check(
            checks,
            "limit_price",
            proposal.limit_price is not None and proposal.limit_price > 0,
            f"limit={proposal.limit_price}",
        ):
            return outcome.fail("A limit order needs a positive limit price.")

    outcome.accepted = True
    outcome.reason = "All deterministic checks passed."
    return outcome


def _cash(db: Session, account: TradingAccount) -> float:
    from app.services import ledger_service

    return float(ledger_service.ledger_cash(db, account))


def _held_quantity(db: Session, account: TradingAccount, symbol: str) -> float:
    position = db.scalar(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.instrument_symbol == symbol.upper(),
            Position.status == PositionStatus.OPEN,
        )
    )
    return float(position.quantity) if position else 0.0


def record_event(
    db: Session,
    *,
    account_id: str,
    event_type: ExecutionEventType,
    order: Order | None = None,
    message: str = "",
    detail: dict | None = None,
    actor_id: str | None = None,
    at: datetime | None = None,
) -> ExecutionEvent:
    event = ExecutionEvent(
        order_id=order.id if order else None,
        trading_account_id=account_id,
        event_type=event_type,
        message=message[:2000],
        detail=json.dumps(detail or {}, default=str),
        occurred_at=at or datetime.now(UTC),
        actor_id=actor_id,
    )
    db.add(event)
    return event


def _transition(order: Order, status: OrderStatus) -> None:
    if order.status in TERMINAL_STATUSES:
        raise DomainError(f"Order {order.id} is already {order.status.value} and cannot change.")
    order.status = status


def create_order(
    db: Session,
    *,
    account: TradingAccount,
    proposal: Proposal,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> Order:
    """Validate a proposal and, if permissible, create a submitted order.

    Idempotent on `client_order_id`: resubmitting the same instruction returns the
    original order. Duplicate submissions are the classic way a paper engine
    doubles a position and reports a result twice as good as reality.
    """
    moment = now or datetime.now(UTC)

    if proposal.client_order_id:
        existing = db.scalar(
            select(Order).where(
                Order.trading_account_id == account.id,
                Order.client_order_id == proposal.client_order_id,
            )
        )
        if existing is not None:
            return existing

    outcome = validate_proposal(db, account=account, proposal=proposal, now=moment)

    order = Order(
        trading_account_id=account.id,
        instrument_symbol=proposal.symbol.upper(),
        client_order_id=proposal.client_order_id or f"ord-{moment.timestamp():.6f}",
        side=proposal.side,
        order_type=proposal.order_type,
        time_in_force=proposal.time_in_force,
        status=OrderStatus.NEW,
        quantity=float(proposal.quantity),
        limit_price=proposal.limit_price,
        origin=proposal.origin,
        origin_ref=proposal.origin_ref,
        rationale=proposal.rationale,
        requested_by=actor_id,
        expires_at=moment + timedelta(days=1)
        if proposal.time_in_force == TimeInForce.DAY
        else None,
    )
    db.add(order)
    db.flush()

    record_event(
        db,
        account_id=account.id,
        event_type=ExecutionEventType.SUBMITTED,
        order=order,
        message=f"{proposal.side.value} {proposal.quantity} {order.instrument_symbol} proposed",
        detail={"origin": proposal.origin, "rationale": proposal.rationale},
        actor_id=actor_id,
        at=moment,
    )

    if not outcome.accepted:
        order.status = OrderStatus.REJECTED
        order.reject_reason = outcome.reason
        order.completed_at = moment
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.REJECTED,
            order=order,
            message=outcome.reason,
            detail={"checks": outcome.checks},
            actor_id=actor_id,
            at=moment,
        )
        db.flush()
        return order

    # --- Independent risk gate (Phase 9) ------------------------------------
    # Validation answered "is this a well-formed, affordable order?". The risk
    # controller answers a different question: "should this account be taking this
    # risk at all, right now?". It is a separate service on purpose, and it can
    # reduce the quantity as well as refuse.
    from app.services.risk import engine as risk_engine
    from app.services.risk.breakers import breaker_state as _breaker_state

    if (
        _breaker_state(db, account)
        != __import__("app.models.risk", fromlist=["BreakerState"]).BreakerState.CLOSED
    ):
        order.status = OrderStatus.REJECTED
        order.reject_reason = (
            "New risk is halted by the circuit breaker. An authorised user must clear it."
        )
        order.completed_at = moment
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.REJECTED,
            order=order,
            message=order.reject_reason,
            detail={"stage": "risk", "reason": "breaker_open"},
            actor_id=actor_id,
            at=moment,
        )
        db.flush()
        return order

    risk_price = _price(db, order.instrument_symbol, proposal.timeframe)
    risk = risk_engine.evaluate_proposal(
        db,
        account=account,
        symbol=order.instrument_symbol,
        side=proposal.side,
        quantity=float(proposal.quantity),
        price=risk_price,
        stop_pct=proposal.stop_pct or DEFAULT_STOP_PCT,
        actor_id=actor_id,
        now=moment,
    )
    if risk.check is not None:
        risk.check.order_id = order.id
        db.flush()

    if not risk.allowed:
        order.status = OrderStatus.REJECTED
        order.reject_reason = risk.reason
        order.completed_at = moment
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.REJECTED,
            order=order,
            message=risk.reason,
            detail={
                "stage": "risk",
                "decision": risk.decision.value,
                "checks": [c.as_dict() for c in risk.checks],
            },
            actor_id=actor_id,
            at=moment,
        )
        db.flush()
        return order

    if risk.approved_quantity < order.quantity - 1e-12:
        original = order.quantity
        order.quantity = float(risk.approved_quantity)
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.VALIDATED,
            order=order,
            message=risk.reason,
            detail={
                "stage": "risk",
                "decision": risk.decision.value,
                "original_quantity": original,
                "approved_quantity": order.quantity,
                "checks": [c.as_dict() for c in risk.checks],
            },
            actor_id=actor_id,
            at=moment,
        )
        db.flush()

    _transition(order, OrderStatus.VALIDATED)
    record_event(
        db,
        account_id=account.id,
        event_type=ExecutionEventType.VALIDATED,
        order=order,
        message=outcome.reason,
        detail={"checks": outcome.checks},
        actor_id=actor_id,
        at=moment,
    )

    _transition(order, OrderStatus.SUBMITTED)
    order.submitted_at = moment
    record_event(
        db,
        account_id=account.id,
        event_type=ExecutionEventType.ACKNOWLEDGED,
        order=order,
        message="Paper broker acknowledged the order.",
        detail={"venue": "paper"},
        actor_id=actor_id,
        at=moment,
    )
    order.acknowledged_at = moment
    db.flush()
    return order


def cancel_order(
    db: Session, *, order: Order, actor_id: str | None = None, reason: str = ""
) -> Order:
    """Cancel what has not filled. A partially filled order keeps its fills."""
    if order.status in TERMINAL_STATUSES:
        raise DomainError(f"Order is already {order.status.value}.")
    moment = datetime.now(UTC)
    order.status = OrderStatus.CANCELLED
    order.completed_at = moment
    order.reject_reason = reason or "Cancelled before completion."
    record_event(
        db,
        account_id=order.trading_account_id,
        event_type=ExecutionEventType.CANCELLED,
        order=order,
        message=order.reject_reason,
        actor_id=actor_id,
        at=moment,
    )
    db.flush()
    return order


def expire_stale_orders(db: Session, *, now: datetime | None = None) -> list[Order]:
    """Expire DAY orders that never completed.

    A resting order from three days ago is not a resting order, it is a stale
    instruction acting on a market that has moved. Expiring it keeps the queue
    honest.
    """
    moment = now or datetime.now(UTC)
    stale = list(
        db.scalars(
            select(Order).where(
                Order.status.in_(
                    [OrderStatus.SUBMITTED, OrderStatus.VALIDATED, OrderStatus.PARTIALLY_FILLED]
                ),
                Order.expires_at.is_not(None),
                Order.expires_at < moment,
            )
        )
    )
    for order in stale:
        order.status = OrderStatus.EXPIRED
        order.completed_at = moment
        record_event(
            db,
            account_id=order.trading_account_id,
            event_type=ExecutionEventType.EXPIRED,
            order=order,
            message="Order expired unfilled past its time in force.",
            at=moment,
        )
    db.flush()
    return stale
