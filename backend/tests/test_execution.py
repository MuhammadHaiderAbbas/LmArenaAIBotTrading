"""Order validation, simulated fills, position accounting and reconciliation.

The tests that matter are the accounting ones: a fill that does not balance, a
position that disagrees with the ledger, or a rejected order that leaves a position
behind are the failures that make every later number wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.account import Environment, TradingAccount
from app.models.execution import (
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionStatus,
    TimeInForce,
)
from app.models.market import Instrument, MarketCandle, Timeframe, Venue
from app.services import auth_service, ledger_service
from app.services.execution import broker
from app.services.execution import orders as order_service
from app.services.execution.orders import Proposal


def make_account(db, email: str = "exec@test.ai", funding: float = 50_000.0) -> TradingAccount:
    try:
        user = auth_service.register(db, email=email, password="Str0ngPass!", full_name="Exec")
    except Exception:  # noqa: BLE001 - already registered in this suite
        user = db.query(__import__("app.models.identity", fromlist=["User"]).User).first()
    account = TradingAccount(
        user_id=user.id, name=f"Paper {email}", environment=Environment.PAPER, base_currency="GBP"
    )
    db.add(account)
    db.flush()
    ledger_service.open_account_with_funding(db, account=account, amount=funding)
    db.flush()
    return account


def seed_bars(
    db,
    symbol: str = "EXE/TEST",
    *,
    bars: int = 40,
    price: float = 100.0,
    age_hours: float = 0.0,
    volume: float = 1_000_000.0,
) -> Instrument:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="EXV", name="Exec venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="EXE",
            quote_currency="TST",
            is_tradable=True,
            status="active",
        )
        db.add(instrument)
        db.flush()

    # The newest bar must be fresh or validation refuses it, so anchor to now.
    newest = datetime.now(UTC) - timedelta(hours=age_hours)
    for index in range(bars):
        when = newest - timedelta(days=bars - 1 - index)
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=when,
                close_time=when + timedelta(hours=23),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=volume,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.flush()
    return instrument


def buy(db, account, symbol: str = "EXE/TEST", quantity: float = 10.0, **kwargs):
    proposal = Proposal(symbol=symbol, side=OrderSide.BUY, quantity=quantity, **kwargs)
    return order_service.create_order(db, account=account, proposal=proposal)


# ------------------------------------------------------------------ validation


def test_a_valid_proposal_is_submitted(db_session) -> None:
    account = make_account(db_session)
    seed_bars(db_session)
    order = buy(db_session, account)
    db_session.commit()
    assert order.status == OrderStatus.SUBMITTED
    assert order.reject_reason == ""


def test_stale_market_data_is_refused(db_session) -> None:
    """Trading on a price nobody could have got is the classic paper-engine lie."""
    account = make_account(db_session, email="stale@test.ai")
    seed_bars(db_session, symbol="EXE/STALE", age_hours=24 * 5)
    order = buy(db_session, account, symbol="EXE/STALE")
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert "stale" in order.reject_reason.lower()


def test_missing_market_data_is_refused(db_session) -> None:
    account = make_account(db_session, email="nodata@test.ai")
    instrument = seed_bars(db_session, symbol="EXE/NODATA", bars=1)
    # Remove the only bar.
    db_session.query(MarketCandle).filter(MarketCandle.instrument_id == instrument.id).delete()
    db_session.flush()
    order = buy(db_session, account, symbol="EXE/NODATA")
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert (
        "no 1d bars" in order.reject_reason.lower()
        or "does not exist" in order.reject_reason.lower()
    )


def test_unknown_instrument_is_refused(db_session) -> None:
    account = make_account(db_session, email="unknown@test.ai")
    order = buy(db_session, account, symbol="NOT/REAL")
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert "Unknown instrument" in order.reject_reason


def test_insufficient_cash_is_refused_and_says_why(db_session) -> None:
    account = make_account(db_session, email="poor@test.ai", funding=100.0)
    seed_bars(db_session, symbol="EXE/POOR")
    order = buy(db_session, account, symbol="EXE/POOR", quantity=100.0)
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert "Insufficient cash" in order.reject_reason
    assert "Leverage is disabled" in order.reject_reason


def test_short_selling_is_refused_by_name(db_session) -> None:
    account = make_account(db_session, email="short@test.ai")
    seed_bars(db_session, symbol="EXE/SHORT")
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="EXE/SHORT", side=OrderSide.SELL, quantity=5),
    )
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert "borrow" in order.reject_reason or "Short selling" in order.reject_reason


def test_rejected_orders_record_their_checks(db_session) -> None:
    account = make_account(db_session, email="checks@test.ai", funding=50.0)
    seed_bars(db_session, symbol="EXE/CHECKS")
    order = buy(db_session, account, symbol="EXE/CHECKS", quantity=100)
    db_session.commit()
    events = [event for event in order.events if event.event_type.value == "rejected"]
    assert events
    assert "checks" in events[0].detail


def test_a_rejected_order_creates_no_position(db_session) -> None:
    """A refusal must leave the account exactly as it was."""
    account = make_account(db_session, email="clean@test.ai", funding=10.0)
    seed_bars(db_session, symbol="EXE/CLEAN")
    buy(db_session, account, symbol="EXE/CLEAN", quantity=100)
    db_session.commit()
    assert db_session.query(Position).filter(Position.trading_account_id == account.id).count() == 0


def test_duplicate_client_order_id_is_idempotent(db_session) -> None:
    """The same instruction submitted twice must not double the position."""
    account = make_account(db_session, email="dupe@test.ai")
    seed_bars(db_session, symbol="EXE/DUPE")
    first = buy(db_session, account, symbol="EXE/DUPE", client_order_id="dupe-1")
    db_session.commit()
    broker.execute_order(db_session, account=account, order=first)
    db_session.commit()

    second = buy(db_session, account, symbol="EXE/DUPE", client_order_id="dupe-1")
    db_session.commit()
    assert second.id == first.id
    assert db_session.query(Position).filter(Position.trading_account_id == account.id).count() == 1


# --------------------------------------------------------------------- fills


def test_a_fill_posts_a_balanced_ledger_transaction(db_session) -> None:
    account = make_account(db_session, email="ledger@test.ai")
    seed_bars(db_session, symbol="EXE/LEDGER")
    order = buy(db_session, account, symbol="EXE/LEDGER")
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()

    assert order.status == OrderStatus.FILLED
    assert order.fills
    assert ledger_service.trial_balance(db_session, account) == 0


def test_a_fill_records_slippage_and_commission_separately(db_session) -> None:
    account = make_account(db_session, email="costs@test.ai")
    seed_bars(db_session, symbol="EXE/COSTS", price=100.0)
    order = buy(db_session, account, symbol="EXE/COSTS", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()

    fill = order.fills[0]
    assert fill.price > fill.reference_price, "a buy must fill at or above the mid"
    assert fill.slippage > 0
    assert fill.commission > 0
    assert order.total_slippage > 0
    assert order.total_commission > 0


def test_cash_falls_by_notional_plus_commission(db_session) -> None:
    account = make_account(db_session, email="cash@test.ai", funding=10_000.0)
    seed_bars(db_session, symbol="EXE/CASH", price=100.0)
    before = float(ledger_service.ledger_cash(db_session, account))
    order = buy(db_session, account, symbol="EXE/CASH", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()

    after = float(ledger_service.ledger_cash(db_session, account))
    spent = before - after
    notional = sum(fill.notional for fill in order.fills)
    commission = sum(fill.commission for fill in order.fills)
    assert spent == pytest.approx(notional + commission, abs=1e-6)


def test_a_large_order_fills_in_several_bites(db_session) -> None:
    """A bite-sized bar cannot absorb a huge order at one price."""
    account = make_account(db_session, email="bites@test.ai", funding=10_000_000.0)
    seed_bars(db_session, symbol="EXE/BITES", price=10.0, volume=1_000.0)
    order = buy(db_session, account, symbol="EXE/BITES", quantity=5000)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()
    assert len(order.fills) > 1
    # Later bites pay more: slippage degrades as liquidity is consumed.
    assert order.fills[-1].price > order.fills[0].price


def test_partial_fills_sum_to_the_order_quantity(db_session) -> None:
    account = make_account(db_session, email="sum@test.ai", funding=10_000_000.0)
    seed_bars(db_session, symbol="EXE/SUM", price=10.0, volume=500.0)
    order = buy(db_session, account, symbol="EXE/SUM", quantity=900)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()
    total = sum(fill.quantity for fill in order.fills)
    assert total == pytest.approx(order.filled_quantity, abs=1e-9)
    assert order.filled_quantity <= order.quantity + 1e-9


def test_a_non_marketable_limit_order_rests_unfilled(db_session) -> None:
    account = make_account(db_session, email="limit@test.ai")
    seed_bars(db_session, symbol="EXE/LIMIT", price=100.0)
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(
            symbol="EXE/LIMIT",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=50.0,
        ),
    )
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()
    assert order.fills == []
    assert order.status == OrderStatus.SUBMITTED
    assert db_session.query(Position).filter(Position.trading_account_id == account.id).count() == 0


def test_a_limit_order_never_fills_worse_than_its_limit(db_session) -> None:
    account = make_account(db_session, email="limit2@test.ai")
    seed_bars(db_session, symbol="EXE/LIM2", price=100.0)
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(
            symbol="EXE/LIM2",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=101.0,
        ),
    )
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()
    for fill in order.fills:
        assert fill.price <= 101.0


def test_cancelling_a_resting_order_is_recorded(db_session) -> None:
    account = make_account(db_session, email="cancel@test.ai")
    seed_bars(db_session, symbol="EXE/CANCEL", price=100.0)
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(
            symbol="EXE/CANCEL",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=10.0,
        ),
    )
    db_session.commit()
    order_service.cancel_order(db_session, order=order, reason="changed my mind")
    db_session.commit()
    assert order.status == OrderStatus.CANCELLED
    assert any(event.event_type.value == "cancelled" for event in order.events)


def test_a_terminal_order_cannot_be_cancelled_again(db_session) -> None:
    account = make_account(db_session, email="term@test.ai")
    seed_bars(db_session, symbol="EXE/TERM", price=100.0)
    order = buy(db_session, account, symbol="EXE/TERM", quantity=1)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()
    from app.services.errors import DomainError

    with pytest.raises(DomainError):
        order_service.cancel_order(db_session, order=order)


# ------------------------------------------------------------------ positions


def test_a_buy_opens_a_position_with_the_right_basis(db_session) -> None:
    account = make_account(db_session, email="pos@test.ai")
    seed_bars(db_session, symbol="EXE/POS", price=100.0)
    order = buy(db_session, account, symbol="EXE/POS", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()

    position = db_session.query(Position).filter(Position.trading_account_id == account.id).one()
    assert position.status == PositionStatus.OPEN
    assert position.quantity == pytest.approx(10.0)
    assert position.average_entry_price > 100.0  # paid the offer plus slippage
    assert position.cost_basis == pytest.approx(sum(fill.notional for fill in order.fills))


def test_a_second_buy_averages_the_cost_basis(db_session) -> None:
    account = make_account(db_session, email="avg@test.ai", funding=100_000.0)
    seed_bars(db_session, symbol="EXE/AVG", price=100.0)
    first = buy(db_session, account, symbol="EXE/AVG", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=first)
    db_session.commit()

    second = buy(db_session, account, symbol="EXE/AVG", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=second)
    db_session.commit()

    position = db_session.query(Position).filter(Position.trading_account_id == account.id).one()
    assert position.quantity == pytest.approx(20.0)
    assert position.cost_basis == pytest.approx(
        sum(fill.notional for fill in first.fills) + sum(fill.notional for fill in second.fills)
    )


def test_a_sell_closes_the_position_and_books_realised_pnl(db_session) -> None:
    account = make_account(db_session, email="close@test.ai", funding=100_000.0)
    instrument = seed_bars(db_session, symbol="EXE/CLOSE", price=100.0)
    order = buy(db_session, account, symbol="EXE/CLOSE", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()

    # Move the market up so the sale is profitable.
    db_session.query(MarketCandle).filter(MarketCandle.instrument_id == instrument.id).update(
        {MarketCandle.close: 120.0}
    )
    db_session.flush()

    sell = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="EXE/CLOSE", side=OrderSide.SELL, quantity=10),
    )
    db_session.commit()
    broker.execute_order(db_session, account=account, order=sell)
    db_session.commit()

    position = db_session.query(Position).filter(Position.trading_account_id == account.id).one()
    assert position.status == PositionStatus.CLOSED
    assert position.quantity == 0
    assert position.realised_pnl > 0
    # And the ledger agrees with the position.
    assert ledger_service.trial_balance(db_session, account) == 0


def test_selling_more_than_is_held_is_refused(db_session) -> None:
    account = make_account(db_session, email="oversell@test.ai")
    seed_bars(db_session, symbol="EXE/OVER", price=100.0)
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="EXE/OVER", side=OrderSide.SELL, quantity=5),
    )
    db_session.commit()
    assert order.status == OrderStatus.REJECTED
    assert "holds 0" in order.reject_reason or "holds 0.0" in order.reject_reason


def test_positions_reconcile_against_the_ledger(db_session) -> None:
    """Two records of the same money must agree, including after a close."""
    account = make_account(db_session, email="recon@test.ai", funding=100_000.0)
    seed_bars(db_session, symbol="EXE/RECON", price=100.0)
    order = buy(db_session, account, symbol="EXE/RECON", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()

    report = broker.reconcile_positions(db_session, account=account)
    assert report["investment_agrees"] is True
    assert report["realised_agrees"] is True

    sell = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="EXE/RECON", side=OrderSide.SELL, quantity=10),
    )
    db_session.commit()
    broker.execute_order(db_session, account=account, order=sell)
    db_session.commit()

    closed_report = broker.reconcile_positions(db_session, account=account)
    assert closed_report["open_positions"] == 0
    assert closed_report["closed_positions"] == 1
    # Realised P&L on a closed position must still reconcile — the bug this caught.
    assert closed_report["realised_agrees"] is True
    assert closed_report["investment_agrees"] is True


def test_marking_updates_the_unrealised_side_without_touching_the_ledger(db_session) -> None:
    account = make_account(db_session, email="mark@test.ai", funding=100_000.0)
    instrument = seed_bars(db_session, symbol="EXE/MARK", price=100.0)
    order = buy(db_session, account, symbol="EXE/MARK", quantity=10)
    db_session.commit()
    broker.execute_order(db_session, account=account, order=order)
    db_session.commit()
    cash_before = ledger_service.ledger_cash(db_session, account)

    db_session.query(MarketCandle).filter(MarketCandle.instrument_id == instrument.id).update(
        {MarketCandle.close: 150.0}
    )
    db_session.flush()
    broker.mark_positions(db_session, account=account)
    db_session.commit()

    position = db_session.query(Position).filter(Position.trading_account_id == account.id).one()
    assert position.last_mark_price == pytest.approx(150.0)
    # A mark is not a movement of money.
    assert ledger_service.ledger_cash(db_session, account) == cash_before


def test_expiring_stale_day_orders(db_session) -> None:
    account = make_account(db_session, email="expire@test.ai")
    seed_bars(db_session, symbol="EXE/EXP", price=100.0)
    order = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(
            symbol="EXE/EXP",
            side=OrderSide.BUY,
            quantity=5,
            order_type=OrderType.LIMIT,
            limit_price=10.0,
            time_in_force=TimeInForce.DAY,
        ),
    )
    db_session.commit()
    expired = order_service.expire_stale_orders(
        db_session, now=datetime.now(UTC) + timedelta(days=2)
    )
    db_session.commit()
    assert any(item.id == order.id for item in expired)
    assert order.status == OrderStatus.EXPIRED


# ------------------------------------------------------- strategy integration


def test_an_unvalidated_strategy_cannot_place_orders(db_session) -> None:
    """The validation gate would be decorative if a backtested rule could trade."""
    from app.models.strategy import Strategy, StrategyFamily, StrategyStatus
    from app.services.execution.strategy_bridge import submit_strategy_proposal

    account = make_account(db_session, email="bridge@test.ai")
    seed_bars(db_session, symbol="EXE/BRIDGE", price=100.0, bars=400)
    strategy = Strategy(
        code="trend_ma_v1",
        name="Trend",
        family=StrategyFamily.TREND,
        status=StrategyStatus.TESTING,
    )
    db_session.add(strategy)
    db_session.commit()

    outcome = submit_strategy_proposal(
        db_session, account=account, strategy_code="trend_ma_v1", symbol="EXE/BRIDGE"
    )
    assert outcome.created is False
    assert "not validated" in outcome.reason
    assert outcome.order is None


def test_a_validated_strategy_places_a_risk_sized_order(db_session) -> None:
    from app.models.strategy import Strategy, StrategyFamily, StrategyStatus
    from app.services.execution.strategy_bridge import submit_strategy_proposal

    account = make_account(db_session, email="bridge2@test.ai", funding=100_000.0)
    # A steadily rising series so the trend rule is unambiguously long.
    venue = db_session.query(Venue).first()
    instrument = Instrument(
        symbol="EXE/TREND",
        name="Trend",
        asset_class="forex",
        venue_id=venue.id if venue else None,
        base_currency="EXE",
        quote_currency="TRD",
        is_tradable=True,
        status="active",
    )
    if venue is None:
        venue = Venue(code="EXV2", name="Exec venue 2", asset_class="forex")
        db_session.add(venue)
        db_session.flush()
        instrument.venue_id = venue.id
    db_session.add(instrument)
    db_session.flush()
    newest = datetime.now(UTC)
    for index in range(400):
        price = 100.0 + index * 0.5
        # The newest bar must sit at "now" or the freshness check (correctly)
        # refuses the order.
        when = newest - timedelta(days=399 - index)
        db_session.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=when,
                close_time=when + timedelta(hours=23),
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    strategy = Strategy(
        code="trend_ma_v1",
        name="Trend",
        family=StrategyFamily.TREND,
        status=StrategyStatus.VALIDATED,
    )
    db_session.add(strategy)
    db_session.commit()

    outcome = submit_strategy_proposal(
        db_session, account=account, strategy_code="trend_ma_v1", symbol="EXE/TREND"
    )
    assert outcome.created is True, outcome.reason
    assert outcome.signal == "long"
    assert outcome.order is not None
    assert outcome.order.origin == "trend_ma_v1"
    # Risk-based sizing: a 2% stop on 1% of equity.
    assert outcome.quantity > 0


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
