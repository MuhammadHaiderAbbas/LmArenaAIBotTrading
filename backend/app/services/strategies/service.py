"""Backtest orchestration: load bars, run the simulator, persist the evidence.

Two rules, mirroring the model registry:

- A completed backtest writes `StrategyStatus.TESTING`. It never marks a strategy
  `validated` and never sets `live_eligible`. Validation is a separate, staff-only
  act with its own record, exactly as promotion is for models.
- Every run stores the cost model it used. A result whose assumptions are not
  recorded is an anecdote.
"""

from __future__ import annotations

import json
import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import Instrument, MarketCandle, Timeframe
from app.models.strategy import (
    Backtest,
    BacktestEquityPoint,
    BacktestMetrics,
    BacktestStatus,
    BacktestTrade,
    Strategy,
    StrategyStatus,
)
from app.services.strategies import rules, simulator

logger = logging.getLogger(__name__)

BARS_PER_YEAR: dict[str, float] = {
    "1h": 24 * 252,
    "4h": 6 * 252,
    "1d": 252,
    "1w": 52,
}


def _load_bars(db: Session, symbol: str, timeframe: str, limit: int):
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if instrument is None:
        raise ValueError(f"Unknown instrument '{symbol}'")
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.instrument_id == instrument.id,
                MarketCandle.timeframe == Timeframe(timeframe),
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    return rows


def run_backtest(
    db: Session,
    *,
    strategy_code: str,
    symbol: str,
    timeframe: str = "1d",
    limit: int = 1500,
    starting_equity: float = 10_000.0,
    spread_bps: float = 1.0,
    commission_bps: float = 0.5,
    slippage_bps: float = 0.5,
    financing_bps_per_day: float = 0.0,
    risk_per_trade: float = 0.01,
    is_out_of_sample: bool = False,
    requested_by: str | None = None,
) -> Backtest:
    started = time.perf_counter()

    strategy = db.scalar(select(Strategy).where(Strategy.code == strategy_code))
    if strategy is None:
        # Shipped strategies are code, not user data. Seed once on first use so a
        # backtest does not depend on the caller having visited the catalogue.
        rules.seed_strategies(db)
        db.flush()
        strategy = db.scalar(select(Strategy).where(Strategy.code == strategy_code))
    if strategy is None:
        raise ValueError(f"Unknown strategy '{strategy_code}'")
    spec = rules.REGISTRY.get(strategy_code)
    if spec is None:
        raise ValueError(f"Strategy '{strategy_code}' is registered but has no implementation")

    backtest = Backtest(
        strategy_id=strategy.id,
        instrument_symbol=symbol,
        timeframe=timeframe,
        status=BacktestStatus.RUNNING,
        starting_equity=starting_equity,
        spread_bps=spread_bps,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        financing_bps_per_day=financing_bps_per_day,
        risk_per_trade=risk_per_trade,
        is_out_of_sample=is_out_of_sample,
        parameters_snapshot=json.dumps(spec.parameters),
        requested_by=requested_by,
    )
    db.add(backtest)
    db.flush()

    try:
        candles = _load_bars(db, symbol, timeframe, limit)
    except ValueError as exc:
        backtest.status = BacktestStatus.REFUSED
        backtest.refusal_reason = str(exc)
        db.flush()
        return backtest

    if len(candles) < spec.min_history_bars:
        backtest.status = BacktestStatus.REFUSED
        backtest.bars_used = len(candles)
        backtest.refusal_reason = (
            f"{len(candles)} bars of {symbol} on {timeframe} are available; "
            f"{strategy_code} needs {spec.min_history_bars} before the first decision "
            "is meaningful. Refusing rather than reporting a result from a sample this small."
        )
        db.flush()
        return backtest

    bars = rules.Bars(
        opens=[float(row.open) for row in candles],
        highs=[float(row.high) for row in candles],
        lows=[float(row.low) for row in candles],
        closes=[float(row.close) for row in candles],
        volumes=[float(row.volume or 0) for row in candles],
    )
    timestamps = [row.open_time for row in candles]

    cost_model = simulator.CostModel(
        spread_bps=spread_bps,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        financing_bps_per_day=financing_bps_per_day,
        starting_equity=starting_equity,
        risk_per_trade=risk_per_trade,
    )

    # Warmup covers the longest lookback the rules need, so no decision is taken on
    # a partially-formed indicator.
    warmup = min(spec.min_history_bars, len(bars) // 3)
    signals = rules.signals_for(strategy_code, bars, json.loads(backtest.parameters_snapshot))
    result = simulator.simulate(
        bars, timestamps, signals, cost_model, warmup_bars=warmup, use_stop=spec.use_stop
    )

    if result.refused:
        backtest.status = BacktestStatus.REFUSED
        backtest.refusal_reason = result.refusal_reason
        db.flush()
        return backtest

    final_equity = result.equity[-1].equity if result.equity else starting_equity
    baseline_return = _baseline_return(db, symbol, timeframe, limit, starting_equity, spec, warmup)

    metrics = simulator.compute_metrics(
        result,
        starting_equity=starting_equity,
        final_equity=final_equity,
        baseline_return=baseline_return,
        bars_per_year=BARS_PER_YEAR.get(timeframe, 252.0),
    )

    backtest.bars_used = result.bars_used
    backtest.warmup_bars = result.warmup_bars
    backtest.data_start = candles[0].open_time
    backtest.data_end = candles[-1].open_time
    backtest.status = BacktestStatus.COMPLETED
    backtest.duration_ms = int((time.perf_counter() - started) * 1000)

    for sequence, trade in enumerate(result.trades):
        db.add(
            BacktestTrade(
                backtest_id=backtest.id,
                sequence=sequence,
                direction=trade.direction,
                quantity=trade.quantity,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                stop_price=trade.stop_price,
                target_price=trade.target_price,
                gross_pnl=trade.gross_pnl,
                costs=trade.costs,
                net_pnl=trade.net_pnl,
                return_pct=trade.return_pct,
                bars_held=trade.bars_held,
                exit_reason=trade.exit_reason,
                entry_reason=trade.entry_reason,
            )
        )

    for point in result.equity:
        db.add(
            BacktestEquityPoint(
                backtest_id=backtest.id,
                at=point.at,
                equity=point.equity,
                drawdown=point.drawdown,
                in_position=point.in_position,
            )
        )

    db.add(BacktestMetrics(backtest_id=backtest.id, **metrics))

    # A completed backtest moves a draft strategy to "testing" and no further.
    if strategy.status == StrategyStatus.DRAFT:
        strategy.status = StrategyStatus.TESTING
    db.flush()
    return backtest


def _baseline_return(
    db: Session,
    symbol: str,
    timeframe: str,
    limit: int,
    starting_equity: float,
    spec: rules.StrategySpec,
    warmup: int,
) -> float:
    """Buy-and-hold over the identical window, with identical costs.

    Computed from the same candles rather than passed in, so the benchmark cannot
    drift away from the sample it is meant to describe.
    """
    if spec.code == "buy_and_hold_v1":
        return 0.0
    candles = _load_bars(db, symbol, timeframe, limit)
    if len(candles) < 3:
        return 0.0
    bars = rules.Bars(
        opens=[float(row.open) for row in candles],
        highs=[float(row.high) for row in candles],
        lows=[float(row.low) for row in candles],
        closes=[float(row.close) for row in candles],
    )
    timestamps = [row.open_time for row in candles]
    cost_model = simulator.CostModel(starting_equity=starting_equity)
    baseline = simulator.simulate(
        bars,
        timestamps,
        rules.signals_for("buy_and_hold_v1", bars),
        cost_model,
        warmup_bars=warmup,
        use_stop=False,
    )
    if not baseline.equity:
        return 0.0
    return (baseline.equity[-1].equity - starting_equity) / starting_equity


def validate_strategy(db: Session, *, strategy_id: str, actor_id: str, note: str) -> Strategy:
    """Mark a strategy validated — staff-only, and requires a passing backtest.

    This is the Phase 7 equivalent of model promotion. It still does not set
    `live_eligible`: nothing can trade until the execution engine exists in Phase 8,
    and the risk controller in Phase 9 is what will stand between a strategy and an
    order.
    """
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("Strategy not found")

    passing = db.scalar(
        select(Backtest)
        .join(BacktestMetrics, BacktestMetrics.backtest_id == Backtest.id)
        .where(Backtest.strategy_id == strategy.id, BacktestMetrics.passes_gate.is_(True))
        .order_by(Backtest.created_at.desc())
    )
    if passing is None:
        raise ValueError(
            "Validation refused: no completed backtest of this strategy has passed the "
            "acceptance gate. A strategy cannot be validated by intent."
        )

    strategy.status = StrategyStatus.VALIDATED
    logger.info("strategy %s validated by %s: %s", strategy.code, actor_id, note)
    db.flush()
    return strategy


def retire_strategy(db: Session, *, strategy_id: str) -> Strategy:
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise ValueError("Strategy not found")
    strategy.status = StrategyStatus.RETIRED
    strategy.live_eligible = False
    db.flush()
    return strategy
