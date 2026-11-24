"""Strategy definitions, backtest runs and their trade/equity records.

Phase 7's job is to answer one question honestly: *after realistic costs, does this
strategy have an edge?* The schema is built around the three ways that answer gets
faked, so each has a structural defence:

- **Look-ahead.** A backtest row stores the exact bar window and the `bar_offset`
  at which a decision is acted on (next bar's open). Nothing is filled at the price
  that generated the signal.
- **Ignoring costs.** Every `BacktestTrade` stores gross and net P&L separately, so a
  strategy that only works before costs is visible as such instead of collapsing into
  one flattering number.
- **Cherry-picking.** Runs carry `is_out_of_sample` and a benchmark comparison. The
  metrics table has `baseline_return` next to `net_return` for the same reason the
  model registry carries `baseline_accuracy`.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class StrategyFamily(str, enum.Enum):
    """Implemented families only. A family without code cannot be backtested."""

    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    BUY_AND_HOLD = "buy_and_hold"


class StrategyStatus(str, enum.Enum):
    DRAFT = "draft"
    TESTING = "testing"
    VALIDATED = "validated"
    RETIRED = "retired"


class BacktestStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUSED = "refused"


class ExitReason(str, enum.Enum):
    STOP = "stop"
    TARGET = "target"
    SIGNAL = "signal"
    TIME = "time"
    END_OF_DATA = "end_of_data"


class Strategy(Base, TimestampMixin):
    """A rule set, with its hypothesis stated before the results are known.

    The `hypothesis` field exists so the write-up cannot be reverse-engineered from
    the equity curve, which is how most strategy documentation gets written.
    """

    __tablename__ = "strategies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    family: Mapped[StrategyFamily] = mapped_column(
        Enum(StrategyFamily, native_enum=False, length=24), nullable=False
    )
    status: Mapped[StrategyStatus] = mapped_column(
        Enum(StrategyStatus, native_enum=False, length=16),
        default=StrategyStatus.DRAFT,
        server_default="DRAFT",
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    hypothesis: Mapped[str] = mapped_column(Text, default="", server_default="")
    parameters: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    min_history_bars: Mapped[int] = mapped_column(
        Integer, default=220, server_default="220", nullable=False
    )
    # Strategies cannot be enabled by a backtest. Promotion is a separate act, as
    # with models: Phase 8 is the first phase that could act on one.
    live_eligible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    deterministic: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1", nullable=False
    )

    backtests: Mapped[list[Backtest]] = relationship(back_populates="strategy")


class Backtest(Base, TimestampMixin):
    __tablename__ = "backtests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    strategy_id: Mapped[str] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instrument_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), nullable=False, server_default="1d")
    status: Mapped[BacktestStatus] = mapped_column(
        Enum(BacktestStatus, native_enum=False, length=16),
        default=BacktestStatus.PENDING,
        server_default="PENDING",
        nullable=False,
    )

    # --- cost model, stored with the result so it can never be re-derived wrongly
    starting_equity: Mapped[float] = mapped_column(Float, nullable=False, server_default="10000")
    spread_bps: Mapped[float] = mapped_column(Float, nullable=False, server_default="1.0")
    commission_bps: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    slippage_bps: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.5")
    financing_bps_per_day: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    risk_per_trade: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.01")

    # --- data window, so a result can be reproduced exactly
    bars_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    warmup_bars: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    data_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    data_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_out_of_sample: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")

    parameters_snapshot: Mapped[str] = mapped_column(Text, nullable=False, server_default="{}")
    refusal_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    strategy: Mapped[Strategy] = relationship(back_populates="backtests")
    trades: Mapped[list[BacktestTrade]] = relationship(
        back_populates="backtest", cascade="all, delete-orphan"
    )
    metrics: Mapped[BacktestMetrics | None] = relationship(
        back_populates="backtest", cascade="all, delete-orphan", uselist=False
    )
    equity_points: Mapped[list[BacktestEquityPoint]] = relationship(
        back_populates="backtest", cascade="all, delete-orphan"
    )


class BacktestTrade(Base, TimestampMixin):
    """One round trip. Gross and net are separate columns on purpose."""

    __tablename__ = "backtest_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # long | short
    quantity: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    exit_price: Mapped[float] = mapped_column(Float, nullable=False)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    gross_pnl: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    costs: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    net_pnl: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    return_pct: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    bars_held: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    exit_reason: Mapped[ExitReason] = mapped_column(
        Enum(ExitReason, native_enum=False, length=16),
        default=ExitReason.SIGNAL,
        server_default="SIGNAL",
        nullable=False,
    )
    entry_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    backtest: Mapped[Backtest] = relationship(back_populates="trades")


class BacktestMetrics(Base, TimestampMixin):
    """The blueprint's acceptance metrics, with the benchmark beside them."""

    __tablename__ = "backtest_metrics"
    __table_args__ = (UniqueConstraint("backtest_id", name="uq_backtest_metrics_backtest"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False, index=True
    )

    trades: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    win_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)

    gross_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    net_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_over_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_costs: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    cost_drag: Mapped[float | None] = mapped_column(Float, nullable=True)

    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    sortino: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)

    exposure: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_bars_held: Mapped[float | None] = mapped_column(Float, nullable=True)

    #: Set when the sample is too small or the edge too thin to claim anything.
    verdict: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    passes_gate: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")

    backtest: Mapped[Backtest] = relationship(back_populates="metrics")


class BacktestEquityPoint(Base, TimestampMixin):
    """Daily equity, so drawdown can be read off the curve rather than trusted."""

    __tablename__ = "backtest_equity_points"
    __table_args__ = (Index("ix_equity_backtest_time", "backtest_id", "at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    backtest_id: Mapped[str] = mapped_column(
        ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False
    )
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    drawdown: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    in_position: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="0")

    backtest: Mapped[Backtest] = relationship(back_populates="equity_points")
