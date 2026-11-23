"""Turn a strategy's signal into a sized proposal.

This is the join Phase 7 left open: a strategy produced signals, but nothing could
act on them. Now a *validated* strategy can emit a proposal, which then goes through
exactly the same deterministic validation as a manual order.

Two things this deliberately does **not** do:

- It does not bypass validation. A proposal from a strategy is refused for the same
  reasons a manual one is (stale data, insufficient cash, short selling).
- It does not run on its own. `submit_strategy_proposal` is called by an endpoint a
  human invokes. There is no scheduler and no daemon: the blueprint's autonomous loop
  needs the independent risk controller of Phase 9 before it should exist.

Position sizing is risk-based, matching the backtest: quantity is chosen so a stop-out
costs `risk_per_trade` of equity. That keeps the paper account and the backtest
speaking the same language, so a live-vs-backtest comparison later means something.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import Order, OrderSide, Position, PositionStatus
from app.models.market import Instrument, MarketCandle, Timeframe
from app.models.strategy import Strategy, StrategyStatus
from app.services import ledger_service
from app.services.execution import orders as order_service
from app.services.execution.orders import Proposal
from app.services.strategies import rules

logger = logging.getLogger(__name__)

DEFAULT_RISK_PER_TRADE = 0.01
DEFAULT_STOP_PCT = 0.02


@dataclass
class StrategyProposalResult:
    created: bool
    reason: str
    order: Order | None = None
    signal: str = "flat"
    quantity: float = 0.0


def _bars(db: Session, symbol: str, timeframe: Timeframe, limit: int = 400) -> rules.Bars | None:
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.instrument_id == instrument.id,
                MarketCandle.timeframe == timeframe,
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
        )
    )
    rows.reverse()
    if len(rows) < 2:
        return None
    return rules.Bars(
        opens=[float(row.open) for row in rows],
        highs=[float(row.high) for row in rows],
        lows=[float(row.low) for row in rows],
        closes=[float(row.close) for row in rows],
        volumes=[float(row.volume or 0) for row in rows],
    )


def submit_strategy_proposal(
    db: Session,
    *,
    account: TradingAccount,
    strategy_code: str,
    symbol: str,
    timeframe: Timeframe = Timeframe.D1,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    stop_pct: float = DEFAULT_STOP_PCT,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> StrategyProposalResult:
    """Read the strategy's latest signal and size a proposal accordingly.

    A `validated` strategy is required. A strategy in `testing` has a backtest but no
    acceptance decision, and letting it place orders would make the validation gate
    decorative.
    """
    moment = now or datetime.now(UTC)

    strategy = db.scalar(select(Strategy).where(Strategy.code == strategy_code))
    if strategy is None:
        rules.seed_strategies(db)
        db.flush()
        strategy = db.scalar(select(Strategy).where(Strategy.code == strategy_code))
    if strategy is None:
        return StrategyProposalResult(False, f"Unknown strategy '{strategy_code}'")
    if strategy.status != StrategyStatus.VALIDATED:
        return StrategyProposalResult(
            False,
            f"Strategy '{strategy_code}' is {strategy.status.value}, not validated. A strategy "
            "must pass its acceptance gate and be validated by a human before it can place orders.",
        )

    bars = _bars(db, symbol, timeframe)
    if bars is None:
        return StrategyProposalResult(False, f"No usable bars for {symbol}.")

    spec = rules.REGISTRY.get(strategy_code)
    if spec is None:
        return StrategyProposalResult(False, f"Strategy '{strategy_code}' has no implementation")

    signals = rules.signals_for(strategy_code, bars)
    latest = signals[-1]
    signal_value = latest.direction.value

    position = db.scalar(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.instrument_symbol == symbol.upper(),
            Position.status == PositionStatus.OPEN,
        )
    )
    held = float(position.quantity) if position else 0.0

    price = float(bars.closes[-1])
    equity = float(ledger_service.ledger_cash(db, account))

    if latest.direction.value == "long" and held <= 0:
        quantity = round((equity * risk_per_trade) / (price * stop_pct), 8) if price else 0.0
        if quantity <= 0:
            return StrategyProposalResult(False, "Computed quantity was zero.", signal=signal_value)
        proposal = Proposal(
            symbol=symbol,
            side=OrderSide.BUY,
            quantity=quantity,
            rationale=f"{strategy.name}: {latest.reason}",
            origin=strategy_code,
            client_order_id=f"{strategy_code}:{symbol}:{moment.date()}",
            timeframe=timeframe,
        )
    elif latest.direction.value != "long" and held > 0:
        quantity = held
        proposal = Proposal(
            symbol=symbol,
            side=OrderSide.SELL,
            quantity=quantity,
            rationale=f"{strategy.name}: exit — {latest.reason}",
            origin=strategy_code,
            client_order_id=f"{strategy_code}:{symbol}:exit:{moment.date()}",
            timeframe=timeframe,
        )
    else:
        return StrategyProposalResult(
            False,
            f"No change: strategy is {signal_value} and the account holds {held:.6f} {symbol}.",
            signal=signal_value,
        )

    order = order_service.create_order(
        db, account=account, proposal=proposal, actor_id=actor_id, now=moment
    )
    return StrategyProposalResult(
        created=order.status.value != "rejected",
        reason=order.reject_reason or "Order submitted to the paper broker.",
        order=order,
        signal=signal_value,
        quantity=quantity,
    )
