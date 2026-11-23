"""Strategy definitions.

Every strategy is a pure function of the bar history up to index *i*, returning the
position it wants held **for the bar that opens next**. That single convention is
what prevents look-ahead bias: a signal computed from bar *i*'s close cannot be
filled at bar *i*'s close, because that trade was already in the past by the time
the number existed.

There are three, deliberately few:

- `buy_and_hold_v1` — the benchmark. Every strategy is measured against it, because
  a trend follower on an instrument that went up is not evidence of skill.
- `trend_ma_v1` — the classic. It is included precisely because it is well known to
  be marginal, which makes it a useful test of the cost model.
- `mean_reversion_v1` — fights the trend, so a single instrument will rarely favour
  both. Running both is a check that the harness is not simply reporting "up".

No strategy here is enabled for live use, and none can enable itself.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Direction(enum.StrEnum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


@dataclass(frozen=True)
class Signal:
    """What a strategy wants to hold going into the next bar."""

    direction: Direction
    reason: str = ""
    stop_pct: float | None = None
    target_pct: float | None = None


@dataclass(frozen=True)
class Bars:
    """Column-oriented price history. Index i is one complete bar."""

    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.closes)

    def slice(self, end: int) -> Bars:
        return Bars(
            opens=self.opens[:end],
            highs=self.highs[:end],
            lows=self.lows[:end],
            closes=self.closes[:end],
            volumes=self.volumes[:end],
        )


@dataclass(frozen=True)
class StrategySpec:
    code: str
    name: str
    family: str
    description: str
    hypothesis: str
    parameters: dict[str, float | int]
    min_history_bars: int
    evaluate: object  # Callable[[Bars], list[Signal]]
    #: A benchmark must not be stopped out and re-entered: that turns one decision
    #: into sixty-four and stops it being a benchmark. Active strategies use stops
    #: because that is how they would actually be traded.
    use_stop: bool = True


def _sma(values: list[float], period: int, index: int) -> float | None:
    if index + 1 < period:
        return None
    window = values[index + 1 - period : index + 1]
    return sum(window) / period


def _std(values: list[float], period: int, index: int) -> float | None:
    if index + 1 < period:
        return None
    window = values[index + 1 - period : index + 1]
    mean = sum(window) / period
    return (sum((value - mean) ** 2 for value in window) / period) ** 0.5


def buy_and_hold(bars: Bars) -> list[Signal]:
    """Always long. The benchmark every other strategy must beat after costs.

    Costs are charged once, on entry, because the position is never closed. That
    makes it a genuinely hard bar to clear: buy-and-hold pays the least possible
    in costs while a strategy pays them every time it changes its mind.
    """
    return [Signal(Direction.LONG, "benchmark: always long") for _ in bars.closes]


def trend_following(bars: Bars, *, fast: int = 20, slow: int = 60) -> list[Signal]:
    """Long when the fast average is above the slow one, flat otherwise.

    Long/flat rather than long/short, because a short in a rising market with a
    stop is a different risk proposition and this strategy has no mandate to sell
    an instrument it does not hold in a paper account.
    """
    signals: list[Signal] = []
    for index in range(len(bars.closes)):
        fast_value = _sma(bars.closes, fast, index)
        slow_value = _sma(bars.closes, slow, index)
        if fast_value is None or slow_value is None:
            signals.append(Signal(Direction.FLAT, "warmup"))
            continue
        if fast_value > slow_value:
            signals.append(
                Signal(Direction.LONG, f"SMA{fast} {fast_value:.4f} above SMA{slow} {slow_value:.4f}")
            )
        else:
            signals.append(
                Signal(Direction.FLAT, f"SMA{fast} {fast_value:.4f} below SMA{slow} {slow_value:.4f}")
            )
    return signals


def mean_reversion(bars: Bars, *, period: int = 20, entry_z: float = 1.5, exit_z: float = 0.3) -> list[Signal]:
    """Long when price is `entry_z` standard deviations *below* its mean.

    The mirror image of trend following: it should lose in a strong trend and win
    in a range. Running both across instruments is the cheapest way to notice a
    harness that only ever reports the direction the data happened to move.
    """
    signals: list[Signal] = []
    for index in range(len(bars.closes)):
        mean = _sma(bars.closes, period, index)
        deviation = _std(bars.closes, period, index)
        if mean is None or deviation is None or deviation == 0:
            signals.append(Signal(Direction.FLAT, "warmup"))
            continue
        z = (bars.closes[index] - mean) / deviation
        if z <= -entry_z:
            signals.append(Signal(Direction.LONG, f"z-score {z:.2f} at or below -{entry_z}"))
        elif z >= -exit_z:
            signals.append(Signal(Direction.FLAT, f"z-score {z:.2f} reverted past -{exit_z}"))
        else:
            signals.append(Signal(Direction.FLAT, f"z-score {z:.2f} holding flat"))
    return signals


REGISTRY: dict[str, StrategySpec] = {
    "buy_and_hold_v1": StrategySpec(
        code="buy_and_hold_v1",
        name="Buy and hold benchmark",
        family="buy_and_hold",
        description="Buy at the first tradable bar, hold to the end. Pays costs once.",
        hypothesis=(
            "Not a strategy — the bar to clear. Any active strategy must beat this after "
            "paying spread, commission and slippage on every change of position."
        ),
        parameters={},
        min_history_bars=5,
        evaluate=buy_and_hold,
        use_stop=False,
    ),
    "trend_ma_v1": StrategySpec(
        code="trend_ma_v1",
        name="Trend following (SMA cross)",
        family="trend",
        description="Long while the 20-bar SMA is above the 60-bar SMA; flat otherwise.",
        hypothesis=(
            "Persistent moves continue long enough that a slow moving-average cross "
            "captures more of an advance than it gives back in whipsaws, after costs."
        ),
        parameters={"fast": 20, "slow": 60},
        min_history_bars=200,
        evaluate=trend_following,
    ),
    "mean_reversion_v1": StrategySpec(
        code="mean_reversion_v1",
        name="Mean reversion (z-score)",
        family="mean_reversion",
        description="Long when price is 1.5σ below its 20-bar mean; exit as it reverts.",
        hypothesis=(
            "Short-horizon overreaction reverts far enough, often enough, to pay for the "
            "round trip. It should lose in strong trends and that is expected."
        ),
        parameters={"period": 20, "entry_z": 1.5, "exit_z": 0.3},
        min_history_bars=200,
        evaluate=mean_reversion,
    ),
}


def seed_strategies(db) -> dict[str, int]:
    from sqlalchemy import select

    from app.models.strategy import Strategy, StrategyFamily, StrategyStatus

    created = 0
    updated = 0
    for spec in REGISTRY.values():
        existing = db.scalar(select(Strategy).where(Strategy.code == spec.code))
        if existing is None:
            db.add(
                Strategy(
                    code=spec.code,
                    name=spec.name,
                    family=StrategyFamily(spec.family),
                    status=StrategyStatus.DRAFT,
                    description=spec.description,
                    hypothesis=spec.hypothesis,
                    parameters=str(spec.parameters).replace("'", '"'),
                    min_history_bars=spec.min_history_bars,
                    live_eligible=False,
                )
            )
            created += 1
        else:
            existing.name = spec.name
            existing.description = spec.description
            existing.hypothesis = spec.hypothesis
            existing.parameters = str(spec.parameters).replace("'", '"')
            existing.min_history_bars = spec.min_history_bars
            updated += 1
    db.flush()
    return {"created": created, "updated": updated, "total": created + updated}


def signals_for(code: str, bars: Bars, parameters: dict | None = None) -> list[Signal]:
    spec = REGISTRY.get(code)
    if spec is None:
        raise ValueError(f"Unknown strategy '{code}'")
    evaluate = spec.evaluate
    merge = {**spec.parameters, **(parameters or {})}
    try:
        return evaluate(bars, **merge)  # type: ignore[operator]
    except TypeError:
        return evaluate(bars)  # type: ignore[operator]
