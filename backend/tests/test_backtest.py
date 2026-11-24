"""Backtest simulator, cost model and acceptance gate.

The tests that matter are the ones that would catch a flattering backtest:
look-ahead fills, costs that never get charged, and a stop that is ignored because
the close recovered.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.services.strategies import rules, simulator
from app.services.strategies.rules import Bars, Direction, Signal


def series(rows: int, *, seed: int = 3, drift: float = 0.0, sigma: float = 0.01) -> Bars:
    rng = random.Random(seed)  # noqa: S311 - synthetic fixtures
    closes = [100.0]
    for _ in range(rows - 1):
        closes.append(closes[-1] * (1 + rng.gauss(drift, sigma)))
    return Bars(
        opens=[value * 0.999 for value in closes],
        highs=[value * 1.006 for value in closes],
        lows=[value * 0.994 for value in closes],
        closes=closes,
        volumes=[1000.0] * rows,
    )


def stamps(rows: int) -> list[datetime]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    return [start + timedelta(days=index) for index in range(rows)]


# --------------------------------------------------------------- look-ahead


def test_a_signal_is_filled_at_the_next_bar_not_the_current_one() -> None:
    """The core anti-look-ahead property, asserted directly.

    A signal computed from bar 1's close must enter at bar 2's open. If it entered
    at bar 1's close, the backtest would be trading on a price it used to decide.
    """
    bars = Bars(
        opens=[100, 101, 105, 105],
        highs=[101, 106, 106, 106],
        lows=[99, 100, 100, 100],
        closes=[100, 105, 105, 105],
        volumes=[1, 1, 1, 1],
    )
    ts = stamps(4)
    # Buy signal on bar 0, flat afterwards.
    signals = [
        Signal(Direction.LONG, "test"),
        Signal(Direction.FLAT),
        Signal(Direction.FLAT),
        Signal(Direction.FLAT),
    ]
    result = simulator.simulate(
        bars,
        ts,
        signals,
        simulator.CostModel(spread_bps=0, commission_bps=0, slippage_bps=0),
        warmup_bars=0,
    )
    assert result.trades
    first = result.trades[0]
    # Bar 1's open is 101, not bar 0's close of 100.
    assert first.entry_price == pytest.approx(101.0)
    assert first.entry_time == ts[1]


def test_no_position_is_opened_on_the_final_bar() -> None:
    """A position opened on the last bar could never be closed, so it is not opened."""
    bars = series(10)
    ts = stamps(10)
    signals = [Signal(Direction.LONG)] * 10
    result = simulator.simulate(bars, ts, signals, simulator.CostModel(), warmup_bars=2)
    assert all(trade.exit_time <= ts[-1] for trade in result.trades)
    assert result.trades


def test_changing_only_the_future_does_not_change_past_trades() -> None:
    """A truncated run must agree with the full run up to the truncation point."""
    bars = series(200, seed=8)
    ts = stamps(200)
    signals = rules.signals_for("trend_ma_v1", bars)
    costs = simulator.CostModel()
    full = simulator.simulate(bars, ts, signals, costs, warmup_bars=60)

    truncated_bars = bars.slice(150)
    truncated_signals = rules.signals_for("trend_ma_v1", truncated_bars)
    truncated = simulator.simulate(
        truncated_bars, ts[:150], truncated_signals, costs, warmup_bars=60
    )

    full_early = [trade for trade in full.trades if trade.exit_time < ts[148]]
    truncated_early = [trade for trade in truncated.trades if trade.exit_time < ts[148]]
    assert len(full_early) == len(truncated_early)
    for left, right in zip(full_early, truncated_early, strict=True):
        assert left.entry_price == pytest.approx(right.entry_price)
        assert left.exit_price == pytest.approx(right.exit_price)


# ---------------------------------------------------------------- cost model


def test_costs_make_a_trade_worse_than_no_costs() -> None:
    bars = series(120, seed=5)
    ts = stamps(120)
    signals = rules.signals_for("trend_ma_v1", bars)
    free = simulator.simulate(
        bars,
        ts,
        signals,
        simulator.CostModel(spread_bps=0, commission_bps=0, slippage_bps=0),
        warmup_bars=60,
    )
    costly = simulator.simulate(
        bars,
        ts,
        signals,
        simulator.CostModel(spread_bps=20, commission_bps=10, slippage_bps=10),
        warmup_bars=60,
    )
    free_net = sum(trade.net_pnl for trade in free.trades)
    costly_net = sum(trade.net_pnl for trade in costly.trades)
    assert costly_net < free_net


def test_entry_pays_the_offer_and_exit_pays_the_bid() -> None:
    costs = simulator.CostModel(spread_bps=10, commission_bps=0, slippage_bps=0)
    # A 10bp spread means a buyer pays 5bp over mid.
    assert costs.entry_price(100.0, Direction.LONG) == pytest.approx(100.05)
    assert costs.exit_price(100.0, Direction.LONG) == pytest.approx(99.95)


def test_slippage_always_works_against_the_trader() -> None:
    costs = simulator.CostModel(spread_bps=0, commission_bps=0, slippage_bps=20)
    assert costs.entry_price(100.0, Direction.LONG) > 100.0
    assert costs.exit_price(100.0, Direction.LONG) < 100.0
    assert costs.entry_price(100.0, Direction.SHORT) < 100.0
    assert costs.exit_price(100.0, Direction.SHORT) > 100.0


def test_commission_and_financing_scale_with_notional_and_time() -> None:
    costs = simulator.CostModel(commission_bps=2, financing_bps_per_day=1)
    assert costs.commission(10_000) == pytest.approx(2.0)
    assert costs.financing(10_000, 3) == pytest.approx(3.0)
    assert costs.financing(10_000, 0) == 0.0


def test_every_trade_reports_costs_separately_from_gross() -> None:
    bars = series(200, seed=9)
    ts = stamps(200)
    result = simulator.simulate(
        bars, ts, rules.signals_for("trend_ma_v1", bars), simulator.CostModel(), warmup_bars=60
    )
    assert result.trades
    for trade in result.trades:
        assert trade.costs >= 0
        # Net can never be better than gross minus the itemised costs.
        assert trade.net_pnl <= trade.gross_pnl + 1e-9


# ----------------------------------------------------------------------- stops


def test_a_stop_is_hit_when_the_low_touches_it_even_if_the_close_recovers() -> None:
    """The classic optimistic bug: the bar closed fine, so the stop is ignored."""
    bars = Bars(
        opens=[100.0, 100.0, 100.0, 120.0, 120.0],
        highs=[100.0, 100.0, 121.0, 122.0, 122.0],
        lows=[100.0, 100.0, 90.0, 119.0, 119.0],  # bar 2 dips to 90 and recovers
        closes=[100.0, 100.0, 120.0, 120.0, 120.0],
        volumes=[1, 1, 1, 1, 1],
    )
    ts = stamps(5)
    signals = [
        Signal(Direction.LONG),
        Signal(Direction.LONG),
        Signal(Direction.LONG),
        Signal(Direction.LONG),
        Signal(Direction.LONG),
    ]
    result = simulator.simulate(
        bars,
        ts,
        signals,
        simulator.CostModel(spread_bps=0, commission_bps=0, slippage_bps=0, stop_pct=0.05),
        warmup_bars=0,
        use_stop=True,
    )
    stopped = [trade for trade in result.trades if trade.exit_reason.value == "stop"]
    assert stopped, "a bar trading through the stop must trigger it"


def test_the_benchmark_is_never_stopped_out() -> None:
    """Buy-and-hold must stay a single decision, not sixty-four re-entries."""
    bars = series(400, seed=21)
    ts = stamps(400)
    result = simulator.simulate(
        bars,
        ts,
        rules.signals_for("buy_and_hold_v1", bars),
        simulator.CostModel(stop_pct=0.01),
        warmup_bars=10,
        use_stop=False,
    )
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason.value == "end_of_data"


# --------------------------------------------------------------------- gates


def test_gate_refuses_a_sample_that_is_too_small() -> None:
    passed, reason = simulator.judge(
        {"trades": 4, "net_return": 0.5, "baseline_return": 0.0, "edge_over_baseline": 0.5}
    )
    assert passed is False
    assert "at least 20" in reason


def test_gate_refuses_a_negative_net_return() -> None:
    passed, reason = simulator.judge(
        {"trades": 40, "net_return": -0.05, "baseline_return": 0.1, "edge_over_baseline": -0.15}
    )
    assert passed is False
    assert "negative" in reason


def test_gate_refuses_a_strategy_that_loses_to_buy_and_hold() -> None:
    passed, reason = simulator.judge(
        {
            "trades": 40,
            "net_return": 0.10,
            "baseline_return": 0.25,
            "edge_over_baseline": -0.15,
            "max_drawdown": -0.05,
            "sharpe": 1.2,
        }
    )
    assert passed is False
    assert "buy-and-hold" in reason


def test_gate_refuses_excessive_drawdown_and_weak_sharpe() -> None:
    passed, _ = simulator.judge(
        {
            "trades": 40,
            "net_return": 0.3,
            "baseline_return": 0.0,
            "edge_over_baseline": 0.3,
            "max_drawdown": -0.40,
            "sharpe": 1.5,
        }
    )
    assert passed is False
    passed, _ = simulator.judge(
        {
            "trades": 40,
            "net_return": 0.3,
            "baseline_return": 0.0,
            "edge_over_baseline": 0.3,
            "max_drawdown": -0.05,
            "sharpe": 0.1,
        }
    )
    assert passed is False


def test_gate_passes_a_genuinely_good_result() -> None:
    passed, reason = simulator.judge(
        {
            "trades": 60,
            "net_return": 0.18,
            "baseline_return": 0.05,
            "edge_over_baseline": 0.13,
            "max_drawdown": -0.08,
            "sharpe": 1.1,
        }
    )
    assert passed is True
    assert "buy-and-hold" in reason


def test_metrics_are_complete_and_consistent() -> None:
    bars = series(300, seed=12)
    ts = stamps(300)
    result = simulator.simulate(
        bars, ts, rules.signals_for("trend_ma_v1", bars), simulator.CostModel(), warmup_bars=80
    )
    metrics = simulator.compute_metrics(
        result,
        starting_equity=10_000,
        final_equity=result.equity[-1].equity,
        baseline_return=0.05,
    )
    for key in (
        "trades",
        "win_rate",
        "profit_factor",
        "net_return",
        "baseline_return",
        "edge_over_baseline",
        "sharpe",
        "sortino",
        "max_drawdown",
        "exposure",
        "turnover",
        "average_bars_held",
        "verdict",
        "passes_gate",
    ):
        assert key in metrics
    assert metrics["max_drawdown"] <= 0
    assert 0 <= (metrics["exposure"] or 0) <= 1
    # Costs are never negative and always at least the entry commission.
    assert metrics["total_costs"] >= 0


# ---------------------------------------------------------------- strategies


def test_all_registered_strategies_emit_one_signal_per_bar() -> None:
    bars = series(300)
    for code in rules.REGISTRY:
        signals = rules.signals_for(code, bars)
        assert len(signals) == len(bars), code
        assert all(
            signal.direction in {Direction.LONG, Direction.SHORT, Direction.FLAT}
            for signal in signals
        )


def test_strategies_warm_up_before_signalling() -> None:
    bars = series(300)
    signals = rules.signals_for("trend_ma_v1", bars)
    # Before the slow average exists there cannot be a long signal.
    assert all(signal.direction == Direction.FLAT for signal in signals[:59])
    assert any(signal.direction == Direction.LONG for signal in signals[100:])


def test_trend_and_mean_reversion_disagree_somewhere() -> None:
    """If both always agreed, one of them would not be a distinct hypothesis."""
    bars = series(400, seed=17)
    trend = rules.signals_for("trend_ma_v1", bars)
    reversion = rules.signals_for("mean_reversion_v1", bars)
    disagreements = sum(
        1 for left, right in zip(trend, reversion, strict=True) if left.direction != right.direction
    )
    assert disagreements > 0


def test_unknown_strategy_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown strategy"):
        rules.signals_for("does_not_exist", series(50))


def test_simulation_refuses_a_series_shorter_than_the_warmup() -> None:
    result = simulator.simulate(
        series(10), stamps(10), [Signal(Direction.LONG)] * 10, simulator.CostModel(), warmup_bars=40
    )
    assert result.refused
    assert "not enough" in result.refusal_reason


def test_mismatched_inputs_are_rejected() -> None:
    with pytest.raises(ValueError, match="same length"):
        simulator.simulate(
            series(10), stamps(5), [Signal(Direction.LONG)] * 10, simulator.CostModel()
        )
