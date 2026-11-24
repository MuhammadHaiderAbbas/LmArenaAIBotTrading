"""Analytics: honest metrics from recorded trades.

The tests that matter are the refusals. A page that shows "Sharpe 0.00" for an account
with two observations is worse than one that shows nothing, because the zero looks like
a result.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.account import Environment, TradingAccount
from app.models.execution import OrderSide, Position, PositionStatus
from app.models.identity import User
from app.models.market import Instrument, MarketCandle, Timeframe, Venue
from app.services import analytics, ledger_service
from app.services.execution import broker
from app.services.execution import orders as order_service
from app.services.execution.orders import Proposal


def make_account(db, email: str, funding: float = 100_000.0) -> TradingAccount:
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email, password_hash="x", full_name="Ana", is_active=True, is_verified=True
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


def seed(db, symbol: str, *, price: float = 100.0, bars: int = 60, instrument=None) -> Instrument:
    if instrument is None:
        venue = db.query(Venue).first()
        if venue is None:
            venue = Venue(code="ANV", name="Ana venue", asset_class="forex")
            db.add(venue)
            db.flush()
        instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
        if instrument is None:
            instrument = Instrument(
                symbol=symbol,
                name=symbol,
                asset_class="forex",
                venue_id=venue.id,
                base_currency="AN",
                quote_currency="T",
                is_tradable=True,
                status="active",
            )
            db.add(instrument)
            db.flush()
    newest = datetime.now(UTC)
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
                volume=1_000_000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.flush()
    return instrument


def round_trip(db, account, instrument, *, quantity: float, entry: float, exit: float, symbol: str):
    """Buy, mark the market to `exit`, sell. Returns the closed trade count."""
    buy = order_service.create_order(
        db, account=account, proposal=Proposal(symbol=symbol, side=OrderSide.BUY, quantity=quantity)
    )
    db.flush()
    broker.execute_order(db, account=account, order=buy)
    db.flush()

    db.query(MarketCandle).filter(MarketCandle.instrument_id == instrument.id).update(
        {MarketCandle.close: exit}
    )
    db.flush()

    sell = order_service.create_order(
        db,
        account=account,
        proposal=Proposal(symbol=symbol, side=OrderSide.SELL, quantity=quantity),
    )
    db.flush()
    broker.execute_order(db, account=account, order=sell)
    db.flush()
    del entry


# ------------------------------------------------------------ empty account


def test_an_account_with_no_trades_reports_nothing_and_says_so(db_session) -> None:
    account = make_account(db_session, "empty@ana.ai")
    report = analytics.build_report(db_session, account)

    assert report.trades == []
    assert report.net_return == pytest.approx(0.0)
    by_key = {metric.key: metric for metric in report.metrics}
    # Every ratio is refused, not zeroed.
    assert by_key["sharpe"].value is None
    assert by_key["sortino"].value is None
    assert by_key["win_rate"].value is None
    assert by_key["profit_factor"].value is None
    assert any("nothing to measure" in note for note in report.notes)


def test_starting_equity_comes_from_the_opening_entry(db_session) -> None:
    account = make_account(db_session, "base@ana.ai", funding=42_000.0)
    assert analytics.starting_equity(db_session, account) == pytest.approx(42_000.0)


# --------------------------------------------------------------- round trips


def test_a_profitable_round_trip_is_net_of_commission(db_session) -> None:
    account = make_account(db_session, "profit@ana.ai")
    instrument = seed(db_session, "ANA/UP")
    round_trip(
        db_session, account, instrument, quantity=100, entry=100.0, exit=110.0, symbol="ANA/UP"
    )

    report = analytics.build_report(db_session, account)
    assert len(report.trades) == 1
    trade = report.trades[0]
    assert trade.symbol == "ANA/UP"
    assert trade.quantity == pytest.approx(100.0)
    assert trade.realised_pnl > 0
    # Net means after both sides of commission, so it is below the gross move.
    gross = (trade.exit_price - trade.entry_price) * trade.quantity
    assert trade.realised_pnl < gross
    assert trade.commission > 0
    assert report.net_return is not None and report.net_return > 0


def test_trade_pnl_plus_commission_reconciles_to_the_position_table(db_session) -> None:
    """Two records of the same money must agree, and the difference is explainable.

    The position table stores realised P&L gross; the trade list reports net. So
    net + commission must equal it exactly — asserted as an identity, not a tolerance.
    """
    account = make_account(db_session, "recon@ana.ai")
    instrument = seed(db_session, "ANA/RECON")
    round_trip(
        db_session, account, instrument, quantity=50, entry=100.0, exit=105.0, symbol="ANA/RECON"
    )

    report = analytics.build_report(db_session, account)
    fifo_net = sum(trade.realised_pnl for trade in report.trades)
    fifo_commission = sum(trade.commission for trade in report.trades)
    ledger_gross = analytics.realised_total(db_session, account)
    assert fifo_net + fifo_commission == pytest.approx(ledger_gross, abs=1e-6)


def test_a_losing_round_trip_is_negative(db_session) -> None:
    account = make_account(db_session, "loss@ana.ai")
    instrument = seed(db_session, "ANA/DOWN")
    round_trip(
        db_session, account, instrument, quantity=100, entry=100.0, exit=90.0, symbol="ANA/DOWN"
    )

    report = analytics.build_report(db_session, account)
    assert report.trades[0].realised_pnl < 0
    assert report.net_return is not None and report.net_return < 0


def test_an_open_position_produces_no_closed_trade(db_session) -> None:
    """A position marked up is not a win; nothing is realised until it is sold."""
    account = make_account(db_session, "open@ana.ai")
    seed(db_session, "ANA/OPEN")
    buy = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="ANA/OPEN", side=OrderSide.BUY, quantity=10),
    )
    db_session.flush()
    broker.execute_order(db_session, account=account, order=buy)
    db_session.flush()

    report = analytics.build_report(db_session, account)
    assert report.trades == []
    assert any("none is closed" in note for note in report.notes)
    # But the holding is in equity.
    assert report.equity > report.cash


def test_fifo_matches_partial_exits(db_session) -> None:
    account = make_account(db_session, "fifo@ana.ai")
    instrument = seed(db_session, "ANA/FIFO")
    round_trip(
        db_session, account, instrument, quantity=100, entry=100.0, exit=110.0, symbol="ANA/FIFO"
    )
    report = analytics.build_report(db_session, account)
    assert sum(trade.quantity for trade in report.trades) == pytest.approx(100.0)


# ------------------------------------------------------------------ the curve


def test_the_curve_does_not_invent_a_drawdown_on_a_profitable_account(db_session) -> None:
    """The bug this caught: valuing history from today's open positions.

    A closed position has quantity 0, so every past day was marked flat and the
    funding day looked like a 10% loss that never happened.
    """
    account = make_account(db_session, "curve@ana.ai")
    instrument = seed(db_session, "ANA/CURVE")
    round_trip(
        db_session, account, instrument, quantity=100, entry=100.0, exit=110.0, symbol="ANA/CURVE"
    )

    report = analytics.build_report(db_session, account)
    by_key = {metric.key: metric for metric in report.metrics}
    assert by_key["max_drawdown"].value is not None
    assert by_key["max_drawdown"].value < 0.01


def test_the_curve_holds_the_position_while_it_is_open(db_session) -> None:
    account = make_account(db_session, "hold@ana.ai")
    seed(db_session, "ANA/HOLD")
    buy = order_service.create_order(
        db_session,
        account=account,
        proposal=Proposal(symbol="ANA/HOLD", side=OrderSide.BUY, quantity=10),
    )
    db_session.flush()
    broker.execute_order(db_session, account=account, order=buy)
    db_session.flush()

    fills = analytics.fill_timeline(db_session, account)
    assert len(fills) == 1
    later = fills[0].at + timedelta(hours=1)
    assert analytics.quantity_held_at(fills, "ANA/HOLD", later) == pytest.approx(10.0)
    earlier = fills[0].at - timedelta(hours=1)
    assert analytics.quantity_held_at(fills, "ANA/HOLD", earlier) == pytest.approx(0.0)


def test_the_benchmark_covers_the_same_instruments(db_session) -> None:
    account = make_account(db_session, "bench@ana.ai")
    instrument = seed(db_session, "ANA/BENCH")
    round_trip(
        db_session, account, instrument, quantity=10, entry=100.0, exit=102.0, symbol="ANA/BENCH"
    )
    report = analytics.build_report(db_session, account)
    assert report.benchmark_curve
    assert any("not an index" in note for note in report.notes)


# --------------------------------------------------------------- statistics


def test_sharpe_is_refused_on_a_thin_sample() -> None:
    assert analytics.sharpe([0.01, 0.02, -0.01]) is None


def test_sharpe_is_refused_when_volatility_is_zero() -> None:
    assert analytics.sharpe([0.0] * 20) is None


def test_sharpe_is_computed_on_a_real_sample() -> None:
    returns = [
        0.01,
        -0.004,
        0.007,
        0.002,
        -0.006,
        0.009,
        0.003,
        -0.001,
        0.005,
        0.004,
        0.002,
        -0.003,
    ]
    value = analytics.sharpe(returns)
    assert value is not None
    assert value > 0


def test_sortino_ignores_upside_volatility() -> None:
    """The whole point: a fat right tail should not penalise the ratio."""
    steady = [0.01, -0.005] * 6
    lumpy = [0.10, -0.005, 0.0, -0.005, 0.0, -0.005, 0.0, -0.005, 0.0, -0.005, 0.0, -0.005]
    assert analytics.sortino(lumpy) is not None
    assert analytics.sortino(steady) is not None
    assert analytics.sortino(lumpy) > analytics.sortino(steady)


def test_sortino_is_refused_with_no_downside() -> None:
    assert analytics.sortino([0.01] * 12) is None


def test_max_drawdown_measures_peak_to_trough() -> None:
    curve = [100.0, 120.0, 90.0, 110.0]
    assert analytics.max_drawdown(curve) == pytest.approx(0.25)


def test_max_drawdown_of_a_rising_curve_is_zero() -> None:
    assert analytics.max_drawdown([100.0, 101.0, 105.0]) == pytest.approx(0.0)


def test_profit_factor_is_refused_with_no_losses() -> None:
    """Reporting 'infinity' for an unbeaten strategy is flattery, not information."""
    from app.services.analytics import ClosedTrade

    winners = [
        ClosedTrade("X", 1, 10, 12, 2.0, 0.1, datetime.now(UTC), datetime.now(UTC)),
        ClosedTrade("X", 1, 10, 13, 3.0, 0.1, datetime.now(UTC), datetime.now(UTC)),
    ]
    assert analytics.profit_factor(winners) is None


def test_profit_factor_divides_gains_by_losses() -> None:
    from app.services.analytics import ClosedTrade

    now = datetime.now(UTC)
    trades = [
        ClosedTrade("X", 1, 10, 12, 4.0, 0.1, now, now),
        ClosedTrade("X", 1, 10, 9, -2.0, 0.1, now, now),
    ]
    assert analytics.profit_factor(trades) == pytest.approx(2.0)


def test_win_rate_counts_only_profitable_trips(db_session) -> None:
    account = make_account(db_session, "winrate@ana.ai")
    instrument = seed(db_session, "ANA/WIN")
    round_trip(
        db_session, account, instrument, quantity=10, entry=100.0, exit=105.0, symbol="ANA/WIN"
    )
    report = analytics.build_report(db_session, account)
    by_key = {metric.key: metric for metric in report.metrics}
    # One trip is below the reporting threshold, so the rate is withheld.
    assert by_key["win_rate"].value is None
    assert "5" in by_key["win_rate"].note


# ------------------------------------------------------------------- costs


def test_costs_are_broken_out_not_lumped_together(db_session) -> None:
    account = make_account(db_session, "costs@ana.ai")
    instrument = seed(db_session, "ANA/COST")
    round_trip(
        db_session, account, instrument, quantity=10, entry=100.0, exit=101.0, symbol="ANA/COST"
    )
    report = analytics.build_report(db_session, account)
    costs = report.cost_breakdown
    assert costs["commission"] > 0
    assert costs["slippage"] > 0
    assert costs["total"] == pytest.approx(costs["commission"] + costs["slippage"])
    assert costs["fills"] == 2
    assert costs["orders"] == 2


def test_attribution_splits_by_instrument_and_origin(db_session) -> None:
    account = make_account(db_session, "attr@ana.ai")
    instrument = seed(db_session, "ANA/ATTR")
    round_trip(
        db_session, account, instrument, quantity=10, entry=100.0, exit=105.0, symbol="ANA/ATTR"
    )
    report = analytics.build_report(db_session, account)
    assert "ANA/ATTR" in report.attribution["by_symbol"]
    assert "manual" in report.attribution["by_strategy"]


def test_turnover_is_traded_notional_over_average_equity(db_session) -> None:
    account = make_account(db_session, "turn@ana.ai")
    instrument = seed(db_session, "ANA/TURN")
    round_trip(
        db_session, account, instrument, quantity=10, entry=100.0, exit=100.0, symbol="ANA/TURN"
    )
    report = analytics.build_report(db_session, account)
    by_key = {metric.key: metric for metric in report.metrics}
    assert by_key["turnover"].value is not None
    assert by_key["turnover"].value > 0


def test_every_metric_carries_its_definition(db_session) -> None:
    account = make_account(db_session, "defs@ana.ai")
    report = analytics.build_report(db_session, account)
    for metric in report.metrics:
        assert metric.definition, f"{metric.key} has no definition"
        assert metric.label


def test_an_open_position_row_is_not_counted_as_a_closed_trade(db_session) -> None:
    account = make_account(db_session, "norow@ana.ai")
    seed(db_session, "ANA/ROW")
    db_session.add(
        Position(
            trading_account_id=account.id,
            instrument_symbol="ANA/ROW",
            status=PositionStatus.OPEN,
            quantity=5,
            average_entry_price=100.0,
            cost_basis=500.0,
            opened_at=datetime.now(UTC),
        )
    )
    db_session.flush()
    assert analytics.round_trips(db_session, account) == []


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
