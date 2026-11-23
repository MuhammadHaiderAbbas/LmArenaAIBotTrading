"""Feature engineering with explicit leakage discipline.

Every feature here uses information available **at or before** the close of the
bar it is attached to. Nothing peeks. The tests enforce that with a
prefix-invariance check: computing features on a series truncated at row *n* must
give exactly the same values for rows < *n* as computing on the full series. If a
feature secretly used future data, that equality breaks.

The label is the one place the future is allowed, and it is built separately:

    label(t) = sign(close(t + horizon) - close(t))

so the last `horizon` rows have no label and are dropped from training.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Feature order is part of the model contract: stored on the version so a later
# load cannot silently reinterpret column 4 as something else.
FEATURE_NAMES: tuple[str, ...] = (
    "return_1",
    "return_5",
    "return_20",
    "sma_gap_20",
    "sma_gap_50",
    "ema_gap_20",
    "rsi_14",
    "macd_hist",
    "atr_pct",
    "bb_position",
    "volatility_20",
    "volatility_ratio",
    "range_pct",
    "close_location",
    "volume_z",
)


@dataclass
class FeatureMatrix:
    names: tuple[str, ...]
    values: np.ndarray  # shape (rows, features)
    labels: np.ndarray  # shape (rows,), -1 until the label horizon is known
    timestamps: list[str]
    usable: np.ndarray  # boolean mask: label known and features complete

    @property
    def rows(self) -> int:
        return int(self.values.shape[0]) if self.values.ndim == 2 else 0


def _rolling_mean(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    if len(values) < period:
        return out
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    out[period - 1 :] = (cumulative[period:] - cumulative[:-period]) / period
    return out


def _rolling_std(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        out[index] = float(np.std(window, ddof=1)) if len(window) > 1 else np.nan
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.full(closes.shape, np.nan)
    if len(closes) <= period:
        return out
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for index in range(period, len(closes)):
        if index > period:
            avg_gain = (avg_gain * (period - 1) + gains[index - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[index - 1]) / period
        if avg_loss == 0:
            out[index] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[index] = 100.0 - 100.0 / (1.0 + rs)
    return out


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    out = np.full(values.shape, np.nan)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1.0)
    out[period - 1] = float(np.mean(values[:period]))
    for index in range(period, len(values)):
        out[index] = alpha * values[index] + (1 - alpha) * out[index - 1]
    return out


def _true_range_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    out = np.full(close.shape, np.nan)
    if len(close) < 2:
        return out
    for index in range(1, len(close)):
        previous = close[index - 1]
        tr = max(high[index] - low[index], abs(high[index] - previous), abs(low[index] - previous))
        out[index] = tr / previous * 100 if previous else np.nan
    return out


def build_matrix(
    *,
    opens: list[float],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
    timestamps: list[str],
    horizon: int = 1,
) -> FeatureMatrix:
    """Compute the feature matrix and forward-return labels.

    Rows with incomplete features (early rows) or unknown labels (the final
    `horizon` rows) are marked unusable rather than imputed. Imputing a moving
    average you could not yet compute invents information.
    """
    if not closes:
        return FeatureMatrix(FEATURE_NAMES, np.zeros((0, len(FEATURE_NAMES))), np.zeros(0), [], np.zeros(0, dtype=bool))
    if not (len(opens) == len(highs) == len(lows) == len(closes) == len(volumes) == len(timestamps)):
        raise ValueError("OHLCV and timestamp series must be the same length")

    close = np.asarray(closes, dtype=float)
    high = np.asarray(highs, dtype=float)
    low = np.asarray(lows, dtype=float)
    volume = np.asarray(volumes, dtype=float)
    rows = len(close)

    returns = np.full(rows, np.nan)
    returns[1:] = np.diff(close) / close[:-1]

    def shifted_return(period: int) -> np.ndarray:
        out = np.full(rows, np.nan)
        if rows > period:
            out[period:] = (close[period:] - close[:-period]) / close[:-period]
        return out

    sma20 = _rolling_mean(close, 20)
    sma50 = _rolling_mean(close, 50)
    ema20 = _ema(close, 20)
    rsi14 = _rsi(close, 14)
    macd_fast = _ema(close, 12)
    macd_slow = _ema(close, 26)
    macd_hist = (macd_fast - macd_slow) / np.where(close == 0, np.nan, close) * 100
    atr_pct = _true_range_pct(high, low, close)
    atr14 = _rolling_mean(np.nan_to_num(atr_pct, nan=0.0), 14)
    volatility = _rolling_std(np.nan_to_num(returns, nan=0.0), 20) * math.sqrt(252) * 100
    short_vol = _rolling_std(np.nan_to_num(returns, nan=0.0), 10)
    long_vol = _rolling_std(np.nan_to_num(returns, nan=0.0), 60)
    bb_std = _rolling_std(close, 20)
    bb_upper = sma20 + 2 * bb_std
    bb_lower = sma20 - 2 * bb_std
    bb_span = bb_upper - bb_lower

    volume_mean = _rolling_mean(volume, 20)
    volume_std = _rolling_std(volume, 20)

    features = np.full((rows, len(FEATURE_NAMES)), np.nan)
    for index in range(rows):
        price = close[index]
        span = bb_span[index] if not math.isnan(bb_span[index]) else 0.0
        bar_range = high[index] - low[index]

        features[index] = (
            returns[index],
            shifted_return(5)[index],
            shifted_return(20)[index],
            (price - sma20[index]) / sma20[index] if sma20[index] else np.nan,
            (price - sma50[index]) / sma50[index] if sma50[index] else np.nan,
            (price - ema20[index]) / ema20[index] if ema20[index] else np.nan,
            rsi14[index],
            macd_hist[index],
            atr14[index],
            ((price - bb_lower[index]) / span) if span else np.nan,
            volatility[index],
            (short_vol[index] / long_vol[index]) if long_vol[index] else np.nan,
            (bar_range / price * 100) if price else np.nan,
            ((price - low[index]) / bar_range) if bar_range else 0.5,
            # A constant-volume series carries no information, but it must not
            # produce NaN: some FX and crypto feeds report a flat volume column,
            # and one dead feature would make the instrument untrainable.
            # Zero here means "at the average", which is literally true.
            ((volume[index] - volume_mean[index]) / volume_std[index]) if volume_std[index] else 0.0,
        )

    labels = np.full(rows, -1, dtype=int)
    if rows > horizon:
        future = close[horizon:]
        current = close[:-horizon]
        labels[: rows - horizon] = np.where(future > current, 1, np.where(future < current, 0, 0))
        # A flat forward return is not a down move; it is dropped instead.
        flat = future == current
        labels[: rows - horizon][flat] = -1

    feature_ok = ~np.isnan(features).any(axis=1)
    label_ok = labels >= 0
    usable = feature_ok & label_ok

    return FeatureMatrix(FEATURE_NAMES, features, labels, list(timestamps), usable)


def chronological_split(usable_index: np.ndarray, *, train: float = 0.6, validation: float = 0.2) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split by time, never at random.

    A random split on time-series data leaks: rows on either side of the boundary
    share overlapping label windows, so a model can score well by memorising the
    neighbourhood of each test row. The split here is strictly forward in time.
    """
    if not 0 < train < 1 or not 0 <= validation < 1 or train + validation >= 1:
        raise ValueError("train and validation fractions must leave an out-of-sample slice")
    ordered = np.asarray(usable_index)
    total = len(ordered)
    if total < 30:
        raise ValueError(f"not enough usable rows to split ({total}); need at least 30")
    train_end = int(total * train)
    validation_end = train_end + int(total * validation)
    return ordered[:train_end], ordered[train_end:validation_end], ordered[validation_end:]


def describe(features: np.ndarray, names: tuple[str, ...]) -> list[dict[str, object]]:
    """Distribution summary per feature, for drift reference and audit."""
    summaries: list[dict[str, object]] = []
    for column, name in enumerate(names):
        values = features[:, column]
        values = values[~np.isnan(values)]
        if values.size == 0:
            summaries.append(
                {"feature": name, "count": 0, "mean": None, "std": None,
                 "minimum": None, "maximum": None, "deciles": []}
            )
            continue
        summaries.append(
            {
                "feature": name,
                "count": int(values.size),
                "mean": float(np.mean(values)),
                "std": float(np.std(values, ddof=1)) if values.size > 1 else 0.0,
                "minimum": float(np.min(values)),
                "maximum": float(np.max(values)),
                "deciles": [float(np.quantile(values, q)) for q in np.linspace(0.1, 0.9, 9)],
            }
        )
    return summaries
