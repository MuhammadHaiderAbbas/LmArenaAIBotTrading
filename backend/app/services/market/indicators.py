"""Technical indicators, computed identically on the server and in the browser.

Rules that keep these honest (they matter more than the formulas):

* Pure functions over a list of closes/highs/lows — no I/O, no global state, so
  every value is reproducible from recorded bars.
* Warm-up periods return ``None`` rather than a fabricated number. A 14-period RSI
  has no value for bar 3, and showing one would be a lie.
* Streaming variants (``*_last``) compute from the tail only, so the frontend can
  recompute indicators for a 500-bar chart without shipping Python.
* Wilder smoothing is used for RSI and ATR (the original definition), not a
  simple moving average of gains.

The frontend mirrors these in TypeScript; `tests/test_indicators.py` pins the
expected values so the two implementations cannot silently diverge.
"""

from __future__ import annotations

from dataclasses import dataclass


def sma(values: list[float], period: int) -> list[float | None]:
    """Simple moving average. First ``period - 1`` entries are None."""
    if period <= 0:
        raise ValueError("period must be positive")
    out: list[float | None] = []
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        out.append(running / period if index >= period - 1 else None)
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    """Exponential moving average seeded with the first SMA (Wilder-style start)."""
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return [None] * len(values)

    seed = sum(values[:period]) / period
    multiplier = 2 / (period + 1)
    out: list[float | None] = [None] * (period - 1) + [seed]
    previous = seed
    for value in values[period:]:
        previous = (value - previous) * multiplier + previous
        out.append(previous)
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    """Relative Strength Index using Wilder smoothing."""
    if len(values) <= period:
        return [None] * len(values)

    gains = [0.0]
    losses = [0.0]
    for previous, current in zip(values, values[1:], strict=False):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[1 : period + 1]) / period
    avg_loss = sum(losses[1 : period + 1]) / period

    out: list[float | None] = [None] * period
    out.append(_rsi_value(avg_gain, avg_loss))

    for index in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
        out.append(_rsi_value(avg_gain, avg_loss))
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(
    values: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (macd_line, signal_line, histogram)."""
    fast_ema = ema(values, fast)
    slow_ema = ema(values, slow)
    macd_line: list[float | None] = [
        (f - s) if f is not None and s is not None else None for f, s in zip(fast_ema, slow_ema, strict=False)
    ]

    first_valid = next((i for i, value in enumerate(macd_line) if value is not None), None)
    if first_valid is None:
        return macd_line, [None] * len(values), [None] * len(values)

    dense = [value for value in macd_line[first_valid:] if value is not None]
    signal_dense = ema(dense, signal)
    signal_line: list[float | None] = [None] * first_valid + signal_dense
    histogram: list[float | None] = [
        (m - s) if m is not None and s is not None else None
        for m, s in zip(macd_line, signal_line, strict=False)
    ]
    return macd_line, signal_line, histogram


def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    out = [highs[0] - lows[0]] if highs else []
    for index in range(1, len(highs)):
        out.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> list[float | None]:
    """Average True Range with Wilder smoothing."""
    ranges = true_range(highs, lows, closes)
    if len(ranges) < period:
        return [None] * len(ranges)

    seed = sum(ranges[:period]) / period
    out: list[float | None] = [None] * (period - 1) + [seed]
    previous = seed
    for value in ranges[period:]:
        previous = (previous * (period - 1) + value) / period
        out.append(previous)
    return out


def bollinger(
    values: list[float], period: int = 20, deviations: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Return (upper, middle, lower). Uses population standard deviation."""
    middle = sma(values, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for index, mean in enumerate(middle):
        if mean is None:
            upper.append(None)
            lower.append(None)
            continue
        window = values[index - period + 1 : index + 1]
        variance = sum((value - mean) ** 2 for value in window) / period
        sd = variance**0.5
        upper.append(mean + deviations * sd)
        lower.append(mean - deviations * sd)
    return upper, middle, lower


def realised_volatility(values: list[float], period: int = 20) -> list[float | None]:
    """Annualised close-to-close volatility, in percent."""
    if len(values) < period + 1:
        return [None] * len(values)
    returns = [
        (current - previous) / previous
        for previous, current in zip(values, values[1:], strict=False)
        if previous
    ]
    out: list[float | None] = [None]
    for index in range(1, len(values)):
        if index < period:
            out.append(None)
            continue
        window = returns[index - period : index]
        mean = sum(window) / period
        variance = sum((value - mean) ** 2 for value in window) / (period - 1)
        out.append((variance**0.5) * (252**0.5) * 100)
    return out


def percent_change(values: list[float], bars: int) -> float | None:
    if len(values) <= bars or values[-bars - 1] == 0:
        return None
    return (values[-1] - values[-bars - 1]) / values[-bars - 1] * 100


@dataclass(slots=True)
class IndicatorSnapshot:
    """Latest indicator values for one instrument — the shape the UI consumes."""

    sma_20: float | None = None
    sma_50: float | None = None
    ema_20: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    atr_14: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    volatility_20: float | None = None
    change_pct_1: float | None = None
    change_pct_5: float | None = None
    change_pct_20: float | None = None


def snapshot(
    *,
    closes: list[float],
    highs: list[float],
    lows: list[float],
) -> IndicatorSnapshot:
    """Compute the latest value of every indicator, tolerating short histories."""
    if not closes:
        return IndicatorSnapshot()

    def last(series: list[float | None]) -> float | None:
        return series[-1] if series else None

    sma20_series = sma(closes, 20)
    sma50_series = sma(closes, 50)
    ema20_series = ema(closes, 20)
    rsi_series = rsi(closes, 14)
    macd_line, signal_line, histogram = macd(closes)
    atr_series = atr(highs, lows, closes, 14)
    bb_upper, bb_middle, bb_lower = bollinger(closes, 20)
    vol_series = realised_volatility(closes, 20)

    return IndicatorSnapshot(
        sma_20=round(last(sma20_series), 6) if last(sma20_series) is not None else None,
        sma_50=round(last(sma50_series), 6) if last(sma50_series) is not None else None,
        ema_20=round(last(ema20_series), 6) if last(ema20_series) is not None else None,
        rsi_14=round(last(rsi_series), 2) if last(rsi_series) is not None else None,
        macd=round(last(macd_line), 6) if last(macd_line) is not None else None,
        macd_signal=round(last(signal_line), 6) if last(signal_line) is not None else None,
        macd_histogram=round(last(histogram), 6) if last(histogram) is not None else None,
        atr_14=round(last(atr_series), 6) if last(atr_series) is not None else None,
        bb_upper=round(last(bb_upper), 6) if last(bb_upper) is not None else None,
        bb_middle=round(last(bb_middle), 6) if last(bb_middle) is not None else None,
        bb_lower=round(last(bb_lower), 6) if last(bb_lower) is not None else None,
        volatility_20=round(last(vol_series), 2) if last(vol_series) is not None else None,
        change_pct_1=round(percent_change(closes, 1), 2)
        if percent_change(closes, 1) is not None
        else None,
        change_pct_5=round(percent_change(closes, 5), 2)
        if percent_change(closes, 5) is not None
        else None,
        change_pct_20=round(percent_change(closes, 20), 2)
        if percent_change(closes, 20) is not None
        else None,
    )
