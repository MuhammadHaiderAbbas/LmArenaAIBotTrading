"""Analytics API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class MetricRead(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    definition: str
    note: str


class CurvePoint(BaseModel):
    at: str
    equity: float


class TradeRead(BaseModel):
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    realised_pnl: float
    commission: float
    holding_days: float
    return_pct: float
    opened_at: datetime
    closed_at: datetime
    strategy_code: str | None


class CostBreakdown(BaseModel):
    commission: float
    slippage: float
    total: float
    orders: int
    fills: int
    rejected_orders: int


class Attribution(BaseModel):
    by_symbol: dict[str, float]
    by_strategy: dict[str, float]


class PerformanceReport(BaseModel):
    as_of: datetime
    equity: float
    cash: float
    starting_equity: float
    net_return: float | None
    metrics: list[MetricRead]
    equity_curve: list[CurvePoint]
    benchmark_curve: list[CurvePoint]
    benchmark_return: float | None
    attribution: Attribution
    costs: CostBreakdown
    trades: list[TradeRead]
    notes: list[str]
    #: Stated in the contract so no client can present this as a forecast.
    forward_looking: bool = False
