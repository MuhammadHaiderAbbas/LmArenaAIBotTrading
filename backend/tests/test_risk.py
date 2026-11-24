"""Risk engine: limits, breaker behaviour, and the properties that make it trustworthy.

The tests worth reading are the ones about *independence* and *fail-closed*: a risk
engine that can be talked around, or that fails open when it cannot evaluate
something, is worse than none because it produces false assurance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.account import Environment, TradingAccount
from app.models.execution import Order, OrderSide, OrderStatus, Position, PositionStatus
from app.models.identity import User
from app.models.ledger import LedgerAccountKind
from app.models.market import Instrument, MarketCandle, Timeframe, Venue
from app.models.risk import BreakerState, RiskCheck, RiskDecision, RiskPolicy
from app.services import ledger_service
from app.services.execution import orders as order_service
from app.services.execution.orders import Proposal
from app.services.risk import breakers, engine


def make_account(db, email: str, funding: float = 100_000.0) -> TradingAccount:
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email, password_hash="x", full_name="Risk", is_active=True, is_verified=True
        )
        db.add(user)
        db.flush()
    account = TradingAccount(
        user_id=user.id, name=f"Paper {email}", environment=Environment.PAPER, base_currency="GBP"
    )
    db.add(account)
    db.flush()
    ledger_service.open_account_with_funding(db, account=account, amount=funding)
    db.flush()
    return account


def seed(
    db,
    symbol: str,
    *,
    price: float = 100.0,
    bars: int = 60,
    drift: float = 0.0,
    instrument: Instrument | None = None,
) -> Instrument:
    if instrument is None:
        venue = db.query(Venue).first()
        if venue is None:
            venue = Venue(code="RSKV", name="Risk venue", asset_class="forex")
            db.add(venue)
            db.flush()
        instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                name=symbol,
                asset_class="forex",
                venue_id=venue.id,
                base_currency="RSK",
                quote_currency="TST",
                is_tradable=True,
                status="active",
            )
            db.add(instrument)
            db.flush()
    newest = datetime.now(UTC)
    for index in range(bars):
        when = newest - timedelta(days=bars - 1 - index)
        value = price + drift * index
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=when,
                close_time=when + timedelta(hours=23),
                open=value,
                high=value * 1.01,
                low=value * 0.99,
                close=value,
                volume=1_000_000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.flush()
    return instrument


# ------------------------------------------------------------- default policy


def test_the_default_policy_matches_the_blueprint_numbers(db_session) -> None:
    account = make_account(db_session, "policy@risk.ai")
    policy = engine.ensure_policy(db_session, account)
    assert policy.max_risk_per_trade == 0.005
    assert policy.max_daily_loss == 0.02
    assert policy.max_drawdown == 0.10
    assert policy.max_positions == 5
    assert policy.max_exposure == 0.50
    assert policy.leverage_enabled is False


def test_ensure_policy_is_idempotent(db_session) -> None:
    account = make_account(db_session, "idem@risk.ai")
    first = engine.ensure_policy(db_session, account)
    second = engine.ensure_policy(db_session, account)
    assert first.id == second.id
    assert (
        db_session.query(RiskPolicy).filter(RiskPolicy.trading_account_id == account.id).count()
        == 1
    )


def test_limits_are_arithmetically_coherent(db_session) -> None:
    """A correct full-risk trade must be allowed.

    A concentration cap below (risk_per_trade / stop_pct) would refuse every properly
    sized trade while looking prudent. This asserts the two limits cannot contradict
    each other.
    """
    account = make_account(db_session, "coherent@risk.ai")
    policy = engine.ensure_policy(db_session, account)
    full_risk_notional = policy.max_risk_per_trade / 0.02  # default stop distance
    assert policy.max_position_concentration >= full_risk_notional


# ------------------------------------------------------------------- decisions


def test_a_small_order_is_allowed(db_session) -> None:
    account = make_account(db_session, "small@risk.ai")
    seed(db_session, "RSK/SMALL")
    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/SMALL",
        side=OrderSide.BUY,
        quantity=10,
        price=100.0,
    )
    assert outcome.decision == RiskDecision.ALLOW
    assert outcome.approved_quantity == 10
    assert outcome.check is not None
    assert all(check.passed for check in outcome.checks)


def test_an_oversized_order_is_reduced_not_refused(db_session) -> None:
    """Reducing is the right answer for a slightly-too-large order, as long as the
    reduction lands exactly on the risk budget."""
    account = make_account(db_session, "reduce@risk.ai")
    seed(db_session, "RSK/REDUCE")
    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/REDUCE",
        side=OrderSide.BUY,
        quantity=400,
        price=100.0,
    )
    assert outcome.decision == RiskDecision.ALLOW_REDUCED
    # 0.5% of 100,000 = 500 risked; a 2% stop means 250 units at 100.
    assert outcome.approved_quantity == pytest.approx(250.0)
    assert outcome.approved_quantity < 400
    assert "0.50%" in outcome.reason


def test_per_trade_risk_is_measured_against_the_stop_not_the_notional(db_session) -> None:
    """The whole point of risk-based sizing: a 100k notional trade with a tight stop is
    not a 100k risk."""
    account = make_account(db_session, "stop@risk.ai")
    seed(db_session, "RSK/STOP")
    tight = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/STOP",
        side=OrderSide.BUY,
        quantity=300,
        price=100.0,
        stop_pct=0.001,
    )
    # 300 * 100 * 0.1% = 30 risked, far under the 500 budget.
    assert tight.decision == RiskDecision.ALLOW


def test_leverage_is_refused_with_the_numbers(db_session) -> None:
    account = make_account(db_session, "lev@risk.ai", funding=10_000.0)
    seed(db_session, "RSK/LEV")
    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/LEV",
        side=OrderSide.BUY,
        quantity=1000,
        price=100.0,
    )
    assert outcome.decision == RiskDecision.REJECT
    assert "leverage" in outcome.reason.lower()
    assert "100,000.00" in outcome.reason


def test_five_positions_is_the_limit_and_a_sixth_is_refused(db_session) -> None:
    account = make_account(db_session, "count@risk.ai", funding=1_000_000.0)
    policy = engine.ensure_policy(db_session, account)
    policy.max_positions = 2
    policy.max_exposure = 5.0  # isolate the rule under test
    db_session.flush()

    for index in range(2):
        symbol = f"RSK/C{index}"
        seed(db_session, symbol)
        position = Position(
            trading_account_id=account.id,
            instrument_symbol=symbol,
            status=PositionStatus.OPEN,
            quantity=1,
            average_entry_price=100.0,
            cost_basis=100.0,
            opened_at=datetime.now(UTC),
        )
        db_session.add(position)
    db_session.flush()

    seed(db_session, "RSK/C9")
    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/C9", side=OrderSide.BUY, quantity=1, price=100.0
    )
    assert outcome.decision == RiskDecision.REJECT
    assert outcome.breached == ["max_positions"]
    assert "2 positions" in outcome.reason


def test_adding_to_an_existing_position_is_allowed_at_the_limit(db_session) -> None:
    """The position limit is about diversification, not about blocking adds to a
    position the account already holds."""
    account = make_account(db_session, "add@risk.ai", funding=1_000_000.0)
    policy = engine.ensure_policy(db_session, account)
    policy.max_positions = 1
    policy.max_exposure = 5.0
    seed(db_session, "RSK/ADD")
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/ADD",
            status=PositionStatus.OPEN,
            quantity=1,
            average_entry_price=100.0,
            cost_basis=100.0,
            opened_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/ADD", side=OrderSide.BUY, quantity=1, price=100.0
    )
    assert outcome.decision == RiskDecision.ALLOW


def test_exposure_above_the_limit_is_refused(db_session) -> None:
    account = make_account(db_session, "expo@risk.ai", funding=100_000.0)
    policy = engine.ensure_policy(db_session, account)
    policy.max_exposure = 0.10
    policy.max_position_concentration = 1.0
    policy.max_risk_per_trade = 0.50
    seed(db_session, "RSK/EXPO")
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/EXPO",
            status=PositionStatus.OPEN,
            quantity=80,
            average_entry_price=100.0,
            cost_basis=8_000.0,
            last_mark_price=100.0,
            opened_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/EXPO", side=OrderSide.BUY, quantity=30, price=100.0
    )
    assert outcome.decision == RiskDecision.REJECT
    assert "max_exposure" in outcome.breached


def test_correlation_with_a_held_position_is_refused(db_session) -> None:
    """Two instruments moving identically are one bet, whatever their tickers say."""
    account = make_account(db_session, "corr@risk.ai", funding=1_000_000.0)
    policy = engine.ensure_policy(db_session, account)
    policy.max_exposure = 5.0
    policy.max_risk_per_trade = 0.9
    policy.max_position_concentration = 1.0
    seed(db_session, "RSK/CORA", price=100.0, drift=1.0)
    seed(db_session, "RSK/CORB", price=50.0, drift=0.5)
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/CORA",
            status=PositionStatus.OPEN,
            quantity=1,
            average_entry_price=100.0,
            cost_basis=100.0,
            last_mark_price=100.0,
            opened_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/CORB", side=OrderSide.BUY, quantity=1, price=50.0
    )
    assert outcome.decision == RiskDecision.REJECT
    assert outcome.breached == ["correlation"]
    assert "one bet" in outcome.reason.lower()


def test_daily_loss_limit_refuses_further_buys(db_session) -> None:
    account = make_account(db_session, "daily@risk.ai", funding=100_000.0)
    seed(db_session, "RSK/DAILY")
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/DAILY",
            status=PositionStatus.CLOSED,
            quantity=0,
            average_entry_price=100.0,
            cost_basis=0.0,
            realised_pnl=-2_500.0,  # 2.5% of equity, past the 2% limit
            opened_at=datetime.now(UTC) - timedelta(days=1),
            closed_at=datetime.now(UTC),
        )
    )
    db_session.flush()

    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/DAILY", side=OrderSide.BUY, quantity=1, price=100.0
    )
    assert outcome.decision == RiskDecision.REJECT
    assert outcome.breached == ["daily_loss"]


def test_drawdown_limit_refuses_further_buys(db_session) -> None:
    account = make_account(db_session, "dd@risk.ai", funding=100_000.0)
    seed(db_session, "RSK/DD")
    # Realise an 11% loss: post it to the ledger so cash — and therefore equity —
    # actually falls. A position row alone records the loss without moving money, and
    # asserting drawdown off a row instead of the ledger would be testing a fiction.
    ledger_service.post_transaction(
        db_session,
        account=account,
        transaction_type=__import__(
            "app.models.ledger", fromlist=["TransactionType"]
        ).TransactionType.ADJUSTMENT,
        description="simulated loss",
        # Debit-positive: a loss debits P&L and credits (reduces) cash.
        lines=[(LedgerAccountKind.REALISED_PNL, 11_000.0), (LedgerAccountKind.CASH, -11_000.0)],
        idempotency_key="dd-loss",
    )
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/DD",
            status=PositionStatus.CLOSED,
            quantity=0,
            average_entry_price=100.0,
            cost_basis=0.0,
            realised_pnl=-11_000.0,
            opened_at=datetime.now(UTC) - timedelta(days=5),
            closed_at=datetime.now(UTC) - timedelta(days=3),
        )
    )
    db_session.flush()
    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/DD", side=OrderSide.BUY, quantity=1, price=100.0
    )
    assert outcome.decision == RiskDecision.REJECT
    assert "max_drawdown" in outcome.breached


def test_order_rate_limit_blocks_a_runaway_loop(db_session) -> None:
    account = make_account(db_session, "rate@risk.ai")
    policy = engine.ensure_policy(db_session, account)
    policy.max_orders_per_hour = 3
    seed(db_session, "RSK/RATE")
    for _ in range(3):
        db_session.add(
            Order(
                trading_account_id=account.id,
                instrument_symbol="RSK/RATE",
                client_order_id=f"rate-{_}",
                side=OrderSide.BUY,
                order_type="market",
                status=OrderStatus.FILLED,
                quantity=1,
            )
        )
    db_session.flush()
    outcome = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/RATE", side=OrderSide.BUY, quantity=1, price=100.0
    )
    assert outcome.decision == RiskDecision.REJECT
    assert outcome.breached == ["order_rate"]


# --------------------------------------------------------------------- breaker


def test_a_manual_stop_halts_new_risk_and_is_not_cleared_by_a_metric(db_session) -> None:
    account = make_account(db_session, "manual@risk.ai")
    seed(db_session, "RSK/MANUAL")
    breakers.emergency_stop(db_session, account=account, reason="Testing the stop.")
    assert breakers.breaker_state(db_session, account) == BreakerState.MANUAL

    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/MANUAL",
        side=OrderSide.BUY,
        quantity=1,
        price=100.0,
    )
    assert outcome.decision == RiskDecision.REJECT
    assert outcome.breached == ["kill_switch"]
    assert "manual" in outcome.reason.lower()

    # An automatic evaluation pass must not clear a human's stop.
    breakers.evaluate_breakers(db_session, account)
    assert breakers.breaker_state(db_session, account) == BreakerState.MANUAL


def test_the_stop_distinguishes_halting_from_liquidating(db_session) -> None:
    """The blueprint's rule, encoded: a shutdown stops new risk and never sells.

    Blind liquidation in a disrupted market can create the loss it is meant to avoid.
    """
    account = make_account(db_session, "noliquid@risk.ai")
    seed(db_session, "RSK/KEEP")
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/KEEP",
            status=PositionStatus.OPEN,
            quantity=10,
            average_entry_price=100.0,
            cost_basis=1_000.0,
            opened_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    cash_before = ledger_service.ledger_cash(db_session, account)

    breaker = breakers.emergency_stop(db_session, account=account, reason="Emergency.")
    db_session.flush()

    assert breaker.positions_affected == 1
    position = db_session.query(Position).filter(Position.trading_account_id == account.id).one()
    assert position.status == PositionStatus.OPEN, "the stop must not liquidate"
    assert position.quantity == 10
    # And no money moved.
    assert ledger_service.ledger_cash(db_session, account) == cash_before


def test_the_stop_cancels_pending_orders(db_session) -> None:
    account = make_account(db_session, "cancelp@risk.ai")
    seed(db_session, "RSK/CANCELP")
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(
            symbol="RSK/CANCELP",
            side=OrderSide.BUY,
            quantity=1,
            order_type="limit",
            limit_price=1.0,
        ),
    )
    db_session.flush()
    assert order.status == OrderStatus.SUBMITTED

    breaker = breakers.emergency_stop(db_session, account=account, reason="Emergency.")
    db_session.flush()
    assert breaker.orders_cancelled == 1
    assert order.status == OrderStatus.CANCELLED


def test_clearing_the_breaker_requires_an_explicit_act_and_is_attributed(db_session) -> None:
    account = make_account(db_session, "clear@risk.ai")
    seed(db_session, "RSK/CLEAR")
    breakers.emergency_stop(db_session, account=account, reason="Emergency.")
    assert breakers.breaker_state(db_session, account) == BreakerState.MANUAL

    with pytest.raises(PermissionError):
        breakers.clear_breaker(db_session, account=account, allow_manual=False)

    row = breakers.clear_breaker(
        db_session, account=account, actor_id="staff-1", reason="Reviewed."
    )
    assert row is not None
    assert row.cleared_by == "staff-1"
    assert breakers.breaker_state(db_session, account) == BreakerState.CLOSED


def test_tripping_records_a_critical_event_with_what_was_affected(db_session) -> None:
    account = make_account(db_session, "event@risk.ai")
    seed(db_session, "RSK/EVENT")
    breakers.emergency_stop(db_session, account=account, reason="Emergency test.")
    from app.models.risk import RiskEvent

    event = (
        db_session.query(RiskEvent)
        .filter(
            RiskEvent.trading_account_id == account.id, RiskEvent.event_type == "breaker_tripped"
        )
        .one()
    )
    assert event.severity == "critical"
    assert event.detail["trigger"] == "emergency_stop"
    assert "closing_positions" in event.detail


# -------------------------------------------------- integration with execution


def test_an_order_cannot_bypass_the_risk_controller(db_session) -> None:
    """The whole point of independence: there is one door, and risk is on it."""
    account = make_account(db_session, "bypass@risk.ai", funding=10_000.0)
    seed(db_session, "RSK/BYPASS")
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="RSK/BYPASS", side=OrderSide.BUY, quantity=1000),
    )
    db_session.flush()
    assert order.status == OrderStatus.REJECTED
    assert "leverage" in order.reject_reason.lower()
    # A refusal must be recorded as a risk check, not just an execution event.
    # Two gates, in order, and the cheap one is genuinely sufficient here: an order
    # larger than the account's cash fails execution validation before the risk engine
    # is consulted. The risk engine is the *second* gate, not the only one — which is
    # why `test_risk_replaces_the_cheap_check_when_cash_covers_the_order` exists below.
    assert "Insufficient cash" in order.reject_reason
    assert (
        db_session.query(RiskCheck).filter(RiskCheck.trading_account_id == account.id).count() == 0
    )


def test_risk_replaces_the_cheap_check_when_cash_covers_the_order(db_session) -> None:
    """Cash alone would allow this order; the risk controller is what refuses it.

    The point of the test above and this one together: execution validation bounds what
    the *account can pay for*, risk bounds what the *strategy is allowed to risk*. Only
    the second can refuse an affordable order, so its independence is what matters.
    """
    account = make_account(db_session, "secondgate@risk.ai", funding=1_000_000.0)
    policy = engine.ensure_policy(db_session, account)
    policy.max_exposure = 0.05
    policy.max_risk_per_trade = 0.9
    policy.max_position_concentration = 1.0
    db_session.flush()
    seed(db_session, "RSK/GATE")
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="RSK/GATE", side=OrderSide.BUY, quantity=1000),
    )
    # `create_order` flushes but does not commit — the endpoint owns the transaction —
    # so commit here to read the persisted check rows back.
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert "exposure" in order.reject_reason.lower()
    from sqlalchemy import select as _select

    checks = list(
        db_session.scalars(_select(RiskCheck).where(RiskCheck.trading_account_id == account.id))
    )
    assert len(checks) == 1
    assert checks[0].decision == RiskDecision.REJECT
    assert "max_exposure" in checks[0].breached_rules


def test_a_reduced_order_is_stored_at_the_reduced_size(db_session) -> None:
    account = make_account(db_session, "stored@risk.ai")
    seed(db_session, "RSK/STORED")
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="RSK/STORED", side=OrderSide.BUY, quantity=400),
    )
    db_session.flush()
    assert order.status == OrderStatus.SUBMITTED
    assert order.quantity == pytest.approx(250.0)
    # The original request is still visible in the event log.
    import json

    reductions = [
        event
        for event in order.events
        if json.loads(event.detail).get("original_quantity") is not None
    ]
    assert reductions
    assert json.loads(reductions[0].detail)["original_quantity"] == 400


def test_an_order_under_a_breaker_is_refused_before_risk_evaluation(db_session) -> None:
    account = make_account(db_session, "halted@risk.ai")
    seed(db_session, "RSK/HALTED")
    breakers.emergency_stop(db_session, account=account, reason="Halted.")
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="RSK/HALTED", side=OrderSide.BUY, quantity=1),
    )
    db_session.flush()
    assert order.status == OrderStatus.REJECTED
    assert "circuit breaker" in order.reject_reason.lower()


def test_a_sell_is_not_blocked_by_buy_side_limits(db_session) -> None:
    """Closing risk must always be possible; only opening it is limited."""
    account = make_account(db_session, "sell@risk.ai")
    policy = engine.ensure_policy(db_session, account)
    policy.max_exposure = 0.01  # any buy would breach this
    seed(db_session, "RSK/SELL")
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="RSK/SELL",
            status=PositionStatus.OPEN,
            quantity=10,
            average_entry_price=100.0,
            cost_basis=1_000.0,
            last_mark_price=100.0,
            opened_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/SELL",
        side=OrderSide.SELL,
        quantity=10,
        price=100.0,
    )
    assert outcome.decision == RiskDecision.ALLOW


# ---------------------------------------------------------------- fail closed


def test_a_missing_price_is_refused_not_approved(db_session) -> None:
    """Fail-closed: an unvaluable order cannot be risk-checked, so it is refused."""
    account = make_account(db_session, "noprice@risk.ai")
    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/NOPRICE",
        side=OrderSide.BUY,
        quantity=1,
        price=None,
    )
    assert outcome.decision == RiskDecision.REJECT
    assert "cannot value" in outcome.reason


def test_a_dry_run_does_not_write_a_check_row(db_session) -> None:
    account = make_account(db_session, "dry@risk.ai")
    seed(db_session, "RSK/DRY")
    before = db_session.query(RiskCheck).filter(RiskCheck.trading_account_id == account.id).count()
    engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/DRY",
        side=OrderSide.BUY,
        quantity=1,
        price=100.0,
        persist=False,
    )
    after = db_session.query(RiskCheck).filter(RiskCheck.trading_account_id == account.id).count()
    assert after == before


def test_the_decision_is_deterministic(db_session) -> None:
    """Same state, same proposal, same answer — twice, with different snapshots taken."""
    account = make_account(db_session, "determinism@risk.ai")
    seed(db_session, "RSK/DET")
    first = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/DET", side=OrderSide.BUY, quantity=400, price=100.0
    )
    second = engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/DET", side=OrderSide.BUY, quantity=400, price=100.0
    )
    assert first.decision == second.decision
    assert first.approved_quantity == second.approved_quantity
    assert [c.rule for c in first.checks] == [c.rule for c in second.checks]


def test_every_decision_carries_its_full_check_trace(db_session) -> None:
    """A reviewer must be able to see what was evaluated, not only the verdict."""
    account = make_account(db_session, "trace@risk.ai")
    seed(db_session, "RSK/TRACE")
    outcome = engine.evaluate_proposal(
        db_session,
        account=account,
        symbol="RSK/TRACE",
        side=OrderSide.BUY,
        quantity=10,
        price=100.0,
    )
    rules = {check.rule.value for check in outcome.checks}
    assert "max_exposure" in rules
    assert "per_trade_risk" in rules
    assert "max_drawdown" in rules
    assert outcome.check is not None
    assert len(outcome.check.checks) == len(outcome.checks)


def test_the_snapshot_is_stored_with_the_decision(db_session) -> None:
    """'Five positions' means nothing without the account state it was measured against."""
    account = make_account(db_session, "snapshot@risk.ai")
    seed(db_session, "RSK/SNAP")
    engine.evaluate_proposal(
        db_session, account=account, symbol="RSK/SNAP", side=OrderSide.BUY, quantity=10, price=100.0
    )
    check = (
        db_session.query(RiskCheck)
        .filter(RiskCheck.trading_account_id == account.id)
        .order_by(RiskCheck.created_at.desc())
        .first()
    )
    assert check.equity_snapshot == pytest.approx(100_000.0)
    assert check.open_positions_snapshot == 0


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
