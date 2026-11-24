"""Order, fill and position endpoints for the paper execution engine.

Route ordering matters here: `/overview` and `/positions` are declared before
`/{order_id}` so a static path is never swallowed by the dynamic one.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession
from app.core.rate_limit import enforce_rate_limit
from app.models.account import TradingAccount
from app.models.execution import (
    ExecutionEvent,
    ExecutionEventType,
    Order,
    OrderFill,
    OrderStatus,
    Position,
    PositionStatus,
)
from app.models.market import Timeframe
from app.schemas.execution import (
    EventRead,
    ExecutionOverview,
    FillRead,
    OrderDetail,
    OrderRead,
    OrderRequest,
    PositionDetail,
    PositionRead,
    StrategyOrderRequest,
)
from app.services import ledger_service
from app.services.execution import broker
from app.services.execution import orders as order_service
from app.services.execution.orders import Proposal
from app.services.execution.strategy_bridge import submit_strategy_proposal

router = APIRouter(prefix="/execution", tags=["execution"])

#: A paper broker is not a market. Anything beyond this in one window is a client
#: bug or an abuse attempt, and either way the right answer is to slow it down.
MAX_ORDERS_PER_MINUTE = 20


def _account(db: Session, user, account_id: str | None) -> TradingAccount:
    """Resolve the account and enforce ownership on every call."""
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


def _order_detail(db: Session, order: Order) -> OrderDetail:
    detail = OrderDetail.model_validate(order)
    detail.fills = [FillRead.model_validate(fill) for fill in order.fills]
    detail.events = [
        EventRead.model_validate(event)
        for event in db.scalars(
            select(ExecutionEvent)
            .where(ExecutionEvent.order_id == order.id)
            .order_by(ExecutionEvent.occurred_at)
        )
    ]
    return detail


def _position_read(db: Session, position: Position) -> PositionRead:
    payload = PositionRead.model_validate(position)
    if position.status == PositionStatus.OPEN and position.last_mark_price:
        payload.market_value = round(position.quantity * position.last_mark_price, 4)
        payload.unrealised_pnl = round(
            (position.last_mark_price - position.average_entry_price) * position.quantity, 4
        )
    return payload


# ------------------------------------------------------------------- overview


@router.get("/overview", response_model=ExecutionOverview)
def overview(db: DbSession, user: CurrentUser, account_id: str | None = None) -> ExecutionOverview:
    account = _account(db, user, account_id)
    broker.mark_positions(db, account=account)
    db.commit()

    open_positions = list(
        db.scalars(
            select(Position).where(
                Position.trading_account_id == account.id,
                Position.status == PositionStatus.OPEN,
            )
        )
    )
    closed_count = len(
        list(
            db.scalars(
                select(Position).where(
                    Position.trading_account_id == account.id,
                    Position.status == PositionStatus.CLOSED,
                )
            )
        )
    )
    orders = list(db.scalars(select(Order).where(Order.trading_account_id == account.id)))
    rejected = [order for order in orders if order.status == OrderStatus.REJECTED]
    fills = list(
        db.scalars(
            select(OrderFill)
            .join(Order, Order.id == OrderFill.order_id)
            .where(Order.trading_account_id == account.id)
        )
    )

    cash = float(ledger_service.ledger_cash(db, account))
    positions_value = sum(
        position.quantity * (position.last_mark_price or 0) for position in open_positions
    )
    unrealised = sum(
        (position.last_mark_price or position.average_entry_price) * position.quantity
        - position.cost_basis
        for position in open_positions
    )
    realised = sum(
        position.realised_pnl
        for position in db.scalars(
            select(Position).where(Position.trading_account_id == account.id)
        )
    )

    return ExecutionOverview(
        open_positions=len(open_positions),
        closed_positions=closed_count,
        orders_total=len(orders),
        orders_rejected=len(rejected),
        fills_total=len(fills),
        cash=round(cash, 4),
        positions_value=round(positions_value, 4),
        unrealised_pnl=round(unrealised, 4),
        realised_pnl=round(realised, 4),
        equity=round(cash + positions_value, 4),
        total_commission=round(sum(fill.commission for fill in fills), 4),
        total_slippage=round(sum(fill.slippage for fill in fills), 4),
        rejection_rate=round(len(rejected) / len(orders), 4) if orders else 0.0,
        reconciliation=broker.reconcile_positions(db, account=account),
    )


# ------------------------------------------------------------------ positions


@router.get("/positions", response_model=list[PositionRead])
def list_positions(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    status_filter: str = Query(default="open", alias="status"),
) -> list[PositionRead]:
    account = _account(db, user, account_id)
    statement = select(Position).where(Position.trading_account_id == account.id)
    if status_filter in {"open", "closed"}:
        statement = statement.where(Position.status == PositionStatus(status_filter))
    broker.mark_positions(db, account=account)
    db.commit()
    return [_position_read(db, position) for position in db.scalars(statement)]


@router.get("/positions/{position_id}", response_model=PositionDetail)
def get_position(position_id: str, db: DbSession, user: CurrentUser) -> PositionDetail:
    position = db.get(Position, position_id)
    if position is None:
        raise HTTPException(status_code=404, detail="Position not found")
    account = db.get(TradingAccount, position.trading_account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="Position not found")
    base = _position_read(db, position)
    detail = PositionDetail.model_validate(base, from_attributes=True)
    if base.unrealised_pnl is not None and position.cost_basis:
        detail.unrealised_pct = round(base.unrealised_pnl / position.cost_basis, 6)
    return detail


# --------------------------------------------------------------------- orders


@router.get("/orders", response_model=list[OrderRead])
def list_orders(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[OrderRead]:
    account = _account(db, user, account_id)
    statement = (
        select(Order)
        .where(Order.trading_account_id == account.id)
        .order_by(Order.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        try:
            statement = statement.where(Order.status == OrderStatus(status_filter))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"Unknown status '{status_filter}'"
            ) from exc
    return [OrderRead.model_validate(order) for order in db.scalars(statement)]


@router.get("/orders/{order_id}", response_model=OrderDetail)
def get_order(order_id: str, db: DbSession, user: CurrentUser) -> OrderDetail:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    account = db.get(TradingAccount, order.trading_account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    return _order_detail(db, order)


@router.post("/orders", response_model=OrderDetail, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderRequest, request: Request, db: DbSession, user: CurrentUser
) -> OrderDetail:
    """Submit a proposal. Validation is deterministic; a rejection is a normal outcome."""
    enforce_rate_limit(request, "execution_orders", limit=MAX_ORDERS_PER_MINUTE, window_seconds=60)
    account = _account(db, user, None)

    proposal = Proposal(
        symbol=payload.symbol,
        side=payload.side,
        quantity=payload.quantity,
        order_type=payload.order_type,
        limit_price=payload.limit_price,
        time_in_force=payload.time_in_force,
        rationale=payload.rationale,
        origin="manual",
        client_order_id=payload.client_order_id,
        timeframe=Timeframe(payload.timeframe),
    )
    order = order_service.create_order(db, account=account, proposal=proposal, actor_id=user.id)

    if payload.execute and order.status not in (
        OrderStatus.REJECTED,
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
    ):
        try:
            broker.execute_order(db, account=account, order=order, actor_id=user.id)
        except Exception as exc:  # noqa: BLE001 - a fill failure must not lose the order
            order_service.record_event(
                db,
                account_id=account.id,
                event_type=ExecutionEventType.REJECTED,
                order=order,
                message=f"Execution failed: {exc}",
                actor_id=user.id,
            )
            db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    db.commit()
    db.refresh(order)
    return _order_detail(db, order)


@router.post("/orders/{order_id}/cancel", response_model=OrderDetail)
def cancel_order(order_id: str, db: DbSession, user: CurrentUser) -> OrderDetail:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    account = db.get(TradingAccount, order.trading_account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="Order not found")
    try:
        order_service.cancel_order(db, order=order, actor_id=user.id, reason="Cancelled by user.")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    db.refresh(order)
    return _order_detail(db, order)


@router.post("/strategy-orders", response_model=OrderDetail, status_code=status.HTTP_201_CREATED)
def strategy_order(
    payload: StrategyOrderRequest, request: Request, db: DbSession, user: CurrentUser
) -> OrderDetail:
    """Ask a validated strategy for a proposal and submit it.

    Refused for an unvalidated strategy: the validation gate would be decorative if
    any backtested rule could place orders.
    """
    enforce_rate_limit(request, "execution_strategy", limit=10, window_seconds=60)
    account = _account(db, user, None)
    outcome = submit_strategy_proposal(
        db,
        account=account,
        strategy_code=payload.strategy_code,
        symbol=payload.symbol,
        timeframe=Timeframe(payload.timeframe),
        risk_per_trade=payload.risk_per_trade,
        stop_pct=payload.stop_pct,
        actor_id=user.id,
    )
    if outcome.order is None:
        db.commit()
        raise HTTPException(status_code=422, detail=outcome.reason)

    if outcome.order.status not in (OrderStatus.REJECTED, OrderStatus.FILLED):
        broker.execute_order(db, account=account, order=outcome.order, actor_id=user.id)
    db.commit()
    db.refresh(outcome.order)
    return _order_detail(db, outcome.order)


@router.get("/events", response_model=list[EventRead])
def list_events(
    db: DbSession,
    user: CurrentUser,
    account_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[EventRead]:
    account = _account(db, user, account_id)
    events = db.scalars(
        select(ExecutionEvent)
        .where(ExecutionEvent.trading_account_id == account.id)
        .order_by(ExecutionEvent.occurred_at.desc())
        .limit(limit)
    )
    return [EventRead.model_validate(event) for event in events]
