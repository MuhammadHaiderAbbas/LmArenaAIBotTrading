"""Portfolio measurements the risk rules read.

Every metric here is derived from stored state — the ledger, the position book and
the order history — rather than kept in a counter that could drift. A risk limit is
only as trustworthy as the number it is compared against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import Order, OrderSide, OrderStatus, Position, PositionStatus
from app.models.ledger import LedgerAccount, LedgerAccountKind, LedgerEntry, LedgerTransaction
from app.models.market import Instrument, MarketCandle, Timeframe
from app.services import ledger_service


def latest_price(db: Session, symbol: str, timeframe: Timeframe = Timeframe.D1) -> float | None:
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    candle = db.scalar(
        select(MarketCandle)
        .where(MarketCandle.instrument_id == instrument.id, MarketCandle.timeframe == timeframe)
        .order_by(MarketCandle.open_time.desc())
        .limit(1)
    )
    return float(candle.close) if candle else None


def open_positions(db: Session, account: TradingAccount) -> list[Position]:
    return list(
        db.scalars(
            select(Position).where(
                Position.trading_account_id == account.id,
                Position.status == PositionStatus.OPEN,
            )
        )
    )


def positions_value(db: Session, account: TradingAccount) -> float:
    """Marked value of the open book. Falls back to cost basis when unmarked."""
    total = 0.0
    for position in open_positions(db, account):
        price = position.last_mark_price or latest_price(db, position.instrument_symbol)
        if price is None:
            total += position.cost_basis
        else:
            total += price * position.quantity
    return total


def equity(db: Session, account: TradingAccount) -> float:
    return float(ledger_service.ledger_cash(db, account)) + positions_value(db, account)


def exposure(db: Session, account: TradingAccount) -> float:
    """Invested value as a fraction of equity. Leverage is off, so this cannot exceed 1."""
    total_equity = equity(db, account)
    if total_equity <= 0:
        return 1.0 if positions_value(db, account) > 0 else 0.0
    return positions_value(db, account) / total_equity


def realised_pnl(db: Session, account: TradingAccount) -> float:
    rows = db.scalars(select(Position).where(Position.trading_account_id == account.id))
    return sum(position.realised_pnl for position in rows)


def daily_pnl(db: Session, account: TradingAccount, *, now: datetime | None = None) -> float:
    """Today's realised P&L, from the position ledger rather than a running total.

    Unrealised movement is deliberately excluded: a daily-loss limit triggered by a
    mark that recovers by the close would halt an account for no realised reason. The
    breaker for open-risk deterioration is drawdown, not daily loss.
    """
    moment = now or datetime.now(UTC)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.closed_at.is_not(None),
            Position.closed_at >= start,
        )
    )
    return sum(position.realised_pnl for position in rows)


def equity_curve(db: Session, account: TradingAccount, *, max_points: int = 400) -> list[float]:
    """Running cash after each transaction, oldest first.

    Sampled **per transaction**, not per day. A daily sample collapses a funding and a
    loss on the same day into a single number and the high-water mark is never seen —
    which makes drawdown read zero and silently disables the limit. Cash rather than
    marked equity because marking historical days would need position snapshots, and a
    drawdown limit computed from a re-marked curve is not reproducible.
    """
    rows = db.execute(
        select(LedgerTransaction.created_at, LedgerEntry.amount)
        .join(LedgerEntry, LedgerEntry.transaction_id == LedgerTransaction.id)
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.trading_account_id == account.id,
            LedgerAccount.kind == LedgerAccountKind.CASH,
        )
        .order_by(LedgerTransaction.created_at)
    ).all()

    if not rows:
        return []

    running = 0.0
    curve: list[float] = []
    for _created_at, amount in rows:
        running += float(amount)
        curve.append(round(running, 4))

    return curve[-max_points:] if len(curve) > max_points else curve


def peak_equity(db: Session, account: TradingAccount) -> float:
    curve = equity_curve(db, account)
    current = equity(db, account)
    return max([current, *curve])


def current_drawdown(db: Session, account: TradingAccount) -> float:
    """Drawdown from the running peak, as a positive fraction (0.12 = 12% down)."""
    peak = peak_equity(db, account)
    if peak <= 0:
        return 0.0
    current = equity(db, account)
    return max(0.0, (peak - current) / peak)


def orders_last_hour(db: Session, account: TradingAccount, *, now: datetime | None = None) -> int:
    moment = now or datetime.now(UTC)
    window = moment - timedelta(hours=1)
    rows = db.scalars(
        select(Order).where(
            Order.trading_account_id == account.id,
            Order.created_at >= window,
            Order.status != OrderStatus.REJECTED,
        )
    )
    return len(list(rows))


def position_for(db: Session, account: TradingAccount, symbol: str) -> Position | None:
    return db.scalar(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.instrument_symbol == symbol.upper(),
            Position.status == PositionStatus.OPEN,
        )
    )


def realised_today_by_symbol(
    db: Session, account: TradingAccount, symbol: str, *, now: datetime | None = None
) -> float:
    moment = now or datetime.now(UTC)
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = db.scalars(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.instrument_symbol == symbol.upper(),
            Position.closed_at.is_not(None),
            Position.closed_at >= start,
        )
    )
    return sum(position.realised_pnl for position in rows)


def recent_returns(db: Session, symbol: str, *, bars: int = 60) -> list[float]:
    """Simple close-to-close returns, used for the correlation rule."""
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        return []
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.instrument_id == instrument.id, MarketCandle.timeframe == Timeframe.D1
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(bars)
        )
    )
    rows.reverse()
    closes = [float(row.close) for row in rows]
    return [
        (closes[index] - closes[index - 1]) / closes[index - 1]
        for index in range(1, len(closes))
        if closes[index - 1]
    ]


def correlation(left: list[float], right: list[float]) -> float | None:
    """Pearson correlation, or None when there is not enough overlap to say anything."""
    size = min(len(left), len(right))
    if size < 10:
        return None
    a = left[-size:]
    b = right[-size:]
    mean_a = sum(a) / size
    mean_b = sum(b) / size
    cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(size))
    var_a = sum((value - mean_a) ** 2 for value in a)
    var_b = sum((value - mean_b) ** 2 for value in b)
    if var_a <= 0 or var_b <= 0:
        return None
    return cov / (var_a * var_b) ** 0.5


def concentration(
    db: Session, account: TradingAccount, symbol: str, extra_notional: float = 0.0
) -> float:
    """Post-trade share of equity concentrated in one instrument."""
    total_equity = equity(db, account)
    current = 0.0
    position = position_for(db, account, symbol)
    if position is not None:
        price = position.last_mark_price or latest_price(db, symbol)
        current = (price or position.average_entry_price) * position.quantity
    if total_equity <= 0:
        return 1.0
    return (current + extra_notional) / total_equity


def buy_volume_last_hour(
    db: Session, account: TradingAccount, *, now: datetime | None = None
) -> float:
    moment = now or datetime.now(UTC)
    window = moment - timedelta(hours=1)
    rows = db.scalars(
        select(Order).where(
            Order.trading_account_id == account.id,
            Order.side == OrderSide.BUY,
            Order.created_at >= window,
        )
    )
    return float(sum(order.filled_quantity for order in rows))
