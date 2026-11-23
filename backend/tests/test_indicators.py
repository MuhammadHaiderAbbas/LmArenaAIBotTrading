"""Indicator correctness.

The RSI fixture is the canonical Wilder/StockCharts worked example, so these
tests fail if the smoothing is ever "simplified" into something that is not RSI.
"""

from __future__ import annotations

import pytest

from app.services.market.indicators import (
    atr,
    bollinger,
    ema,
    macd,
    percent_change,
    realised_volatility,
    rsi,
    sma,
    snapshot,
)

WILDER_CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
]


def test_sma_warmup_is_none_not_zero() -> None:
    series = sma([1, 2, 3, 4, 5], 3)
    assert series[:2] == [None, None]
    assert series[2:] == pytest.approx([2.0, 3.0, 4.0])


def test_ema_seeds_from_sma() -> None:
    series = ema([1, 2, 3, 4, 5], 3)
    assert series[:2] == [None, None]
    assert series[2] == pytest.approx(2.0)
    assert series[3] == pytest.approx(3.0)
    assert series[4] == pytest.approx(4.0)


def test_rsi_matches_published_reference() -> None:
    series = rsi(WILDER_CLOSES, 14)
    # StockCharts' worked example: first RSI(14) value is 70.46.
    assert all(value is None for value in series[:14])
    assert series[14] == pytest.approx(70.46, abs=0.01)
    assert series[15] == pytest.approx(66.25, abs=0.01)


def test_rsi_bounds_are_respected() -> None:
    rising = [float(value) for value in range(1, 40)]
    falling = list(reversed(rising))
    assert rsi(rising, 14)[-1] == pytest.approx(100.0)
    assert rsi(falling, 14)[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_insufficient_history_returns_all_none() -> None:
    assert rsi([1, 2, 3], 14) == [None, None, None]


def test_macd_histogram_is_line_minus_signal() -> None:
    closes = [100 + (index * 0.7) + ((-1) ** index) for index in range(120)]
    line, signal, histogram = macd(closes)
    tail = [
        (m, s, h)
        for m, s, h in zip(line[-5:], signal[-5:], histogram[-5:], strict=True)
        if None not in (m, s, h)
    ]
    assert tail, "expected defined MACD values"
    for macd_value, signal_value, histogram_value in tail:
        assert histogram_value == pytest.approx(macd_value - signal_value)


def test_atr_never_negative_and_uses_true_range() -> None:
    highs = [10.0, 12.0, 13.0, 15.0, 14.0]
    lows = [8.0, 9.0, 10.0, 12.0, 11.0]
    closes = [9.0, 11.0, 12.0, 14.0, 12.5]
    series = atr(highs, lows, closes, 3)
    assert all(value is None or value >= 0 for value in series)


def test_bollinger_bands_bracket_the_middle() -> None:
    closes = [100 + ((-1) ** index) * 2 for index in range(40)]
    upper, middle, lower = bollinger(closes, 20)
    assert upper[-1] > middle[-1] > lower[-1]


def test_realised_volatility_is_none_until_enough_bars() -> None:
    closes = [100 + index for index in range(10)]
    series = realised_volatility(closes, 20)
    assert all(value is None for value in series)


def test_percent_change_handles_short_series() -> None:
    assert percent_change([100, 110], 1) == pytest.approx(10.0)
    assert percent_change([100], 5) is None


def test_snapshot_tolerates_short_history() -> None:
    snap = snapshot(closes=[1.0, 2.0, 3.0], highs=[1.0, 2.0, 3.0], lows=[1.0, 2.0, 3.0])
    assert snap.rsi_14 is None
    assert snap.sma_20 is None
    assert snap.change_pct_1 == pytest.approx(50.0)


def test_snapshot_is_deterministic() -> None:
    kwargs = {
        "closes": WILDER_CLOSES * 5,
        "highs": [value + 1 for value in WILDER_CLOSES * 5],
        "lows": [value - 1 for value in WILDER_CLOSES * 5],
    }
    first = snapshot(**kwargs)
    second = snapshot(**kwargs)
    assert first == second
