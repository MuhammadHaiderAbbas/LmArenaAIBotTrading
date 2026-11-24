"""Paper broker: simulated fills, positions and ledger postings.

Three behaviours this implements because real markets have them and paper engines
usually do not:

- **Partial fills.** An order larger than a fraction of the bar's typical size fills
  in bites, each at a slightly worse price. A single averaged fill hides the fact
  that the last third of the position cost more than the first.
- **Slippage, measured.** Every fill stores the mid it was priced against and the
  price it got, so slippage is a stored number rather than a claim in a document.
- **Limit orders that do not fill.** A limit away from the market sits there. It
  does not get the market price because we happened to run the simulator.

Money never moves outside the ledger. A fill posts a balanced transaction:

    Buy   Debit INVESTMENT (notional), Credit CASH (notional), Debit FEE, Credit CASH (fee)
    Sell  Debit CASH (notional), Credit INVESTMENT (notional), Debit FEE, Credit CASH (fee)
          and for a profitable sale, Debit INVESTMENT is split so the gain lands in
          REALISED_PNL rather than inflating the investment balance.

The position row is a view over those postings, reconciled in `reconcile_positions`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import (
    ExecutionEventType,
    Order,
    OrderFill,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
)
from app.models.ledger import LedgerAccountKind, TransactionType
from app.models.market import Instrument, MarketCandle, Timeframe
from app.services import ledger_service
from app.services.errors import DomainError
from app.services.execution.orders import (
    DEFAULT_COMMISSION_BPS,
    DEFAULT_SLIPPAGE_BPS,
    MAX_FILL_BITES,
    PARTICIPATION_CAP,
    record_event,
)

logger = logging.getLogger(__name__)


@dataclass
class FillConfig:
    commission_bps: float = DEFAULT_COMMISSION_BPS
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    participation_cap: float = PARTICIPATION_CAP
    max_bites: int = MAX_FILL_BITES


def _current_price(db: Session, symbol: str, timeframe: Timeframe = Timeframe.D1) -> float | None:
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


def _typical_size(db: Session, symbol: str, timeframe: Timeframe = Timeframe.D1) -> float:
    """Median bar volume, used to decide whether an order fills in bites.

    Median rather than mean: one spike in volume would otherwise make every later
    order look tiny.
    """
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        return 0.0
    rows = list(
        db.scalars(
            select(MarketCandle.volume)
            .where(
                MarketCandle.instrument_id == instrument.id,
                MarketCandle.timeframe == timeframe,
                MarketCandle.volume.is_not(None),
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(50)
        )
    )
    values = sorted(float(row) for row in rows if row)
    if not values:
        return 0.0
    return values[len(values) // 2]


def execute_order(
    db: Session,
    *,
    account: TradingAccount,
    order: Order,
    config: FillConfig | None = None,
    now: datetime | None = None,
    actor_id: str | None = None,
) -> Order:
    """Fill a submitted order against the paper venue, in whole or in part."""
    config = config or FillConfig()
    moment = now or datetime.now(UTC)

    if order.status not in (OrderStatus.SUBMITTED, OrderStatus.VALIDATED):
        raise DomainError(
            f"Order {order.id} is {order.status.value}; only a submitted order can be filled."
        )
    if order.status == OrderStatus.VALIDATED:
        order.status = OrderStatus.SUBMITTED
        order.submitted_at = moment

    mid = _current_price(db, order.instrument_symbol)
    if mid is None:
        order.status = OrderStatus.REJECTED
        order.reject_reason = "No price available at execution time."
        order.completed_at = moment
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.REJECTED,
            order=order,
            message=order.reject_reason,
            at=moment,
        )
        db.flush()
        return order

    # A limit order only fills if the market is at or through the limit.
    if order.order_type == OrderType.LIMIT and order.limit_price is not None:
        marketable = (
            mid <= order.limit_price if order.side == OrderSide.BUY else mid >= order.limit_price
        )
        if not marketable:
            record_event(
                db,
                account_id=account.id,
                event_type=ExecutionEventType.ACKNOWLEDGED,
                order=order,
                message=(
                    f"Limit {order.limit_price:.5f} is not marketable against {mid:.5f}; "
                    "the order rests unfilled."
                ),
                at=moment,
            )
            db.flush()
            return order

    remaining = order.remaining_quantity
    if remaining <= 0:
        return order

    typical = _typical_size(db, order.instrument_symbol)
    bite_cap = max(1e-9, typical * config.participation_cap) if typical else remaining
    requested_bites = math.ceil(remaining / bite_cap) if bite_cap < remaining else 1
    bites = max(1, min(config.max_bites, requested_bites))

    # A larger order erodes more; each successive bite pays a little more slippage
    # than the previous one.
    base_slippage = config.slippage_bps / 10_000
    for sequence in range(bites):
        quantity = remaining if bites == 1 else min(bite_cap, remaining)
        if quantity <= 1e-12:
            break
        degradation = 1 + sequence * 0.5
        slip = base_slippage * degradation
        price = mid * (1 + slip) if order.side == OrderSide.BUY else mid * (1 - slip)
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            # A limit never fills worse than its limit; if the modelled price would
            # breach it, the bite does not happen.
            if order.side == OrderSide.BUY and price > order.limit_price:
                break
            if order.side == OrderSide.SELL and price < order.limit_price:
                break
        notional = price * quantity
        commission = notional * config.commission_bps / 10_000
        slippage_cost = abs(price - mid) * quantity

        fill = OrderFill(
            order_id=order.id,
            sequence=sequence,
            quantity=quantity,
            price=price,
            reference_price=mid,
            slippage=slippage_cost,
            commission=commission,
            notional=notional,
            filled_at=moment,
            venue="paper",
        )
        db.add(fill)
        db.flush()

        transaction = ledger_service.post_transaction(
            db,
            account=account,
            transaction_type=(
                TransactionType.TRADE_BUY
                if order.side == OrderSide.BUY
                else TransactionType.TRADE_SELL
            ),
            lines=(
                [
                    (LedgerAccountKind.INVESTMENT, notional),
                    (LedgerAccountKind.CASH, -notional),
                ]
                if order.side == OrderSide.BUY
                else _sell_lines(order, notional, db, account)
            ),
            description=f"{order.side.value} {quantity:.6f} {order.instrument_symbol} @ {price:.5f}",
            reference=order.id,
            idempotency_key=f"fill:{fill.id}",
            actor_id=actor_id,
            occurred_at=moment,
        )
        fill.ledger_transaction_id = transaction.id

        if commission > 0:
            ledger_service.post_transaction(
                db,
                account=account,
                transaction_type=TransactionType.FEE,
                lines=[
                    (LedgerAccountKind.FEE_EXPENSE, commission),
                    (LedgerAccountKind.CASH, -commission),
                ],
                description=f"Commission on {order.instrument_symbol} fill",
                reference=order.id,
                idempotency_key=f"fee:{fill.id}",
                occurred_at=moment,
            )

        order.filled_quantity = round(order.filled_quantity + quantity, 10)
        order.total_commission = round(order.total_commission + commission, 10)
        order.total_slippage = round(order.total_slippage + slippage_cost, 10)
        remaining = round(remaining - quantity, 10)

        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.PARTIAL_FILL,
            order=order,
            message=f"Filled {quantity:.6f} of {order.instrument_symbol} at {price:.5f}",
            detail={
                "sequence": sequence,
                "quantity": quantity,
                "price": price,
                "reference_price": mid,
                "slippage": slippage_cost,
                "commission": commission,
            },
            actor_id=actor_id,
            at=moment,
        )

    # Average fill price is quantity-weighted across all bites, not the mean of the
    # bite prices — those differ when the final bite is a partial one.
    fills = list(order.fills)
    if fills:
        total_quantity = sum(fill.quantity for fill in fills)
        order.average_fill_price = (
            sum(fill.price * fill.quantity for fill in fills) / total_quantity
            if total_quantity
            else None
        )

    if order.filled_quantity >= order.quantity - 1e-9:
        order.status = OrderStatus.FILLED
        order.completed_at = moment
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.FILLED,
            order=order,
            message=f"Order complete: {order.quantity:.6f} {order.instrument_symbol}",
            at=moment,
        )
    else:
        order.status = OrderStatus.PARTIALLY_FILLED
        record_event(
            db,
            account_id=account.id,
            event_type=ExecutionEventType.PARTIAL_FILL,
            order=order,
            message=(
                f"Order partially filled: {order.filled_quantity:.6f} of {order.quantity:.6f}. "
                "Remainder resting."
            ),
            at=moment,
        )

    db.flush()
    apply_to_position(db, order=order, account=account, moment=moment, actor_id=actor_id)
    ledger_service.refresh_balance_cache(db, account)
    db.flush()
    return order


def _sell_lines(order: Order, notional: float, db: Session, account: TradingAccount) -> list[tuple]:
    """A sale returns capital and books any gain or loss against the cost basis.

    Without this split, selling at a profit would leave the investment balance
    overstated by the gain and the P&L account untouched.
    """
    position = db.scalar(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.instrument_symbol == order.instrument_symbol,
            Position.status == PositionStatus.OPEN,
        )
    )
    quantity = order.fills[-1].quantity if order.fills else order.remaining_quantity
    if position is None or position.quantity <= 0:
        return [
            (LedgerAccountKind.CASH, notional),
            (LedgerAccountKind.INVESTMENT, -notional),
        ]

    released_basis = min(position.cost_basis, position.average_entry_price * quantity)
    realised = notional - released_basis
    lines: list[tuple] = [
        (LedgerAccountKind.CASH, notional),
        (LedgerAccountKind.INVESTMENT, -released_basis),
    ]
    if realised != 0:
        # Profit credits REALISED_PNL; a loss debits it. Sign of the entry amount
        # already encodes which, because the ledger sums to zero either way.
        lines.append((LedgerAccountKind.REALISED_PNL, -realised))
    return lines


def apply_to_position(
    db: Session,
    *,
    order: Order,
    account: TradingAccount,
    moment: datetime,
    actor_id: str | None = None,
) -> Position:
    """Fold a filled order into the net position.

    The position is a *view*: quantity and cost basis are derived from fills, and
    `reconcile_positions` checks them against the ledger. It exists for the UI and
    for later phases (risk, analytics) to read cheaply.
    """
    position = db.scalar(
        select(Position).where(
            Position.trading_account_id == account.id,
            Position.instrument_symbol == order.instrument_symbol,
            Position.status == PositionStatus.OPEN,
        )
    )

    fills = list(order.fills)
    if not fills:
        raise DomainError("Cannot apply an order with no fills to a position.")

    if order.side == OrderSide.BUY:
        if position is None:
            quantity = sum(fill.quantity for fill in fills)
            cost = sum(fill.notional for fill in fills)
            commission = sum(fill.commission for fill in fills)
            position = Position(
                trading_account_id=account.id,
                instrument_symbol=order.instrument_symbol,
                status=PositionStatus.OPEN,
                quantity=quantity,
                cost_basis=cost,
                average_entry_price=cost / quantity if quantity else 0.0,
                total_commission=commission,
                opened_at=moment,
                strategy_code=order.origin if order.origin != "manual" else None,
                opening_rationale=order.rationale,
            )
            db.add(position)
            record_event(
                db,
                account_id=account.id,
                event_type=ExecutionEventType.POSITION_OPENED,
                order=order,
                message=f"Opened {quantity:.6f} {order.instrument_symbol}",
                detail={"average_entry_price": position.average_entry_price},
                actor_id=actor_id,
                at=moment,
            )
        else:
            added_quantity = sum(fill.quantity for fill in fills)
            added_cost = sum(fill.notional for fill in fills)
            position.quantity = round(position.quantity + added_quantity, 10)
            position.cost_basis = round(position.cost_basis + added_cost, 10)
            position.average_entry_price = (
                position.cost_basis / position.quantity if position.quantity else 0.0
            )
            position.total_commission = round(
                position.total_commission + sum(fill.commission for fill in fills), 10
            )
        position.last_mark_price = fills[-1].price
        position.last_marked_at = moment
    else:
        if position is None:
            raise DomainError(f"No open position in {order.instrument_symbol} to reduce.")
        sold_quantity = sum(fill.quantity for fill in fills)
        proceeds = sum(fill.notional for fill in fills)
        released_basis = min(position.cost_basis, position.average_entry_price * sold_quantity)
        realised = proceeds - released_basis

        position.quantity = round(position.quantity - sold_quantity, 10)
        position.cost_basis = round(max(0.0, position.cost_basis - released_basis), 10)
        position.realised_pnl = round(position.realised_pnl + realised, 10)
        position.total_commission = round(
            position.total_commission + sum(fill.commission for fill in fills), 10
        )
        position.last_mark_price = fills[-1].price
        position.last_marked_at = moment

        if position.quantity <= 1e-9:
            position.quantity = 0.0
            position.cost_basis = 0.0
            position.status = PositionStatus.CLOSED
            position.closed_at = moment
            record_event(
                db,
                account_id=account.id,
                event_type=ExecutionEventType.POSITION_CLOSED,
                order=order,
                message=(
                    f"Closed {order.instrument_symbol}; realised P&L {realised:+.2f} "
                    f"{account.base_currency}"
                ),
                detail={"realised_pnl": realised},
                actor_id=actor_id,
                at=moment,
            )

    db.flush()
    return position


def mark_positions(
    db: Session, *, account: TradingAccount, now: datetime | None = None
) -> list[Position]:
    """Mark open positions to the latest close.

    Unrealised P&L is derived here rather than stored, so a stale mark cannot
    masquerade as a fact.
    """
    moment = now or datetime.now(UTC)
    positions = list(
        db.scalars(
            select(Position).where(
                Position.trading_account_id == account.id,
                Position.status == PositionStatus.OPEN,
            )
        )
    )
    for position in positions:
        price = _current_price(db, position.instrument_symbol)
        if price is None:
            continue
        position.last_mark_price = price
        position.last_marked_at = moment
    db.flush()
    return positions


def reconcile_positions(db: Session, *, account: TradingAccount) -> dict:
    """Check the position view against the ledger it summarises.

    Two records of the same money will eventually disagree. This is how that gets
    noticed on a schedule rather than during an incident.
    """
    positions = list(
        db.scalars(
            select(Position).where(
                Position.trading_account_id == account.id,
                Position.status == PositionStatus.OPEN,
            )
        )
    )
    invested = sum(position.cost_basis for position in positions)
    ledger_investment = _investment_balance(db, account)

    # Realised P&L belongs to *closed* positions. Comparing only open ones would
    # report a mismatch the moment anything was sold, which is exactly the sort of
    # false alarm that teaches people to ignore reconciliation output.
    all_positions = list(
        db.scalars(select(Position).where(Position.trading_account_id == account.id))
    )
    realised = sum(position.realised_pnl for position in all_positions)
    ledger_realised = _realised_pnl_balance(db, account)

    invested_tolerance = max(0.01, abs(ledger_investment) * 1e-6)
    return {
        "positions_value": round(invested, 4),
        "ledger_investment": round(ledger_investment, 4),
        "investment_agrees": abs(invested - ledger_investment) <= invested_tolerance,
        "positions_realised": round(realised, 4),
        "ledger_realised": round(ledger_realised, 4),
        "realised_agrees": abs(realised - ledger_realised)
        <= max(0.01, abs(ledger_realised) * 1e-6),
        "open_positions": len(positions),
        "closed_positions": len(all_positions) - len(positions),
        "tolerance": invested_tolerance,
    }


def _investment_balance(db: Session, account: TradingAccount) -> float:
    from sqlalchemy import func

    from app.models.ledger import LedgerAccount, LedgerEntry

    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.trading_account_id == account.id,
            LedgerAccount.kind == LedgerAccountKind.INVESTMENT,
        )
    )
    return float(total or 0)


def _realised_pnl_balance(db: Session, account: TradingAccount) -> float:
    from sqlalchemy import func

    from app.models.ledger import LedgerAccount, LedgerEntry

    # REALISED_PNL is income: credits are negative entries, so the balance as seen
    # by the owner is the negation of the ledger sum.
    total = db.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.trading_account_id == account.id,
            LedgerAccount.kind == LedgerAccountKind.REALISED_PNL,
        )
    )
    return -float(total or 0)
