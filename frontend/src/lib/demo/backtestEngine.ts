/**
 * Demo backtest engine.
 *
 * A faithful port of the backend simulator's *rules*, run in the browser against
 * the same synthetic candle series the Markets screen draws:
 *
 *  - a signal from bar i is filled at bar i+1's open, never at bar i's close
 *  - the spread is paid on both sides, plus commission and slippage per side
 *  - a resting stop is resolved against the bar's low/high, so a stop that is hit
 *    mid-bar counts even when the close recovers
 *  - the benchmark is buy-and-hold over the identical window with identical costs
 *
 * The numbers therefore behave the way the real ones do: on synthetic random-walk
 * data no active strategy beats buy-and-hold, and the demo says so rather than
 * shipping a flattering equity curve.
 */

import { candlesForSymbol } from "./marketEngine";
import type { Timeframe } from "../marketTypes";

export type StrategyCode = "buy_and_hold_v1" | "trend_ma_v1" | "mean_reversion_v1";
export type Direction = "long" | "short" | "flat";

export interface CostModel {
  spreadBps: number;
  commissionBps: number;
  slippageBps: number;
  startingEquity: number;
  riskPerTrade: number;
  stopPct: number;
}

export const DEFAULT_COSTS: CostModel = {
  spreadBps: 1.0,
  commissionBps: 0.5,
  slippageBps: 0.5,
  startingEquity: 10_000,
  riskPerTrade: 0.01,
  stopPct: 0.02,
};

export interface StrategySpec {
  code: StrategyCode;
  name: string;
  family: string;
  description: string;
  hypothesis: string;
  parameters: Record<string, number>;
  minHistoryBars: number;
  useStop: boolean;
}

export const STRATEGIES: StrategySpec[] = [
  {
    code: "buy_and_hold_v1",
    name: "Buy and hold benchmark",
    family: "buy_and_hold",
    description: "Buy at the first tradable bar, hold to the end. Pays costs once.",
    hypothesis:
      "Not a strategy — the bar to clear. Any active strategy must beat this after paying spread, commission and slippage on every change of position.",
    parameters: {},
    minHistoryBars: 5,
    useStop: false,
  },
  {
    code: "trend_ma_v1",
    name: "Trend following (SMA cross)",
    family: "trend",
    description: "Long while the 20-bar SMA is above the 60-bar SMA; flat otherwise.",
    hypothesis:
      "Persistent moves continue long enough that a slow moving-average cross captures more of an advance than it gives back in whipsaws, after costs.",
    parameters: { fast: 20, slow: 60 },
    minHistoryBars: 200,
    useStop: true,
  },
  {
    code: "mean_reversion_v1",
    name: "Mean reversion (z-score)",
    family: "mean_reversion",
    description: "Long when price is 1.5σ below its 20-bar mean; exit as it reverts.",
    hypothesis:
      "Short-horizon overreaction reverts far enough, often enough, to pay for the round trip. It should lose in strong trends and that is expected.",
    parameters: { period: 20, entryZ: 1.5, exitZ: 0.3 },
    minHistoryBars: 200,
    useStop: true,
  },
];

// --------------------------------------------------------------------- signals

function sma(values: number[], period: number, index: number): number | null {
  if (index + 1 < period) return null;
  const window = values.slice(index + 1 - period, index + 1);
  return window.reduce((sum, value) => sum + value, 0) / period;
}

function std(values: number[], period: number, index: number): number | null {
  if (index + 1 < period) return null;
  const window = values.slice(index + 1 - period, index + 1);
  const mean = window.reduce((sum, value) => sum + value, 0) / period;
  return Math.sqrt(window.reduce((sum, value) => sum + (value - mean) ** 2, 0) / period);
}

export interface Signal {
  direction: Direction;
  reason: string;
}

export function signals(code: StrategyCode, closes: number[]): Signal[] {
  if (code === "buy_and_hold_v1") {
    return closes.map(() => ({ direction: "long" as Direction, reason: "benchmark: always long" }));
  }
  if (code === "trend_ma_v1") {
    return closes.map((_, index) => {
      const fast = sma(closes, 20, index);
      const slow = sma(closes, 60, index);
      if (fast === null || slow === null) return { direction: "flat" as Direction, reason: "warmup" };
      return fast > slow
        ? { direction: "long" as Direction, reason: `SMA20 ${fast.toFixed(4)} above SMA60 ${slow.toFixed(4)}` }
        : { direction: "flat" as Direction, reason: `SMA20 ${fast.toFixed(4)} below SMA60 ${slow.toFixed(4)}` };
    });
  }
  return closes.map((close, index) => {
    const mean = sma(closes, 20, index);
    const deviation = std(closes, 20, index);
    if (mean === null || deviation === null || deviation === 0) {
      return { direction: "flat" as Direction, reason: "warmup" };
    }
    const z = (close - mean) / deviation;
    if (z <= -1.5) return { direction: "long" as Direction, reason: `z-score ${z.toFixed(2)} at or below -1.5` };
    if (z >= -0.3) return { direction: "flat" as Direction, reason: `z-score ${z.toFixed(2)} reverted past -0.3` };
    return { direction: "flat" as Direction, reason: `z-score ${z.toFixed(2)} holding flat` };
  });
}

// ------------------------------------------------------------------ simulation

export interface Trade {
  sequence: number;
  direction: "long" | "short";
  quantity: number;
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  grossPnl: number;
  costs: number;
  netPnl: number;
  returnPct: number;
  barsHeld: number;
  exitReason: "stop" | "target" | "signal" | "end_of_data";
  entryReason: string;
}

export interface EquityPoint {
  at: string;
  equity: number;
  drawdown: number;
  inPosition: boolean;
}

export interface Metrics {
  trades: number;
  winRate: number | null;
  profitFactor: number | null;
  expectancy: number | null;
  grossReturn: number | null;
  netReturn: number | null;
  baselineReturn: number | null;
  edgeOverBaseline: number | null;
  totalCosts: number;
  costDrag: number;
  sharpe: number | null;
  sortino: number | null;
  maxDrawdown: number | null;
  exposure: number;
  turnover: number;
  averageBarsHeld: number | null;
}

export interface BacktestResult {
  strategyCode: StrategyCode;
  symbol: string;
  timeframe: string;
  status: "completed" | "refused";
  refusalReason: string;
  costs: CostModel;
  barsUsed: number;
  trades: Trade[];
  equity: EquityPoint[];
  metrics: Metrics | null;
  verdict: string;
  passesGate: boolean;
  tradeSummary: { long: number; short: number; wins: number; losses: number; exitReasons: Record<string, number> };
}

function entryPrice(price: number, direction: Direction, costs: CostModel): number {
  const half = costs.spreadBps / 2 / 10_000;
  const slip = costs.slippageBps / 10_000;
  return direction === "long" ? price * (1 + half + slip) : price * (1 - half - slip);
}

function exitPrice(price: number, direction: "long" | "short", costs: CostModel): number {
  const half = costs.spreadBps / 2 / 10_000;
  const slip = costs.slippageBps / 10_000;
  return direction === "long" ? price * (1 - half - slip) : price * (1 + half + slip);
}

function commission(notional: number, costs: CostModel): number {
  return Math.abs(notional) * costs.commissionBps / 10_000;
}

export function runBacktest(
  code: StrategyCode,
  symbol: string,
  timeframe: Timeframe = "1d",
  costs: CostModel = DEFAULT_COSTS,
): BacktestResult {
  const spec = STRATEGIES.find((item) => item.code === code);
  const empty: BacktestResult = {
    strategyCode: code,
    symbol,
    timeframe,
    status: "refused",
    refusalReason: "",
    costs,
    barsUsed: 0,
    trades: [],
    equity: [],
    metrics: null,
    verdict: "",
    passesGate: false,
    tradeSummary: { long: 0, short: 0, wins: 0, losses: 0, exitReasons: {} },
  };

  if (!spec) return { ...empty, refusalReason: `Unknown strategy '${code}'.` };
  const candles = candlesForSymbol(symbol, timeframe, 620);
  if (!candles) {
    return { ...empty, refusalReason: `${symbol} has no priceable history in the demo feed.` };
  }
  if (candles.length < spec.minHistoryBars) {
    return {
      ...empty,
      refusalReason: `${candles.length} bars of ${symbol} are available; ${code} needs ${spec.minHistoryBars} before the first decision is meaningful.`,
    };
  }

  const closes = candles.map((candle) => candle.close);
  const warmup = Math.min(spec.minHistoryBars, Math.floor(closes.length / 3));
  const all = signals(code, closes);

  let equity = costs.startingEquity;
  let peak = equity;
  let position:
    | {
        direction: "long" | "short";
        quantity: number;
        entryPrice: number;
        entryRaw: number;
        entryTime: string;
        entryIndex: number;
        stop: number | null;
        target: number | null;
        commission: number;
        notional: number;
        entryReason: string;
      }
    | null = null;
  let pending: Signal | null = null;
  let closedOnFinalBar = false;

  const trades: Trade[] = [];
  const equityCurve: EquityPoint[] = [];

  const closePosition = (
    rawExit: number,
    useCostsOnExit: boolean,
    when: string,
    index: number,
    reason: Trade["exitReason"],
  ) => {
    if (!position) return;
    const direction = position.direction;
    const exitFill = useCostsOnExit ? exitPrice(rawExit, direction, costs) : rawExit;
    const multiplier = direction === "long" ? 1 : -1;
    const grossPnl = (exitFill - position.entryPrice) * position.quantity * multiplier;
    const exitCommission = commission(exitFill * position.quantity, costs);
    const rawGross = (rawExit - position.entryRaw) * position.quantity * multiplier;
    const costsTotal = position.commission + exitCommission + (rawGross - grossPnl);
    const netPnl = grossPnl - exitCommission;
    const notional = position.entryPrice * position.quantity;
    trades.push({
      sequence: trades.length,
      direction,
      quantity: position.quantity,
      entryTime: position.entryTime,
      exitTime: when,
      entryPrice: position.entryPrice,
      exitPrice: exitFill,
      grossPnl,
      costs: costsTotal,
      netPnl,
      returnPct: notional ? (netPnl / notional) * 100 : 0,
      barsHeld: Math.max(1, index - position.entryIndex),
      exitReason: reason,
      entryReason: position.entryReason,
    });
    equity += netPnl;
    position = null;
  };

  for (let index = warmup; index < candles.length; index += 1) {
    const candle = candles[index];
    const when = candle.open_time || String(index);

    // (1) resting stop / target against this bar's range
    if (position && spec.useStop) {
      const stop = position.stop;
      const target = position.target;
      if (position.direction === "long") {
        if (stop !== null && candle.low <= stop) {
          closePosition(stop, false, when, index, "stop");
          pending = null;
          if (index === candles.length - 1) closedOnFinalBar = true;
        } else if (target !== null && candle.high >= target) {
          closePosition(target, false, when, index, "target");
          pending = null;
        }
      }
    }

    // (2) act on the previous bar's signal at this bar's open
    if (pending && index < candles.length - 1 && !closedOnFinalBar) {
      const desired = pending.direction;
      const holding: Direction = position ? position.direction : "flat";
      if (desired !== holding) {
        if (position) {
          closePosition(candle.open, true, when, index, "signal");
        }
        if (desired === "long" || desired === "short") {
          const fill = entryPrice(candle.open, desired, costs);
          const quantity = (equity * costs.riskPerTrade) / (candle.open * costs.stopPct);
          const notional = fill * quantity;
          const fee = commission(notional, costs);
          equity -= fee;
          position = {
            direction: desired,
            quantity,
            entryPrice: fill,
            entryRaw: candle.open,
            entryTime: when,
            entryIndex: index,
            stop: spec.useStop ? fill * (1 - costs.stopPct) : null,
            target: spec.useStop ? fill * (1 + 2 * costs.stopPct) : null,
            commission: fee,
            notional,
            entryReason: pending.reason,
          };
        }
      }
      pending = null;
    }

    // (3) mark to market and record the next signal
    const unrealised = position
      ? (candle.close - position.entryPrice) * position.quantity * (position.direction === "long" ? 1 : -1)
      : 0;
    const marked = equity + unrealised;
    peak = Math.max(peak, marked);
    equityCurve.push({
      at: when,
      equity: marked,
      drawdown: peak ? (marked - peak) / peak : 0,
      inPosition: position !== null,
    });

    pending = all[index];
  }

  if (position && !closedOnFinalBar) {
    const last = candles[candles.length - 1];
    closePosition(last.close, true, last.open_time || "end", candles.length - 1, "end_of_data");
  }

  const metrics = computeMetrics(equityCurve, trades, costs, candles);
  const decision = judge(metrics);

  const exitReasons: Record<string, number> = {};
  for (const trade of trades) exitReasons[trade.exitReason] = (exitReasons[trade.exitReason] ?? 0) + 1;

  return {
    strategyCode: code,
    symbol,
    timeframe,
    status: "completed",
    refusalReason: "",
    costs,
    barsUsed: candles.length - warmup,
    trades,
    equity: equityCurve,
    metrics,
    verdict: decision.reason,
    passesGate: decision.passed,
    tradeSummary: {
      long: trades.filter((trade) => trade.direction === "long").length,
      short: trades.filter((trade) => trade.direction === "short").length,
      wins: trades.filter((trade) => trade.netPnl > 0).length,
      losses: trades.filter((trade) => trade.netPnl <= 0).length,
      exitReasons,
    },
  };
}

function mean(values: number[]): number | null {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
}

function stdev(values: number[]): number | null {
  if (values.length < 2) return null;
  const mu = mean(values) as number;
  return Math.sqrt(values.reduce((sum, value) => sum + (value - mu) ** 2, 0) / (values.length - 1));
}

export function computeMetrics(
  curve: EquityPoint[],
  trades: Trade[],
  costs: CostModel,
  candles: { close: number }[],
): Metrics {
  const wins = trades.filter((trade) => trade.netPnl > 0).map((trade) => trade.netPnl);
  const losses = trades.filter((trade) => trade.netPnl < 0).map((trade) => trade.netPnl);
  const grossProfit = wins.reduce((sum, value) => sum + value, 0);
  const grossLoss = Math.abs(losses.reduce((sum, value) => sum + value, 0));

  const equities = curve.map((point) => point.equity);
  const returns: number[] = [];
  if (equities.length) returns.push((equities[0] - costs.startingEquity) / costs.startingEquity);
  for (let index = 1; index < equities.length; index += 1) {
    const previous = equities[index - 1];
    if (previous) returns.push((equities[index] - previous) / previous);
  }
  const volatility = stdev(returns);
  const downside = stdev(returns.filter((value) => value < 0));
  const mu = mean(returns);

  const finalEquity = equities.length ? equities[equities.length - 1] : costs.startingEquity;
  // Buy-and-hold over the identical window, measured from the same warmup bar, so
  // the benchmark can never drift away from the sample it describes.
  const firstClose = candles[Math.min(candles.length - 1, Math.floor(candles.length / 3))]?.close ?? 1;
  const lastClose = candles[candles.length - 1]?.close ?? firstClose;
  const baselineReturn = firstClose ? lastClose / firstClose - 1 : 0;

  const totalCosts = trades.reduce((sum, trade) => sum + trade.costs, 0);
  const netReturn = (finalEquity - costs.startingEquity) / costs.startingEquity;

  return {
    trades: trades.length,
    winRate: trades.length ? wins.length / trades.length : null,
    profitFactor: grossLoss > 0 ? grossProfit / grossLoss : null,
    expectancy: mean(trades.map((trade) => trade.netPnl)),
    grossReturn: (finalEquity + totalCosts - costs.startingEquity) / costs.startingEquity,
    netReturn,
    baselineReturn,
    edgeOverBaseline: netReturn - baselineReturn,
    totalCosts,
    costDrag: totalCosts / costs.startingEquity,
    sharpe: mu !== null && volatility && volatility > 0 ? (mu / volatility) * Math.sqrt(252) : null,
    sortino: mu !== null && downside && downside > 0 ? (mu / downside) * Math.sqrt(252) : null,
    maxDrawdown: curve.length ? Math.min(...curve.map((point) => point.drawdown)) : null,
    exposure: curve.length ? curve.filter((point) => point.inPosition).length / curve.length : 0,
    turnover:
      trades.reduce((sum, trade) => sum + Math.abs(trade.entryPrice * trade.quantity), 0) /
      costs.startingEquity,
    averageBarsHeld: mean(trades.map((trade) => trade.barsHeld)),
  };
}

export function judge(
  metrics: Metrics,
  { minTrades = 20, minEdge = 0.02 } = {},
): { passed: boolean; reason: string } {
  if (metrics.trades < minTrades) {
    return {
      passed: false,
      reason: `Only ${metrics.trades} trades; at least ${minTrades} are needed before a win rate means anything.`,
    };
  }
  if ((metrics.netReturn ?? 0) < 0) {
    return { passed: false, reason: `Net return ${((metrics.netReturn ?? 0) * 100).toFixed(2)}% after costs is negative.` };
  }
  if ((metrics.edgeOverBaseline ?? 0) < minEdge) {
    return {
      passed: false,
      reason: `Edge over buy-and-hold ${((metrics.edgeOverBaseline ?? 0) * 100).toFixed(2)}% is below the 2% floor; the strategy has not beaten the benchmark after costs.`,
    };
  }
  if ((metrics.maxDrawdown ?? 0) < -0.25) {
    return {
      passed: false,
      reason: `Maximum drawdown ${((metrics.maxDrawdown ?? 0) * 100).toFixed(1)}% breaches the 25% acceptance ceiling.`,
    };
  }
  if ((metrics.sharpe ?? 0) < 0.3) {
    return { passed: false, reason: `Sharpe ${(metrics.sharpe ?? 0).toFixed(2)} is too low to distinguish from noise.` };
  }
  return {
    passed: true,
    reason: `Net return ${((metrics.netReturn ?? 0) * 100).toFixed(2)}% after costs against buy-and-hold ${((metrics.baselineReturn ?? 0) * 100).toFixed(2)}%, with drawdown and Sharpe inside the acceptance limits.`,
  };
}

export function compare(code: StrategyCode, symbol: string, timeframe: Timeframe = "1d"): BacktestResult {
  return runBacktest(code, symbol, timeframe);
}
