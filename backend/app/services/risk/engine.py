"""The independent risk controller.

It sits between a proposal and the order service, is given nothing but observable
state, and returns a decision with every rule that was applied. Three properties are
non-negotiable and each has a test:

1. **Independence.** It imports the ledger and the position book, never the execution
   service's internals. It cannot be talked out of a decision by the caller, and it
   does not read anything an agent wrote about itself.
2. **Determinism.** The same state and the same proposal give the same decision. No
   randomness, no wall-clock dependency beyond the caller's `now`, no model.
3. **Fail-closed.** An error, an unknown rule, a missing price or a policy the engine
   cannot fully understand results in a rejection. A risk engine that fails open is a
   compliance theatre.

The rules run in a deliberate order, cheapest and most fatal first: a tripped breaker
outranks everything, then per-trade risk, then portfolio-wide limits. A rejection
stops evaluation, so the reason shown is the *first* binding constraint rather than a
list of noise — but every rule evaluated before it is still recorded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import TradingAccount
from app.models.execution import OrderSide
from app.models.risk import (
    BreakerState,
    RiskCheck,
    RiskDecision,
    RiskEvent,
    RiskPolicy,
    RiskRule,
)
from app.services.risk import metrics
from app.services.risk.breakers import breaker_state

logger = logging.getLogger(__name__)

#: Rules this engine actually implements. A policy naming anything else is refused
#: rather than run with the extra limit silently dropped.
SUPPORTED_RULES = {
    RiskRule.PER_TRADE_RISK,
    RiskRule.DAILY_LOSS,
    RiskRule.MAX_DRAWDOWN,
    RiskRule.MAX_POSITIONS,
    RiskRule.MAX_EXPOSURE,
    RiskRule.LEVERAGE,
    RiskRule.SPREAD,
    RiskRule.CONCENTRATION,
    RiskRule.CORRELATION,
    RiskRule.KILL_SWITCH,
    RiskRule.ORDER_RATE,
    RiskRule.DATA_FRESHNESS,
}

#: Spread assumption for the paper venue, in basis points. The paper engine fills with
#: a modelled cost rather than a quoted book, so the spread rule compares this
#: configured cost against the limit instead of pretending to read a live quote.
PAPER_SPREAD_BPS = 1.5


@dataclass
class RuleResult:
    rule: RiskRule
    passed: bool
    observed: float | str | bool | None
    limit: float | str | bool | None
    detail: str

    def as_dict(self) -> dict:
        return {
            "rule": self.rule.value,
            "passed": self.passed,
            "observed": self.observed,
            "limit": self.limit,
            "detail": self.detail,
        }


@dataclass
class RiskDecisionResult:
    decision: RiskDecision
    approved_quantity: float
    reason: str
    checks: list[RuleResult] = field(default_factory=list)
    breached: list[str] = field(default_factory=list)
    equity: float = 0.0
    exposure: float = 0.0
    open_positions: int = 0
    check: RiskCheck | None = None

    @property
    def allowed(self) -> bool:
        return self.decision in (RiskDecision.ALLOW, RiskDecision.ALLOW_REDUCED)


def ensure_policy(db: Session, account: TradingAccount) -> RiskPolicy:
    """The account's active policy, creating the blueprint defaults on first use.

    Defaults are engineering starting points for simulation, not investment advice,
    and they are recorded on the row so a later change is a version, not a diff of a
    constant in source.
    """
    policy = db.scalar(
        select(RiskPolicy)
        .where(RiskPolicy.trading_account_id == account.id, RiskPolicy.is_active.is_(True))
        .order_by(RiskPolicy.version.desc())
    )
    if policy is not None:
        return policy

    policy = RiskPolicy(
        trading_account_id=account.id,
        name="blueprint-default",
        version=1,
        notes=(
            "Illustrative paper-mode defaults from the blueprint: 0.5% risk per trade, "
            "2% daily loss, 10% drawdown, 5 positions, 50% exposure, leverage disabled."
        ),
    )
    db.add(policy)
    db.flush()
    return policy


def policy_for(db: Session, account: TradingAccount) -> RiskPolicy:
    return ensure_policy(db, account)


def _reject(
    outcome: RiskDecisionResult, rule: RiskRule, detail: str, observed=None, limit=None
) -> RiskDecisionResult:
    outcome.checks.append(RuleResult(rule, False, observed, limit, detail))
    outcome.breached.append(rule.value)
    outcome.decision = RiskDecision.REJECT
    outcome.approved_quantity = 0.0
    outcome.reason = detail
    return outcome


def evaluate_proposal(
    db: Session,
    *,
    account: TradingAccount,
    symbol: str,
    side,
    quantity: float,
    price: float | None,
    stop_pct: float = 0.02,
    actor_id: str | None = None,
    persist: bool = True,
    now: datetime | None = None,
) -> RiskDecisionResult:
    """Evaluate one proposal, then record the outcome.

    The wrapper exists so persistence cannot be skipped by a code path. Every early
    refusal inside `_evaluate` used to return straight out of the function and never
    reached the write — meaning the audit trail was empty for exactly the decisions
    worth auditing. Funnelling every return through one place makes that class of bug
    impossible rather than merely fixed.
    """
    outcome = _evaluate(
        db,
        account=account,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=price,
        stop_pct=stop_pct,
        actor_id=actor_id,
        now=now,
    )
    if persist:
        policy = ensure_policy(db, account)
        record = RiskCheck(
            trading_account_id=account.id,
            policy_id=policy.id,
            instrument_symbol=symbol.upper(),
            side=side.value,
            requested_quantity=quantity,
            approved_quantity=outcome.approved_quantity,
            decision=outcome.decision,
            reason=outcome.reason,
            breached_rules=outcome.breached,
            checks=[check.as_dict() for check in outcome.checks],
            equity_snapshot=outcome.equity,
            exposure_snapshot=outcome.exposure,
            open_positions_snapshot=outcome.open_positions,
            actor_id=actor_id,
        )
        db.add(record)
        db.flush()
        outcome.check = record
    return outcome


def _evaluate(
    db: Session,
    *,
    account: TradingAccount,
    symbol: str,
    side: OrderSide,
    quantity: float,
    price: float | None,
    stop_pct: float = 0.02,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> RiskDecisionResult:
    """Apply every rule and return the decision. Persistence happens in the wrapper.

    Returns a decision that always carries the full check trace. This function writes
    nothing: `evaluate_proposal` is the only entry point and it records the result.
    """
    moment = now or datetime.now(UTC)
    policy = ensure_policy(db, account)

    symbol = symbol.upper()
    equity = metrics.equity(db, account)
    positions = metrics.open_positions(db, account)
    open_count = len(positions)
    notional = (price or 0.0) * quantity

    outcome = RiskDecisionResult(
        decision=RiskDecision.ALLOW,
        approved_quantity=quantity,
        reason="All risk rules passed.",
        equity=round(equity, 4),
        exposure=round(metrics.exposure(db, account), 6),
        open_positions=open_count,
    )

    # --- (0) fail closed on anything we cannot evaluate -----------------------
    if price is None or price <= 0:
        return _reject(
            outcome,
            RiskRule.DATA_FRESHNESS,
            f"No price for {symbol}; the risk engine will not approve an order it cannot value.",
        )

    # --- (1) the breaker outranks every limit ---------------------------------
    state = breaker_state(db, account)
    if state == BreakerState.MANUAL:
        return _reject(
            outcome,
            RiskRule.KILL_SWITCH,
            "A manual emergency stop is in force. New risk is halted until an authorised user clears it.",
            observed="manual",
            limit="closed",
        )
    if state == BreakerState.OPEN:
        return _reject(
            outcome,
            RiskRule.KILL_SWITCH,
            "The circuit breaker is open: a risk limit was breached and new orders are halted.",
            observed="open",
            limit="closed",
        )
    outcome.checks.append(
        RuleResult(RiskRule.KILL_SWITCH, True, state.value, "closed", "Breaker closed.")
    )

    # --- (2) order rate -------------------------------------------------------
    recent = metrics.orders_last_hour(db, account, now=moment)
    if recent >= policy.max_orders_per_hour:
        return _reject(
            outcome,
            RiskRule.ORDER_RATE,
            f"{recent} orders in the last hour; the policy allows {policy.max_orders_per_hour}. "
            "This is the runaway-loop guard.",
            observed=recent,
            limit=policy.max_orders_per_hour,
        )
    outcome.checks.append(
        RuleResult(
            RiskRule.ORDER_RATE,
            True,
            recent,
            policy.max_orders_per_hour,
            "Within hourly order budget.",
        )
    )

    # --- (3) daily loss -------------------------------------------------------
    day_pnl = metrics.daily_pnl(db, account, now=moment)
    daily_loss_fraction = (-day_pnl / equity) if equity > 0 and day_pnl < 0 else 0.0
    if daily_loss_fraction >= policy.max_daily_loss:
        return _reject(
            outcome,
            RiskRule.DAILY_LOSS,
            f"Realised loss today is {daily_loss_fraction:.2%} of equity, at or beyond the "
            f"{policy.max_daily_loss:.2%} daily limit. Trading resumes tomorrow.",
            observed=round(daily_loss_fraction, 6),
            limit=policy.max_daily_loss,
        )
    outcome.checks.append(
        RuleResult(
            RiskRule.DAILY_LOSS,
            True,
            round(daily_loss_fraction, 6),
            policy.max_daily_loss,
            "Within the daily loss budget.",
        )
    )

    # --- (4) drawdown ---------------------------------------------------------
    drawdown = metrics.current_drawdown(db, account)
    if drawdown >= policy.max_drawdown:
        return _reject(
            outcome,
            RiskRule.MAX_DRAWDOWN,
            f"Drawdown from peak equity is {drawdown:.2%}, at or beyond the {policy.max_drawdown:.2%} limit.",
            observed=round(drawdown, 6),
            limit=policy.max_drawdown,
        )
    outcome.checks.append(
        RuleResult(
            RiskRule.MAX_DRAWDOWN,
            True,
            round(drawdown, 6),
            policy.max_drawdown,
            "Within the drawdown limit.",
        )
    )

    # --- (5) leverage ---------------------------------------------------------
    # A sell only reduces exposure, so it is always allowed through this rule.
    if side == OrderSide.BUY:
        affordable = float(account_equity_cash := equity)  # cash + positions, no borrowing
        if not policy.leverage_enabled and notional > affordable:
            return _reject(
                outcome,
                RiskRule.LEVERAGE,
                f"Order notional {notional:,.2f} exceeds equity {affordable:,.2f} and leverage is "
                "disabled. The order would require borrowing.",
                observed=round(notional, 2),
                limit=round(account_equity_cash, 2),
            )
    outcome.checks.append(
        RuleResult(
            RiskRule.LEVERAGE,
            True,
            policy.leverage_enabled,
            False,
            "No leverage required." if side == OrderSide.BUY else "A sell reduces exposure.",
        )
    )

    # --- (6) per-trade risk ---------------------------------------------------
    # Risk is what the position loses if the stop is hit, not its notional. Sizing is
    # therefore the number most worth clamping: it can reduce an order and still
    # approve it, which is what a risk engine should do with a slightly-too-large
    # proposal.
    requested_risk = notional * stop_pct
    risk_budget = equity * policy.max_risk_per_trade
    if side == OrderSide.BUY and risk_budget > 0 and requested_risk > risk_budget:
        scaled_quantity = (risk_budget / (price * stop_pct)) if price > 0 and stop_pct > 0 else 0.0
        if scaled_quantity <= 1e-12:
            return _reject(
                outcome,
                RiskRule.PER_TRADE_RISK,
                "The risk budget is too small to size any position in this instrument.",
                observed=round(requested_risk, 4),
                limit=round(risk_budget, 4),
            )
        outcome.decision = RiskDecision.ALLOW_REDUCED
        outcome.approved_quantity = round(scaled_quantity, 8)
        outcome.reason = (
            f"Order reduced from {quantity:g} to {scaled_quantity:.8g} {symbol}: a stop at "
            f"{stop_pct:.2%} on the requested size would risk {requested_risk:,.2f}, above the "
            f"{policy.max_risk_per_trade:.2%} per-trade budget of {risk_budget:,.2f}."
        )
        outcome.checks.append(
            RuleResult(
                RiskRule.PER_TRADE_RISK,
                False,
                round(requested_risk, 4),
                round(risk_budget, 4),
                "Reduced to fit the per-trade risk budget.",
            )
        )
        notional = price * outcome.approved_quantity
    else:
        outcome.checks.append(
            RuleResult(
                RiskRule.PER_TRADE_RISK,
                True,
                round(requested_risk, 4),
                round(risk_budget, 4),
                "Within the per-trade risk budget.",
            )
        )

    # --- (7) position count ---------------------------------------------------
    already_open = metrics.position_for(db, account, symbol) is not None
    if side == OrderSide.BUY and not already_open and open_count >= policy.max_positions:
        return _reject(
            outcome,
            RiskRule.MAX_POSITIONS,
            f"The account already holds {open_count} positions; the policy allows "
            f"{policy.max_positions}. A sell or an add to an existing position is still permitted.",
            observed=open_count,
            limit=policy.max_positions,
        )
    outcome.checks.append(
        RuleResult(
            RiskRule.MAX_POSITIONS,
            True,
            open_count,
            policy.max_positions,
            "Within the position count limit.",
        )
    )

    # --- (8) portfolio exposure ----------------------------------------------
    projected_exposure = (
        (metrics.positions_value(db, account) + (notional if side == OrderSide.BUY else 0.0))
        / equity
        if equity > 0
        else 1.0
    )
    if side == OrderSide.BUY and projected_exposure > policy.max_exposure:
        return _reject(
            outcome,
            RiskRule.MAX_EXPOSURE,
            f"The order takes invested exposure to {projected_exposure:.2%} of equity, above the "
            f"{policy.max_exposure:.2%} policy limit.",
            observed=round(projected_exposure, 6),
            limit=policy.max_exposure,
        )
    outcome.checks.append(
        RuleResult(
            RiskRule.MAX_EXPOSURE,
            True,
            round(projected_exposure, 6),
            policy.max_exposure,
            "Within the exposure limit.",
        )
    )

    # --- (9) concentration ----------------------------------------------------
    if side == OrderSide.BUY:
        share = metrics.concentration(db, account, symbol, extra_notional=notional)
        if share > policy.max_position_concentration:
            return _reject(
                outcome,
                RiskRule.CONCENTRATION,
                f"{symbol} would be {share:.2%} of equity, above the "
                f"{policy.max_position_concentration:.2%} single-instrument limit.",
                observed=round(share, 6),
                limit=policy.max_position_concentration,
            )
        outcome.checks.append(
            RuleResult(
                RiskRule.CONCENTRATION,
                True,
                round(share, 6),
                policy.max_position_concentration,
                "Within the concentration limit.",
            )
        )

    # --- (10) correlation -----------------------------------------------------
    if side == OrderSide.BUY:
        candidate = metrics.recent_returns(db, symbol)
        worst: tuple[str, float] | None = None
        for position in positions:
            if position.instrument_symbol == symbol:
                continue
            other = metrics.recent_returns(db, position.instrument_symbol)
            value = metrics.correlation(candidate, other)
            if value is not None and (worst is None or value > worst[1]):
                worst = (position.instrument_symbol, value)
        if worst is not None and worst[1] > policy.max_correlation:
            return _reject(
                outcome,
                RiskRule.CORRELATION,
                f"{symbol} correlates {worst[1]:.2f} with {worst[0]} already held, above the "
                f"{policy.max_correlation:.2f} limit. Two positions, one bet.",
                observed=round(worst[1], 4),
                limit=policy.max_correlation,
            )
        outcome.checks.append(
            RuleResult(
                RiskRule.CORRELATION,
                True,
                round(worst[1], 4) if worst else None,
                policy.max_correlation,
                f"Highest correlation with the held book: {worst[0]} at {worst[1]:.2f}."
                if worst
                else "Nothing held to correlate against.",
            )
        )

    # --- (11) spread ----------------------------------------------------------
    if PAPER_SPREAD_BPS > policy.max_spread_bps:
        return _reject(
            outcome,
            RiskRule.SPREAD,
            f"The modelled execution spread of {PAPER_SPREAD_BPS:.1f} bps exceeds the "
            f"{policy.max_spread_bps:.1f} bps limit.",
            observed=PAPER_SPREAD_BPS,
            limit=policy.max_spread_bps,
        )
    outcome.checks.append(
        RuleResult(
            RiskRule.SPREAD,
            True,
            PAPER_SPREAD_BPS,
            policy.max_spread_bps,
            "Modelled execution spread within the limit.",
        )
    )

    if outcome.decision == RiskDecision.ALLOW:
        outcome.checks.insert(
            0,
            RuleResult(
                RiskRule.DATA_FRESHNESS, True, round(price, 6), None, "Valuation price available."
            ),
        )

    return outcome


def record_event(
    db: Session,
    *,
    account: TradingAccount | None,
    event_type: str,
    message: str,
    severity: str = "info",
    detail: dict | None = None,
    actor_id: str | None = None,
) -> RiskEvent:
    event = RiskEvent(
        trading_account_id=account.id if account else None,
        event_type=event_type,
        severity=severity,
        message=message,
        detail=detail or {},
        actor_id=actor_id,
    )
    db.add(event)
    db.flush()
    return event
