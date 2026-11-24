"""Risk API contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.execution import OrderSide


class RiskPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: int
    is_active: bool
    max_risk_per_trade: float
    max_daily_loss: float
    max_drawdown: float
    max_positions: int
    max_exposure: float
    max_position_concentration: float
    max_correlation: float
    max_spread_bps: float
    max_orders_per_hour: int
    leverage_enabled: bool
    notes: str


class RiskPolicyUpdate(BaseModel):
    """Partial update. Every field is optional because tightening one limit at a time
    is the normal case; every provided field is validated to be a sane fraction."""

    max_risk_per_trade: float | None = Field(default=None, gt=0, le=0.10)
    max_daily_loss: float | None = Field(default=None, gt=0, le=0.50)
    max_drawdown: float | None = Field(default=None, gt=0, le=0.90)
    max_positions: int | None = Field(default=None, ge=1, le=100)
    max_exposure: float | None = Field(default=None, gt=0, le=3.0)
    max_position_concentration: float | None = Field(default=None, gt=0, le=1.0)
    max_correlation: float | None = Field(default=None, gt=0, le=1.0)
    max_spread_bps: float | None = Field(default=None, gt=0, le=500)
    max_orders_per_hour: int | None = Field(default=None, ge=1, le=1000)
    leverage_enabled: bool | None = None
    reason: str = Field(default="", max_length=500)


class RiskStatus(BaseModel):
    breaker_state: Literal["closed", "open", "manual"]
    halted_new_risk: bool
    breaker_trigger: str | None
    breaker_reason: str | None
    breaker_since: str | None
    equity: float
    exposure: float
    open_positions: int
    drawdown: float
    daily_pnl: float
    daily_loss_fraction: float
    policy: RiskPolicyRead
    applied_rules: list[str]
    advisory_rules: list[str]
    positions_affected: int
    orders_cancelled: int
    #: Always false: the shutdown halts new risk and never liquidates. Stated in the
    #: contract so a client cannot infer otherwise from a green "stopped" light.
    closes_positions: bool


class RiskCheckRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    order_id: str | None
    instrument_symbol: str
    side: str
    requested_quantity: float
    approved_quantity: float
    decision: str
    reason: str
    breached_rules: list[str]
    checks: list[dict[str, Any]]
    equity_snapshot: float
    exposure_snapshot: float
    open_positions_snapshot: int
    created_at: datetime


class RiskEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    severity: str
    message: str
    detail: dict[str, Any]
    created_at: datetime


class BreakerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    state: str
    trigger: str
    reason: str
    halted_new_risk: bool
    positions_affected: int
    orders_cancelled: int
    tripped_at: datetime
    cleared_at: datetime | None
    cleared_by: str | None


class RiskEvaluateRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    side: OrderSide
    quantity: float = Field(gt=0)
    price: float | None = Field(default=None, gt=0)
    stop_pct: float = Field(default=0.02, gt=0, le=0.5)


class RiskEvaluateResponse(BaseModel):
    decision: Literal["allow", "allow_reduced", "reject"]
    approved_quantity: float
    reason: str
    breached_rules: list[str]
    checks: list[dict[str, Any]]
    equity: float
    exposure: float
    open_positions: int


class EmergencyStopRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class RiskOverview(BaseModel):
    breaker_state: str
    checks_total: int
    rejected_total: int
    reduced_total: int
    rejection_rate: float
    breach_counts: dict[str, int]
    policy_version: int
    limits: dict[str, float]
    recent_events: list[RiskEventRead]
    rules_supported: list[str]
    rules_not_implemented: list[str]
