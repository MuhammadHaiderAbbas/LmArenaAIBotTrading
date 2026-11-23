"""Orders, fills, positions and the execution audit trail.

The blueprint's safety boundary is the organising principle here:

> AI agents must not possess unrestricted broker credentials or directly execute
> trades. They create structured proposals. A deterministic trading service
> validates the proposal [...] Only the execution service can submit an order.

So an `Order` is the *only* thing that can move money, and it can only be created
through the deterministic validation path in `app/services/execution/`. Three
structural rules follow:

- **Order state is explicit and one-way.** `new → validated → submitted →
  partially_filled → filled`, with `rejected`, `cancelled` and `expired` as terminal
  exits. Nothing sets `filled` without a corresponding `OrderFill` row, so the
  quantity arithmetic is always reconcilable.
- **Fills carry their own costs.** Commission, slippage and the price actually
  received are stored per fill, not aggregated. A partial fill that got a worse
  price than the first bite has to be visible.
- **The ledger is the source of truth for money.** `Position` is a convenience view
  for the UI; cash and realised P&L come from `ledger_entries` and are reconciled
  against it. Two records of the same money is how accounting systems rot.

`ExecutionEvent` is append-only: every state change writes one, so "who rejected
this order and why" is a query, not an investigation.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
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


class OrderSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class TimeInForce(str, enum.Enum):
    DAY = "day"
    GTC = "gtc"
    IOC = "ioc"


class OrderStatus(str, enum.Enum):
    NEW = "new"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


#: Terminal states never transition again.
TERMINAL_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED}
)


class PositionStatus(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"


class ExecutionEventType(str, enum.Enum):
    SUBMITTED = "submitted"
    VALIDATED = "validated"
    REJECTED = "rejected"
    ACKNOWLEDGED = "acknowledged"
    PARTIAL_FILL = "partial_fill"
    FILLED = "filled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    MARKED = "marked"


class Order(Base, TimestampMixin):
    """A single instruction to the paper broker.

    `client_order_id` is unique per account and is the idempotency key: submitting
    the same instruction twice returns the original order rather than doubling the
    position. That is the duplicate-message failure the blueprint's reliability
    criteria call out.
    """

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("trading_account_id", "client_order_id", name="uq_order_client_id"),
        Index("ix_orders_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instrument_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True
    )

    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    side: Mapped[OrderSide] = mapped_column(
        Enum(OrderSide, native_enum=False, length=8), nullable=False
    )
    order_type: Mapped[OrderType] = mapped_column(
        Enum(OrderType, native_enum=False, length=8),
        default=OrderType.MARKET,
        server_default="MARKET",
        nullable=False,
    )
    time_in_force: Mapped[TimeInForce] = mapped_column(
        Enum(TimeInForce, native_enum=False, length=8),
        default=TimeInForce.DAY,
        server_default="DAY",
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=20),
        default=OrderStatus.NEW,
        server_default="NEW",
        nullable=False,
    )

    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    filled_quantity: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    total_commission: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    total_slippage: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)

    #: Why the order was refused, in the words the reviewer will read.
    reject_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: The rationale chain: which strategy or proposal produced this order.
    origin: Mapped[str] = mapped_column(String(32), default="manual", server_default="manual")
    origin_ref: Mapped[str | None] = mapped_column(String(36), nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="", server_default="")

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Set when a DAY order expires or an IOC remainder is discarded.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    requested_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    fills: Mapped[list[OrderFill]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="OrderFill.sequence"
    )
    events: Mapped[list[ExecutionEvent]] = relationship(
        back_populates="order", cascade="all, delete-orphan", order_by="ExecutionEvent.occurred_at"
    )

    @property
    def remaining_quantity(self) -> float:
        return max(0.0, round(self.quantity - self.filled_quantity, 10))


class OrderFill(Base, TimestampMixin):
    """A simulated execution against the paper broker.

    Partial fills are first-class: a large order is broken into bites of declining
    liquidity, each at a slightly worse price, which is what actually happens and
    what a single averaged fill price hides.
    """

    __tablename__ = "order_fills"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    order_id: Mapped[str] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    #: The mid at the moment of the fill, so slippage is measurable rather than claimed.
    reference_price: Mapped[float] = mapped_column(Float, nullable=False)
    slippage: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    commission: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    notional: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Which market that fill would have come from. Paper only, but labelled.
    venue: Mapped[str] = mapped_column(String(40), default="paper", server_default="paper")
    ledger_transaction_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    order: Mapped[Order] = relationship(back_populates="fills")


class Position(Base, TimestampMixin):
    """A net holding per account and instrument.

    Long-only in Phase 8: leverage is disabled per the blueprint's initial paper
    settings, and a short is a borrow, which needs financing and locate rules this
    phase does not implement. Shorts are refused with that reason rather than
    silently modelled wrong.

    `average_entry_price` is the cost basis used to split proceeds into return of
    capital and realised P&L. It is reconciled against the ledger, not trusted.
    """

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint(
            "trading_account_id", "instrument_symbol", "status", name="uq_position_account_symbol"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    instrument_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[PositionStatus] = mapped_column(
        Enum(PositionStatus, native_enum=False, length=8),
        default=PositionStatus.OPEN,
        server_default="OPEN",
        nullable=False,
    )

    quantity: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    average_entry_price: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    cost_basis: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)

    realised_pnl: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    total_commission: Mapped[float] = mapped_column(Float, default=0.0, server_default="0", nullable=False)
    last_mark_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Which strategy opened it, so performance can be attributed later.
    strategy_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Rationale carried through from the proposal, never invented at fill time.
    opening_rationale: Mapped[str] = mapped_column(Text, default="", server_default="")


class ExecutionEvent(Base, TimestampMixin):
    """Append-only lifecycle record. Never updated, never deleted."""

    __tablename__ = "execution_events"
    __table_args__ = (Index("ix_exec_events_order_time", "order_id", "occurred_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=True
    )
    trading_account_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    event_type: Mapped[ExecutionEventType] = mapped_column(
        Enum(ExecutionEventType, native_enum=False, length=24), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, default="", server_default="")
    detail: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    order: Mapped[Order | None] = relationship(back_populates="events")
