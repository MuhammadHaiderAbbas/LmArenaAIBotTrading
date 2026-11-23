"""Strategy and backtest API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.strategy import BacktestStatus, ExitReason, StrategyFamily, StrategyStatus


class StrategyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    family: StrategyFamily
    status: StrategyStatus
    description: str
    hypothesis: str
    parameters: str
    min_history_bars: int
    live_eligible: bool
    deterministic: bool


class BacktestRequest(BaseModel):
    strategy_code: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(default="1d", pattern="^(1h|4h|1d|1w)$")
    limit: int = Field(default=1500, ge=200, le=5000)
    starting_equity: float = Field(default=10_000.0, gt=0, le=10_000_000)
    spread_bps: float = Field(default=1.0, ge=0, le=200)
    commission_bps: float = Field(default=0.5, ge=0, le=200)
    slippage_bps: float = Field(default=0.5, ge=0, le=200)
    financing_bps_per_day: float = Field(default=0.0, ge=0, le=100)
    risk_per_trade: float = Field(default=0.01, gt=0, le=0.25)
    is_out_of_sample: bool = False


class TradeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    direction: str
    quantity: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float | None
    target_price: float | None
    gross_pnl: float
    costs: float
    net_pnl: float
    return_pct: float
    bars_held: int
    exit_reason: ExitReason
    entry_reason: str


class MetricsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    trades: int
    win_rate: float | None
    profit_factor: float | None
    average_win: float | None
    average_loss: float | None
    expectancy: float | None
    gross_return: float | None
    net_return: float | None
    baseline_return: float | None
    edge_over_baseline: float | None
    total_costs: float
    cost_drag: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float | None
    volatility: float | None
    exposure: float | None
    turnover: float | None
    average_bars_held: float | None
    verdict: str
    passes_gate: bool


class EquityPointRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    at: datetime
    equity: float
    drawdown: float
    in_position: bool


class BacktestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    strategy_id: str
    instrument_symbol: str
    timeframe: str
    status: BacktestStatus
    starting_equity: float
    spread_bps: float
    commission_bps: float
    slippage_bps: float
    financing_bps_per_day: float
    risk_per_trade: float
    bars_used: int
    warmup_bars: int
    data_start: datetime | None
    data_end: datetime | None
    is_out_of_sample: bool
    refusal_reason: str
    duration_ms: int
    created_at: datetime


class BacktestDetail(BacktestRead):
    strategy_code: str = ""
    metrics: MetricsRead | None = None
    trades: list[TradeRead] = []
    equity: list[EquityPointRead] = []
    #: The counts that keep a good-looking equity curve honest.
    trade_summary: dict = {}


class ValidationRequest(BaseModel):
    note: str = Field(default="", max_length=1000)


class StrategyOverview(BaseModel):
    strategies_total: int
    validated: int
    testing: int
    drafts: int
    backtests_total: int
    backtests_passing: int
    best_edge: float | None
    live_eligible: int
