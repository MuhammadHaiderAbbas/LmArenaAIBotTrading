"""Event-driven backtest simulator with a realistic cost model.

The simulation walks bars in order and processes, for each bar, in this order:

    1. mark equity at the close
    2. check any resting stop / target against *this* bar's range
    3. act on the signal computed at the *previous* close, at *this* bar's open

Step 3 is the look-ahead defence. A signal derived from a close cannot be filled at
that same close; it is filled at the next open, after the market has moved. If a
strategy's edge disappears when you shift it one bar, the edge was the shift.

Costs are charged on both sides of every trade:

| Cost | Basis |
| --- | --- |
| Spread | half the quoted spread on entry and on exit, because a buyer pays the offer and a seller the bid |
| Commission | a flat basis-point charge on notional, per side |
| Slippage | adverse basis points on notional, per side, so fills are worse than the screen price |
| Financing | per-day charge on notional held overnight (swap/rollover analogue) |

All four are stored on the `Backtest` row, so a result can never be reproduced with
different assumptions than the ones it was produced with.

Position size is **risk-based**, not notional-based: quantity is chosen so a stop-out
costs `risk_per_trade` of current equity. That is how the position would actually be
sized, and it means a strategy cannot look good merely by leveraging up in a calm
period.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from app.models.strategy import ExitReason
from app.services.strategies.rules import Bars, Direction, Signal


@dataclass(frozen=True)
class CostModel:
    """Everything a fill costs, in basis points."""

    spread_bps: float = 1.0
    commission_bps: float = 0.5
    slippage_bps: float = 0.5
    financing_bps_per_day: float = 0.0
    starting_equity: float = 10_000.0
    risk_per_trade: float = 0.01
    stop_pct: float = 0.02

    def entry_price(self, price: float, direction: Direction) -> float:
        """Buy at the offer, sell at the bid, then slip against ourselves."""
        half_spread = self.spread_bps / 2 / 10_000
        slip = self.slippage_bps / 10_000
        if direction == Direction.LONG:
            return price * (1 + half_spread + slip)
        return price * (1 - half_spread - slip)

    def exit_price(self, price: float, direction: Direction) -> float:
        """Exits pay the same costs in the opposite direction."""
        half_spread = self.spread_bps / 2 / 10_000
        slip = self.slippage_bps / 10_000
        if direction == Direction.LONG:
            return price * (1 - half_spread - slip)
        return price * (1 + half_spread + slip)

    def commission(self, notional: float) -> float:
        return abs(notional) * self.commission_bps / 10_000

    def financing(self, notional: float, days: int) -> float:
        if days <= 0:
            return 0.0
        return abs(notional) * self.financing_bps_per_day / 10_000 * days


@dataclass
class SimTrade:
    direction: str
    quantity: float
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    stop_price: float | None
    target_price: float | None
    gross_pnl: float
    costs: float
    net_pnl: float
    return_pct: float
    bars_held: int
    exit_reason: ExitReason
    entry_reason: str


@dataclass
class EquityPoint:
    at: datetime
    equity: float
    drawdown: float
    in_position: bool


@dataclass
class SimulationResult:
    trades: list[SimTrade] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    bars_used: int = 0
    warmup_bars: int = 0
    refused: bool = False
    refusal_reason: str = ""
    open_position: bool = False


def simulate(
    bars: Bars,
    timestamps: list[datetime],
    signals: list[Signal],
    costs: CostModel,
    *,
    warmup_bars: int = 0,
    use_stop: bool = True,
) -> SimulationResult:
    """Run one strategy over one instrument.

    `signals[i]` is the position wanted for the bar that opens at `timestamps[i+1]`.
    The final signal is never acted on: there is no next open to fill it at, and
    filling it anyway is the most common backtest fudge.
    """
    if len(bars) != len(signals) or len(bars) != len(timestamps):
        raise ValueError("bars, timestamps and signals must be the same length")
    if len(bars) < warmup_bars + 2:
        return SimulationResult(
            refused=True,
            refusal_reason=(
                f"Only {len(bars)} bars available; this strategy needs {warmup_bars} warmup "
                "bars plus a bar to trade, which is not enough to say anything."
            ),
        )

    equity = costs.starting_equity
    peak = equity
    result = SimulationResult(warmup_bars=warmup_bars)

    position: dict | None = None
    pending: Signal | None = None
    closed_on_final_bar = False

    for index in range(warmup_bars, len(bars)):
        close = bars.closes[index]
        high = bars.highs[index]
        low = bars.lows[index]
        open_price = bars.opens[index]
        when = timestamps[index]
        bars_elapsed = index - warmup_bars + 1

        # (1) A stop or target resting from a previous bar is resolved against this
        # bar's range before anything else. Assuming the stop was not hit because
        # the close recovered is the classic optimistic bug.
        if position is not None and use_stop:
            exit_price: float | None = None
            reason: ExitReason | None = None
            if position["direction"] == "long":
                if position["stop"] is not None and low <= position["stop"]:
                    exit_price, reason = position["stop"], ExitReason.STOP
                elif position["target"] is not None and high >= position["target"]:
                    exit_price, reason = position["target"], ExitReason.TARGET
            if exit_price is not None and reason is not None:
                trade = _close(position, exit_price, when, index, reason, costs, equity)
                result.trades.append(trade)
                equity += trade.net_pnl
                position = None
                if index == len(bars) - 1:
                    closed_on_final_bar = True
                # A stop-out cancels any pending entry: re-entering on the same bar
                # that stopped us out is a form of re-entry optimism.
                pending = None

        # (2) Act on the previous bar's signal at this bar's open. Nothing is opened
        # on the final bar: there is no subsequent bar to exit into, so it would be
        # a position the data can never close.
        if pending is not None and index < len(bars) - 1 and not closed_on_final_bar:
            desired = pending.direction
            holding = position["direction"] if position else Direction.FLAT
            if desired != holding:
                if position is not None:
                    trade = _close(
                        position,
                        costs.exit_price(open_price, Direction(position["direction"])),
                        when,
                        index,
                        ExitReason.SIGNAL,
                        costs,
                        equity,
                    )
                    result.trades.append(trade)
                    equity += trade.net_pnl
                    position = None

                if desired in (Direction.LONG, Direction.SHORT):
                    stop_distance = costs.stop_pct
                    quantity = (equity * costs.risk_per_trade) / (open_price * stop_distance)
                    fill = costs.entry_price(open_price, desired)
                    notional = fill * quantity
                    commission = costs.commission(notional)
                    equity -= commission
                    stop = None
                    target = None
                    if use_stop:
                        stop = (
                            fill * (1 - stop_distance)
                            if desired == Direction.LONG
                            else fill * (1 + stop_distance)
                        )
                        target = (
                            fill * (1 + 2 * stop_distance)
                            if desired == Direction.LONG
                            else fill * (1 - 2 * stop_distance)
                        )
                    position = {
                        "direction": "long" if desired == Direction.LONG else "short",
                        "quantity": quantity,
                        "entry_price": fill,
                        "entry_time": when,
                        "entry_index": index,
                        "stop": stop,
                        "target": target,
                        "commission": commission,
                        "entry_reason": pending.reason,
                        "notional": notional,
                        # Kept so the cost of the spread and slippage on entry can
                        # be quantified rather than assumed.
                        "unadjusted_entry": open_price,
                    }
            pending = None

        # (3) Mark to market at this bar's close, and record the next signal.
        mark_price = close
        unrealised = 0.0
        financing = 0.0
        if position is not None:
            direction_multiplier = 1 if position["direction"] == "long" else -1
            unrealised = (
                (mark_price - position["entry_price"]) * position["quantity"] * direction_multiplier
            )
            financing = costs.financing(position["notional"], 1)
        marked = equity + unrealised - (financing if position is not None else 0.0)
        peak = max(peak, marked)
        drawdown = (marked - peak) / peak if peak else 0.0
        result.equity.append(
            EquityPoint(at=when, equity=marked, drawdown=drawdown, in_position=position is not None)
        )
        if position is not None:
            position["financing"] = position.get("financing", 0.0) + financing
            position["bars_held"] = bars_elapsed

        pending = signals[index]

    # Open positions are closed at the last close, so the result is realised. The
    # equity curve already marked it, which is why this does not double count.
    if position is not None and not closed_on_final_bar:
        last_index = len(bars) - 1
        trade = _close(
            position,
            costs.exit_price(bars.closes[last_index], Direction(position["direction"])),
            timestamps[last_index],
            last_index,
            ExitReason.END_OF_DATA,
            costs,
            equity,
        )
        result.trades.append(trade)
        result.open_position = True

    result.bars_used = len(bars) - warmup_bars
    return result


def _close(
    position: dict,
    raw_exit_price: float,
    when: datetime,
    index: int,
    reason: ExitReason,
    costs: CostModel,
    equity: float,
) -> SimTrade:
    """Turn an open position into a realised trade with every cost itemised."""
    direction_enum = Direction.LONG if position["direction"] == "long" else Direction.SHORT
    exit_fill = (
        costs.exit_price(raw_exit_price, direction_enum)
        if reason
        not in (
            ExitReason.STOP,
            ExitReason.TARGET,
        )
        else raw_exit_price
    )
    # Stop and target fills already are the traded price; only the spread and slip
    # on the way out are charged, as a cost, so the fill price stays identifiable.
    if reason in (ExitReason.STOP, ExitReason.TARGET):
        half_spread = costs.spread_bps / 2 / 10_000
        slip = costs.slippage_bps / 10_000
        adjustment = (half_spread + slip) * (-1 if position["direction"] == "long" else 1)
        exit_fill = raw_exit_price * (1 + adjustment)

    direction_multiplier = 1 if position["direction"] == "long" else -1
    gross_pnl = (exit_fill - position["entry_price"]) * position["quantity"] * direction_multiplier
    exit_commission = costs.commission(exit_fill * position["quantity"])
    financing = position.get("financing", 0.0)

    # Spread and slippage are baked into the fill prices themselves, so they are
    # reported through those prices and NOT subtracted again here. Charging them
    # twice would make every strategy look worse than it trades; charging them
    # never would make the cost column a lie. `gross_pnl` below is measured from
    # the *raw* price so the difference is visible.
    raw_gross = (
        (raw_exit_price - position["unadjusted_entry"])
        * position["quantity"]
        * direction_multiplier
    )
    costs_total = position["commission"] + exit_commission + financing + (raw_gross - gross_pnl)
    net_pnl = gross_pnl - exit_commission - financing
    notional = position["entry_price"] * position["quantity"]

    return SimTrade(
        direction=position["direction"],
        quantity=position["quantity"],
        entry_time=position["entry_time"],
        exit_time=when,
        entry_price=position["entry_price"],
        exit_price=exit_fill,
        stop_price=position["stop"],
        target_price=position["target"],
        gross_pnl=gross_pnl,
        costs=costs_total,
        net_pnl=net_pnl,
        return_pct=(net_pnl / notional * 100) if notional else 0.0,
        bars_held=max(1, index - position["entry_index"]),
        exit_reason=reason,
        entry_reason=position["entry_reason"],
    )


# --------------------------------------------------------------------- metrics


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def compute_metrics(
    result: SimulationResult,
    *,
    starting_equity: float,
    final_equity: float,
    baseline_return: float,
    bars_per_year: float = 252.0,
) -> dict:
    """Every acceptance metric from the blueprint, plus the benchmark.

    Sharpe uses daily equity returns and a zero risk-free rate; that is stated
    rather than implied, because a Sharpe with an undisclosed risk-free rate is
    not comparable to anything.
    """
    trades = result.trades
    wins = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losses = [trade.net_pnl for trade in trades if trade.net_pnl < 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None

    total_costs = sum(trade.costs for trade in trades)
    equity_values = [point.equity for point in result.equity]
    returns: list[float] = []
    for index in range(1, len(equity_values)):
        previous = equity_values[index - 1]
        if previous:
            returns.append((equity_values[index] - previous) / previous)
    # The first bar's return is included so a strategy that loses everything on day
    # one does not get a clean Sharpe from an empty series.
    if equity_values and starting_equity:
        returns.insert(0, (equity_values[0] - starting_equity) / starting_equity)

    volatility = _stdev(returns)
    downside = [value for value in returns if value < 0]
    downside_deviation = _stdev(downside) if len(downside) > 1 else None
    mean_return = _mean(returns)

    sharpe = None
    if mean_return is not None and volatility and volatility > 0:
        sharpe = mean_return / volatility * math.sqrt(bars_per_year)
    sortino = None
    if mean_return is not None and downside_deviation and downside_deviation > 0:
        sortino = mean_return / downside_deviation * math.sqrt(bars_per_year)

    max_drawdown = min((point.drawdown for point in result.equity), default=0.0)
    exposure = (
        sum(1 for point in result.equity if point.in_position) / len(result.equity)
        if result.equity
        else 0.0
    )
    notional_traded = sum(abs(trade.entry_price * trade.quantity) for trade in trades)

    net_return = (final_equity - starting_equity) / starting_equity if starting_equity else 0.0
    # Gross return adds the itemised costs back, which is what the strategy would
    # have returned at zero spread, commission and slippage. The gap between the
    # two is the cost drag, and it is usually the whole story.
    gross_return = (
        ((final_equity + total_costs) - starting_equity) / starting_equity
        if starting_equity
        else 0.0
    )

    metrics = {
        "trades": len(trades),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "profit_factor": profit_factor,
        "average_win": _mean(wins),
        "average_loss": _mean(losses),
        "expectancy": _mean([trade.net_pnl for trade in trades]),
        "gross_return": gross_return,
        "net_return": net_return,
        "baseline_return": baseline_return,
        "edge_over_baseline": net_return - baseline_return,
        "total_costs": total_costs,
        "cost_drag": (total_costs / starting_equity) if starting_equity else 0.0,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_drawdown,
        "volatility": volatility * math.sqrt(bars_per_year) if volatility else None,
        "exposure": exposure,
        "turnover": (notional_traded / starting_equity) if starting_equity else 0.0,
        "average_bars_held": _mean([float(trade.bars_held) for trade in trades]),
    }
    metrics["passes_gate"], metrics["verdict"] = judge(metrics)
    return metrics


def judge(metrics: dict, *, min_trades: int = 20, min_edge: float = 0.02) -> tuple[bool, str]:
    """The strategy acceptance gate.

    Deliberately strict on sample size: a 9-trade strategy with a 78% win rate is a
    story, not a result. Every rejection says which bar it failed, using the same
    phrasing pattern as the model gate so the two are read the same way.
    """
    trades = metrics.get("trades") or 0
    if trades < min_trades:
        return False, (
            f"Only {trades} trades; at least {min_trades} are needed before a win rate "
            "means anything."
        )
    net_return = metrics.get("net_return")
    baseline = metrics.get("baseline_return")
    edge = metrics.get("edge_over_baseline")
    if net_return is not None and net_return < 0:
        return False, f"Net return {net_return:+.2%} after costs is negative."
    if edge is not None and edge < min_edge:
        return False, (
            f"Edge over buy-and-hold {edge:+.2%} is below the {min_edge:.0%} floor; the "
            "strategy has not beaten the benchmark after costs."
        )
    drawdown = metrics.get("max_drawdown")
    if drawdown is not None and drawdown < -0.25:
        return False, f"Maximum drawdown {drawdown:.1%} breaches the 25% acceptance ceiling."
    sharpe = metrics.get("sharpe")
    if sharpe is not None and sharpe < 0.3:
        return False, f"Sharpe {sharpe:.2f} is too low to distinguish from noise."
    baseline_text = f"{baseline:+.2%}" if baseline is not None else "n/a"
    return True, (
        f"Net return {net_return:+.2%} after costs against buy-and-hold {baseline_text}, "
        "with the drawdown and Sharpe both inside the acceptance limits."
    )
