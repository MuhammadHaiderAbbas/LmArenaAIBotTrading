"""Performance analytics over the executed book.

The rule this module is built around: **a metric is only reported if it can be
computed from recorded trades, and it is refused with a reason when the sample is too
small to mean anything.** A Sharpe ratio from three observations is not a small
Sharpe; it is noise with a decimal point. Every metric carries `value=None` and a note
explaining what is missing rather than a number that looks authoritative and is not.

Three accounting decisions worth stating, because each is a place analytics usually
lies:

- **The book is reconstructed from fills, not from the position table.** A closed
  position row has its quantity zeroed on close, so valuing history from today's open
  positions marks every past day as flat — which makes a profitable account show a
  double-digit "drawdown" that never happened. Cumulative fills give the quantity held
  *at each moment*, and that is what the curve is built from.
- **Round trips are FIFO over fills.** Long-only, so FIFO and average cost agree on
  total P&L; FIFO is chosen because it also tells you which entry each exit closed,
  which is what a trade list is for.
- **Trade P&L is net of commission; the position table is gross.** The broker posts
  realised P&L as proceeds − released basis and books commission separately to
  `FEE_EXPENSE`, so `positions.realised_pnl` excludes it. A round trip here reports
  *net*, because "net return after costs" is the metric the blueprint asks for — and a
  trade list whose winners are gross while the headline is net invites a reader to
  double-count. The two therefore differ by exactly the commissions, which a test
  asserts as an identity rather than an approximation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import Order, OrderFill, OrderSide, OrderStatus, Position, PositionStatus
from app.models.ledger import LedgerAccount, LedgerAccountKind, LedgerEntry, LedgerTransaction
from app.models.market import Instrument, MarketCandle, Timeframe
from app.services import ledger_service
from app.services.ai.agents import aware

#: Below this many closed round trips, trade statistics are refused rather than shown.
MIN_TRADES_FOR_STATS = 5
#: Below this many return observations, volatility-based ratios are refused.
MIN_OBSERVATIONS_FOR_RATIOS = 10
TRADING_DAYS = 252


@dataclass
class Metric:
    """A metric and its provenance, so no number can be shown without its caveat."""

    key: str
    label: str
    value: float | None
    unit: str = ""
    definition: str = ""
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "value": None if self.value is None else round(self.value, 8),
            "unit": self.unit,
            "definition": self.definition,
            "note": self.note,
        }


@dataclass
class Fill:
    at: datetime
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    notional: float
    commission: float
    slippage: float


@dataclass
class ClosedTrade:
    symbol: str
    quantity: float
    entry_price: float
    exit_price: float
    realised_pnl: float
    commission: float
    opened_at: datetime
    closed_at: datetime
    strategy_code: str | None = None

    @property
    def holding_days(self) -> float:
        return max(0.0, (self.closed_at - self.opened_at).total_seconds() / 86_400)

    @property
    def return_pct(self) -> float:
        basis = self.entry_price * self.quantity
        return self.realised_pnl / basis if basis else 0.0


@dataclass
class AnalyticsReport:
    as_of: datetime
    equity: float
    cash: float
    starting_equity: float
    net_return: float | None
    metrics: list[Metric] = field(default_factory=list)
    trades: list[ClosedTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    benchmark_curve: list[dict] = field(default_factory=list)
    attribution: dict = field(default_factory=dict)
    cost_breakdown: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- fills


def fill_timeline(db: Session, account: TradingAccount) -> list[Fill]:
    """Every fill for the account, oldest first."""
    rows = db.execute(
        select(OrderFill, Order)
        .join(Order, Order.id == OrderFill.order_id)
        .where(Order.trading_account_id == account.id)
        .order_by(OrderFill.filled_at, OrderFill.sequence)
    ).all()

    fills: list[Fill] = []
    for order_fill, order in rows:
        fills.append(
            Fill(
                at=order_fill.filled_at,
                symbol=order.instrument_symbol,
                side=order.side,
                quantity=float(order_fill.quantity),
                price=float(order_fill.price),
                notional=float(order_fill.notional),
                commission=float(order_fill.commission),
                slippage=float(order_fill.slippage),
            )
        )
    return fills


def round_trips(db: Session, account: TradingAccount) -> list[ClosedTrade]:
    """FIFO round trips reconstructed from fills.

    A sell closes the oldest open lot for that symbol. Any remainder is left open,
    which is the correct interpretation of a partial exit and the reason the sum of
    closed-trade P&L can be less than total realised P&L until everything is flat.
    """
    fills = fill_timeline(db, account)
    strategy_by_symbol = _strategy_codes(db, account)

    lots: dict[str, list[dict]] = {}
    trades: list[ClosedTrade] = []

    for fill in fills:
        book = lots.setdefault(fill.symbol, [])
        if fill.side == OrderSide.BUY:
            book.append(
                {
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "opened_at": fill.at,
                    "commission": fill.commission,
                }
            )
            continue

        remaining = fill.quantity
        exit_commission_per_unit = fill.commission / fill.quantity if fill.quantity else 0.0
        while remaining > 1e-12 and book:
            lot = book[0]
            matched = min(lot["quantity"], remaining)
            entry_notional = lot["price"] * matched
            entry_commission = lot["commission"] * (matched / lot["quantity"]) if lot["quantity"] else 0.0
            exit_notional = fill.price * matched
            pnl = exit_notional - entry_notional - entry_commission - exit_commission_per_unit * matched

            trades.append(
                ClosedTrade(
                    symbol=fill.symbol,
                    quantity=matched,
                    entry_price=lot["price"],
                    exit_price=fill.price,
                    realised_pnl=pnl,
                    commission=entry_commission + exit_commission_per_unit * matched,
                    opened_at=lot["opened_at"],
                    closed_at=fill.at,
                    strategy_code=strategy_by_symbol.get(fill.symbol),
                )
            )

            lot["quantity"] -= matched
            lot["commission"] -= entry_commission
            remaining -= matched
            if lot["quantity"] <= 1e-12:
                book.pop(0)

    trades.sort(key=lambda trade: trade.closed_at)
    return trades


def _strategy_codes(db: Session, account: TradingAccount) -> dict[str, str | None]:
    """Which strategy originated the most recent position per symbol, if any."""
    rows = db.scalars(
        select(Position)
        .where(Position.trading_account_id == account.id)
        .order_by(Position.opened_at)
    ).all()
    codes: dict[str, str | None] = {}
    for position in rows:
        if position.strategy_code:
            codes[position.instrument_symbol] = position.strategy_code
    return codes


def quantity_held_at(fills: list[Fill], symbol: str, at: datetime) -> float:
    """Net quantity of `symbol` held at `at`, from cumulative fills."""
    held = 0.0
    for fill in fills:
        if fill.symbol != symbol or aware(fill.at) > at:
            continue
        held += fill.quantity if fill.side == OrderSide.BUY else -fill.quantity
    return max(0.0, held)


# ------------------------------------------------------------------- prices


def price_at(db: Session, symbol: str, at: datetime) -> float | None:
    """Latest daily close at or before `at`. None when there is no bar that early."""
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    candle = db.scalar(
        select(MarketCandle)
        .where(
            MarketCandle.instrument_id == instrument.id,
            MarketCandle.timeframe == Timeframe.D1,
            MarketCandle.open_time <= at,
        )
        .order_by(MarketCandle.open_time.desc())
        .limit(1)
    )
    return float(candle.close) if candle else None


def latest_price(db: Session, symbol: str) -> float | None:
    return price_at(db, symbol, datetime.now(UTC) + timedelta(days=1))


# -------------------------------------------------------------- ledger cash


def cash_rows(db: Session, account: TradingAccount) -> list[tuple[datetime, float]]:
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
    return [(created_at, float(amount)) for created_at, amount in rows]


def cash_at(rows: list[tuple[datetime, float]], at: datetime) -> float:
    # SQLite hands back naive datetimes; compare in one zone or the comparison raises.
    return sum(amount for created_at, amount in rows if aware(created_at) <= at)


def starting_equity(db: Session, account: TradingAccount) -> float:
    """Total deposited, from the opening-equity account.

    Reading cash at time zero would give 0 before funding lands, and a return computed
    off a zero base is either infinite or silently wrong.
    """
    rows = db.scalars(
        select(LedgerEntry.amount)
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerAccount.trading_account_id == account.id,
            LedgerAccount.kind == LedgerAccountKind.OPENING_EQUITY,
        )
    ).all()
    return abs(float(sum(rows))) if rows else 0.0


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
    total = 0.0
    for position in open_positions(db, account):
        price = position.last_mark_price or latest_price(db, position.instrument_symbol)
        total += (price if price is not None else position.average_entry_price) * position.quantity
    return total


def current_equity(db: Session, account: TradingAccount) -> float:
    return float(ledger_service.ledger_cash(db, account)) + positions_value(db, account)


# ------------------------------------------------------------------- curves


def equity_curve(db: Session, account: TradingAccount, *, max_points: int = 400) -> list[dict]:
    """Marked equity per day, rebuilt from fills so past holdings are valued as held.

    Sampled daily from the first fill (or funding, if earlier) to today. Each point is
    `cash at that day + Σ held quantity × that day's close`, which is the only way a
    drawdown figure can be believed.
    """
    rows = cash_rows(db, account)
    if not rows:
        return []

    fills = fill_timeline(db, account)
    symbols = list(dict.fromkeys(fill.symbol for fill in fills))

    start = aware(rows[0][0]).date()
    end = datetime.now(UTC).date()
    if (end - start).days > max_points:
        start = end - timedelta(days=max_points)

    curve: list[dict] = []
    day = start
    while day <= end:
        moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23, minutes=59)
        cash = cash_at(rows, moment)
        held_value = 0.0
        for symbol in symbols:
            quantity = quantity_held_at(fills, symbol, moment)
            if quantity <= 0:
                continue
            price = price_at(db, symbol, moment)
            if price is not None:
                held_value += quantity * price
        curve.append({"at": moment.isoformat(), "equity": round(cash + held_value, 4)})
        day += timedelta(days=1)

    return curve


def benchmark_curve(
    db: Session, account: TradingAccount, symbols: list[str], *, max_points: int = 400
) -> list[dict]:
    """Equal-weight buy-and-hold of the instruments actually traded.

    An index would answer a different question. The honest comparison is "did
    selecting these trades beat simply holding what was traded, over the same window?"
    """
    rows = cash_rows(db, account)
    if not rows:
        return []
    base = starting_equity(db, account)
    if base <= 0:
        return []

    usable: dict[str, list[tuple[datetime, float]]] = {}
    for symbol in dict.fromkeys(symbols):
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
        if instrument is None:
            continue
        candles = db.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.instrument_id == instrument.id,
                MarketCandle.timeframe == Timeframe.D1,
            )
            .order_by(MarketCandle.open_time)
        ).all()
        if candles:
            usable[symbol] = [(candle.open_time, float(candle.close)) for candle in candles]

    if not usable:
        return []

    start = aware(rows[0][0]).date()
    end = datetime.now(UTC).date()
    if (end - start).days > max_points:
        start = end - timedelta(days=max_points)

    first_prices = {symbol: points[0][1] for symbol, points in usable.items()}

    curve: list[dict] = []
    day = start
    while day <= end:
        moment = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=23, minutes=59)
        growth = 0.0
        counted = 0
        for symbol, points in usable.items():
            price = None
            for at, close in points:
                if aware(at) <= moment:
                    price = close
                else:
                    break
            if price is None:
                continue
            base_price = first_prices[symbol]
            if base_price:
                growth += price / base_price
                counted += 1
        if counted:
            curve.append({"at": moment.isoformat(), "equity": round(base * (growth / counted), 4)})
        day += timedelta(days=1)

    return curve


# --------------------------------------------------------------- statistics


def daily_returns(curve: list[dict]) -> list[float]:
    values = [point["equity"] for point in curve]
    returns: list[float] = []
    for index in range(1, len(values)):
        previous = values[index - 1]
        if previous > 0:
            returns.append((values[index] - previous) / previous)
    return returns


def sharpe(returns: list[float], risk_free: float = 0.0) -> float | None:
    if len(returns) < MIN_OBSERVATIONS_FOR_RATIOS:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    deviation = math.sqrt(variance)
    if deviation == 0:
        return None
    return ((mean - risk_free / TRADING_DAYS) / deviation) * math.sqrt(TRADING_DAYS)


def sortino(returns: list[float], risk_free: float = 0.0) -> float | None:
    if len(returns) < MIN_OBSERVATIONS_FOR_RATIOS:
        return None
    mean = sum(returns) / len(returns)
    downside = [value for value in returns if value < 0]
    if not downside:
        return None
    downside_deviation = math.sqrt(sum(value**2 for value in downside) / len(returns))
    if downside_deviation == 0:
        return None
    return ((mean - risk_free / TRADING_DAYS) / downside_deviation) * math.sqrt(TRADING_DAYS)


def max_drawdown(curve: list[float]) -> float | None:
    if not curve:
        return None
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst


def profit_factor(trades: list[ClosedTrade]) -> float | None:
    """Gross profit ÷ gross loss. None when nothing was lost.

    Reporting "infinity" for a strategy that has never lost is flattery; refusing the
    number and saying why is the honest alternative.
    """
    gross_profit = sum(trade.realised_pnl for trade in trades if trade.realised_pnl > 0)
    gross_loss = -sum(trade.realised_pnl for trade in trades if trade.realised_pnl < 0)
    if gross_loss == 0:
        return None
    return gross_profit / gross_loss


def win_rate(trades: list[ClosedTrade]) -> float | None:
    if not trades:
        return None
    return sum(1 for trade in trades if trade.realised_pnl > 0) / len(trades)


# ------------------------------------------------------------------- report


def build_report(db: Session, account: TradingAccount, *, now: datetime | None = None) -> AnalyticsReport:
    moment = now or datetime.now(UTC)
    cash = float(ledger_service.ledger_cash(db, account))
    book_value = positions_value(db, account)
    equity = cash + book_value
    base = starting_equity(db, account)

    fills = fill_timeline(db, account)
    trades = round_trips(db, account)
    curve = equity_curve(db, account)
    returns = daily_returns(curve)
    net_return = (equity - base) / base if base > 0 else None

    symbols = list(dict.fromkeys([fill.symbol for fill in fills]))
    benchmark = benchmark_curve(db, account, symbols)

    notes: list[str] = []
    if not fills:
        notes.append("No fills yet: there is nothing to measure, and an empty dashboard is the honest report.")
    if fills and not trades:
        notes.append("Positions are open but none is closed, so trade statistics stay blank until a round trip completes.")
    if trades and len(trades) < MIN_TRADES_FOR_STATS:
        notes.append(
            f"Only {len(trades)} closed round trip(s); at least {MIN_TRADES_FOR_STATS} are required "
            "before win rate or profit factor is reported."
        )
    if len(returns) < MIN_OBSERVATIONS_FOR_RATIOS:
        notes.append(
            f"Only {len(returns)} daily observation(s); at least {MIN_OBSERVATIONS_FOR_RATIOS} are "
            "needed before Sharpe or Sortino mean anything. This account has not existed long enough."
        )
    if benchmark:
        notes.append(
            "The benchmark is equal-weight buy-and-hold of the instruments actually traded, over the "
            "same window — not an index. Beating it is the only claim worth making."
        )

    traded = sum(fill.notional for fill in fills)
    commissions = sum(fill.commission for fill in fills)
    slippage = sum(fill.slippage for fill in fills)
    curve_values = [point["equity"] for point in curve]
    average_equity = sum(curve_values) / len(curve_values) if curve_values else equity

    orders = list(db.scalars(select(Order).where(Order.trading_account_id == account.id)))

    by_symbol: dict[str, float] = {}
    by_strategy: dict[str, float] = {}
    for trade in trades:
        by_symbol[trade.symbol] = by_symbol.get(trade.symbol, 0.0) + trade.realised_pnl
        key = trade.strategy_code or "manual"
        by_strategy[key] = by_strategy.get(key, 0.0) + trade.realised_pnl

    metrics: list[Metric] = [
        Metric(
            "net_return",
            "Net return",
            net_return,
            "pct",
            "(equity − deposited) ÷ deposited, after every cost",
        ),
        Metric(
            "sharpe",
            "Sharpe ratio",
            sharpe(returns),
            "ratio",
            "Annualised mean daily return ÷ volatility of daily returns",
            "" if len(returns) >= MIN_OBSERVATIONS_FOR_RATIOS else f"needs ≥{MIN_OBSERVATIONS_FOR_RATIOS} daily observations",
        ),
        Metric(
            "sortino",
            "Sortino ratio",
            sortino(returns),
            "ratio",
            "Annualised mean daily return ÷ downside deviation",
            "" if len(returns) >= MIN_OBSERVATIONS_FOR_RATIOS else f"needs ≥{MIN_OBSERVATIONS_FOR_RATIOS} daily observations",
        ),
        Metric(
            "max_drawdown",
            "Max drawdown",
            max_drawdown(curve_values),
            "pct",
            "Worst peak-to-trough decline of marked equity",
        ),
        Metric(
            "win_rate",
            "Win rate",
            win_rate(trades) if len(trades) >= MIN_TRADES_FOR_STATS else None,
            "pct",
            "Closed round trips with positive P&L ÷ all closed round trips",
            "" if len(trades) >= MIN_TRADES_FOR_STATS else f"needs ≥{MIN_TRADES_FOR_STATS} closed trips",
        ),
        Metric(
            "profit_factor",
            "Profit factor",
            profit_factor(trades) if len(trades) >= MIN_TRADES_FOR_STATS else None,
            "ratio",
            "Gross profit ÷ gross loss",
            "undefined while nothing has been lost" if trades and profit_factor(trades) is None else (
                "" if len(trades) >= MIN_TRADES_FOR_STATS else f"needs ≥{MIN_TRADES_FOR_STATS} closed trips"
            ),
        ),
        Metric(
            "turnover",
            "Turnover",
            traded / average_equity if average_equity > 0 and traded > 0 else None,
            "ratio",
            "Total traded notional ÷ average equity over the curve",
        ),
        Metric(
            "exposure",
            "Current exposure",
            book_value / equity if equity > 0 else None,
            "pct",
            "Invested value ÷ equity right now — historical snapshots are not stored, so this is not an average",
        ),
    ]

    return AnalyticsReport(
        as_of=moment,
        equity=round(equity, 4),
        cash=round(cash, 4),
        starting_equity=round(base, 4),
        net_return=net_return,
        metrics=metrics,
        trades=trades,
        equity_curve=curve,
        benchmark_curve=benchmark,
        attribution={
            "by_symbol": {key: round(value, 4) for key, value in sorted(by_symbol.items())},
            "by_strategy": {key: round(value, 4) for key, value in sorted(by_strategy.items())},
        },
        cost_breakdown={
            "commission": round(commissions, 4),
            "slippage": round(slippage, 4),
            "total": round(commissions + slippage, 4),
            "orders": len(orders),
            "fills": len(fills),
            "rejected_orders": sum(1 for order in orders if order.status == OrderStatus.REJECTED),
        },
        notes=notes,
    )


def realised_total(db: Session, account: TradingAccount) -> float:
    """Realised P&L from the position table (gross of commission), for cross-checking.

    `sum(round_trips().realised_pnl) + sum(round_trips().commission)` must equal this.
    """
    return float(
        db.scalar(
            select(func.coalesce(func.sum(Position.realised_pnl), 0)).where(
                Position.trading_account_id == account.id
            )
        )
        or 0
    )
