"""Execution API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.execution import (
    ExecutionEventType,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
    TimeInForce,
)


class OrderRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    side: OrderSide
    quantity: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = Field(default=None, gt=0)
    time_in_force: TimeInForce = TimeInForce.DAY
    rationale: str = Field(default="", max_length=2000)
    client_order_id: str | None = Field(default=None, max_length=64)
    timeframe: str = Field(default="1d", pattern="^(1h|4h|1d|1w)$")
    #: Execute immediately against the paper venue. When false the order is only
    #: validated and submitted, which is how a resting limit would behave.
    execute: bool = True


class FillRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    quantity: float
    price: float
    reference_price: float
    slippage: float
    commission: float
    notional: float
    filled_at: datetime
    venue: str
    ledger_transaction_id: str | None


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: ExecutionEventType
    message: str
    occurred_at: datetime
    actor_id: str | None


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    trading_account_id: str
    instrument_symbol: str
    client_order_id: str
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    status: OrderStatus
    quantity: float
    filled_quantity: float
    limit_price: float | None
    average_fill_price: float | None
    total_commission: float
    total_slippage: float
    reject_reason: str
    origin: str
    rationale: str
    submitted_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class OrderDetail(OrderRead):
    fills: list[FillRead] = []
    events: list[EventRead] = []


class PositionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    instrument_symbol: str
    status: PositionStatus
    quantity: float
    average_entry_price: float
    cost_basis: float
    realised_pnl: float
    total_commission: float
    last_mark_price: float | None
    last_marked_at: datetime | None
    opened_at: datetime
    closed_at: datetime | None
    strategy_code: str | None
    opening_rationale: str
    #: Derived, never stored: a stale mark must not masquerade as a valuation.
    market_value: float | None = None
    unrealised_pnl: float | None = None


class PositionDetail(PositionRead):
    unrealised_pct: float | None = None


class ExecutionOverview(BaseModel):
    open_positions: int
    closed_positions: int
    orders_total: int
    orders_rejected: int
    fills_total: int
    cash: float
    positions_value: float
    unrealised_pnl: float
    realised_pnl: float
    equity: float
    total_commission: float
    total_slippage: float
    rejection_rate: float
    reconciliation: dict


class StrategyOrderRequest(BaseModel):
    strategy_code: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(default="1d", pattern="^(1h|4h|1d|1w)$")
    risk_per_trade: float = Field(default=0.01, gt=0, le=0.25)
    stop_pct: float = Field(default=0.02, gt=0, le=0.5)
