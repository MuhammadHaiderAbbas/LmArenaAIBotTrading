"""Risk policy, per-order checks, risk events and the circuit breaker.

Four tables, four jobs, and one deliberate omission: there is **no** table holding a
"current risk score" that anything can overwrite. A risk decision is computed from
observable state at the moment an order is proposed and stored as an immutable
`RiskCheck` row. That is what makes the engine independently testable — you can
replay any refusal from the rows alone.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class RiskDecision(str, enum.Enum):
    """The outcome of a risk evaluation.

    `ALLOW` and `ALLOW_REDUCED` are the only values that permit an order, and
    `ALLOW_REDUCED` always comes with a smaller quantity than requested.
    """

    ALLOW = "allow"
    ALLOW_REDUCED = "allow_reduced"
    REJECT = "reject"


class RiskRule(str, enum.Enum):
    """Every rule the controller can apply, named so a refusal is legible.

    A rule that is not implemented here cannot be cited in a refusal. The engine
    refuses to run a policy whose rules it does not know, rather than ignoring them.
    """

    PER_TRADE_RISK = "per_trade_risk"
    DAILY_LOSS = "daily_loss"
    MAX_DRAWDOWN = "max_drawdown"
    MAX_POSITIONS = "max_positions"
    MAX_EXPOSURE = "max_exposure"
    LEVERAGE = "leverage"
    DATA_FRESHNESS = "data_freshness"
    SPREAD = "spread"
    CONCENTRATION = "concentration"
    CORRELATION = "correlation"
    KILL_SWITCH = "kill_switch"
    ORDER_RATE = "order_rate"


class BreakerState(str, enum.Enum):
    """Circuit-breaker states.

    `OPEN` stops new risk. It does **not** liquidate positions: closing exposure is a
    separate policy decision, because forced liquidation in an illiquid or disrupted
    market can create the loss it is meant to prevent. That distinction is the
    blueprint's, and it is why `halted_new_risk` and `positions_affected` are separate
    fields on the event.
    """

    CLOSED = "closed"
    OPEN = "open"
    MANUAL = "manual"


class RiskPolicy(Base, TimestampMixin):
    """A named set of limits. Versioned by `version` and scoped per account.

    Limits are stored as JSON so the set can grow without a migration per rule, but
    the engine validates the keys against `RiskRule` on load: an unknown or misspelled
    limit is an error, not something silently ignored. A risk policy that quietly
    skips a limit it does not recognise is worse than no policy.
    """

    __tablename__ = "risk_policies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="default")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Percentages are stored as fractions of equity (0.005 = 0.5%).
    max_risk_per_trade: Mapped[float] = mapped_column(Float, nullable=False, default=0.005)
    max_daily_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.02)
    max_drawdown: Mapped[float] = mapped_column(Float, nullable=False, default=0.10)
    max_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    max_exposure: Mapped[float] = mapped_column(Float, nullable=False, default=0.50)
    # 0.30, not 0.20, because the limits must be arithmetically coherent: a
    # full-risk trade (0.5% of equity risked against a 2% stop) is 25% of equity in
    # notional. A 20% concentration cap would refuse every correctly sized trade while
    # looking prudent — the worst kind of limit, one that is never actually binding on
    # the behaviour it claims to control.
    max_position_concentration: Mapped[float] = mapped_column(Float, nullable=False, default=0.30)
    max_correlation: Mapped[float] = mapped_column(Float, nullable=False, default=0.85)
    max_spread_bps: Mapped[float] = mapped_column(Float, nullable=False, default=25.0)
    max_orders_per_hour: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    leverage_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    @property
    def limits(self) -> dict[str, float]:
        return {
            RiskRule.PER_TRADE_RISK.value: self.max_risk_per_trade,
            RiskRule.DAILY_LOSS.value: self.max_daily_loss,
            RiskRule.MAX_DRAWDOWN.value: self.max_drawdown,
            RiskRule.MAX_POSITIONS.value: self.max_positions,
            RiskRule.MAX_EXPOSURE.value: self.max_exposure,
            RiskRule.CONCENTRATION.value: self.max_position_concentration,
            RiskRule.CORRELATION.value: self.max_correlation,
            RiskRule.SPREAD.value: self.max_spread_bps,
            RiskRule.ORDER_RATE.value: self.max_orders_per_hour,
        }


class RiskCheck(Base, TimestampMixin):
    """The immutable record of one evaluation.

    `checks` holds every rule with its observed value, its limit and its verdict, so a
    reviewer can see not only *that* an order was refused but which limit it breached
    and by how much. `decision` is derived from those checks, never stored ahead of
    them.
    """

    __tablename__ = "risk_checks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[str | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_id: Mapped[str | None] = mapped_column(
        ForeignKey("risk_policies.id", ondelete="SET NULL"), nullable=True
    )
    instrument_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)

    requested_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    approved_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    decision: Mapped[RiskDecision] = mapped_column(
        Enum(RiskDecision, native_enum=False, length=16),
        nullable=False,
        default=RiskDecision.REJECT,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    breached_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    checks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Snapshot of the account state the decision was made against. Without this the
    # decision is un-auditable: "5 positions" means nothing without the bar the
    # equity and exposure were measured against.
    equity_snapshot: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    exposure_snapshot: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    open_positions_snapshot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class RiskEvent(Base, TimestampMixin):
    """Append-only narrative log: checks, breaker transitions, overrides, resets.

    Kept separate from `RiskCheck` because most risk-relevant things are not checks —
    a breaker tripping, a human clearing a halt, a policy being replaced.
    """

    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str | None] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class CircuitBreaker(Base, TimestampMixin):
    """Per-account breaker state.

    `MANUAL` is distinct from `OPEN` on purpose: a breaker tripped by a metric may
    reset when the metric recovers, but a manual stop is a human decision and only a
    human clears it. Collapsing the two would let an automatic reset silently undo an
    emergency stop.
    """

    __tablename__ = "circuit_breaker_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    trading_account_id: Mapped[str] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    state: Mapped[BreakerState] = mapped_column(
        Enum(BreakerState, native_enum=False, length=8),
        nullable=False,
        default=BreakerState.CLOSED,
    )
    trigger: Mapped[str] = mapped_column(String(40), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    halted_new_risk: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    positions_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_cancelled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tripped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cleared_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    events: Mapped[list[RiskEvent]] = relationship(
        "RiskEvent",
        primaryjoin="foreign(RiskEvent.detail)['breaker_id'].as_string() == CircuitBreaker.id",
        viewonly=True,
    )


TERMINAL_BREAKER_STATES = {BreakerState.OPEN, BreakerState.MANUAL}
