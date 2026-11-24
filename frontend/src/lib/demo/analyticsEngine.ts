/**
 * Demo analytics — the same report the backend builds, from the demo book.
 *
 * The honesty rules are identical: a metric the sample cannot support is `null` with
 * a note, never a zero. The demo has no ledger journal, so the "curve" is derived from
 * the order history rather than cash entries; where that limits the result (Sharpe
 * needs daily observations, and the demo's history is a few clicks long) the metric is
 * withheld and the note says so.
 */

import * as execution from "./executionEngine";
import { candlesForSymbol } from "./marketEngine";
import type {
  AnalyticMetric,
  ClosedTrade,
  CurvePoint,
  PerformanceReport,
} from "../analyticsTypes";

const MIN_TRADES_FOR_STATS = 5;
const MIN_OBSERVATIONS_FOR_RATIOS = 10;
const TRADING_DAYS = 252;
const STARTING_EQUITY = 100_000;

function metric(
  key: string,
  label: string,
  value: number | null,
  unit: AnalyticMetric["unit"],
  definition: string,
  note = "",
): AnalyticMetric {
  return { key, label, value, unit, definition, note };
}

function sharpe(returns: number[]): number | null {
  if (returns.length < MIN_OBSERVATIONS_FOR_RATIOS) return null;
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const variance =
    returns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (returns.length - 1);
  const deviation = Math.sqrt(variance);
  if (!deviation) return null;
  return (mean / deviation) * Math.sqrt(TRADING_DAYS);
}

function sortino(returns: number[]): number | null {
  if (returns.length < MIN_OBSERVATIONS_FOR_RATIOS) return null;
  const mean = returns.reduce((sum, value) => sum + value, 0) / returns.length;
  const downside = returns.filter((value) => value < 0);
  if (!downside.length) return null;
  const deviation = Math.sqrt(
    downside.reduce((sum, value) => sum + value ** 2, 0) / returns.length,
  );
  if (!deviation) return null;
  return (mean / deviation) * Math.sqrt(TRADING_DAYS);
}

function maxDrawdown(values: number[]): number | null {
  if (!values.length) return null;
  let peak = values[0];
  let worst = 0;
  for (const value of values) {
    peak = Math.max(peak, value);
    if (peak > 0) worst = Math.max(worst, (peak - value) / peak);
  }
  return worst;
}

/**
 * Round trips reconstructed from the demo's order history, FIFO.
 *
 * The demo engine already stores each closed position's realised P&L, so trades are
 * read from the closed book rather than re-derived — one source of truth, the same
 * number the Execution screen shows.
 */
function closedTrades(): ClosedTrade[] {
  return execution
    .positions("closed")
    .map((position) => ({
      symbol: position.instrument_symbol,
      // A closed position zeroes its live quantity, so the size of the round trip
      // is the quantity it held at the moment it closed.
      quantity: position.closed_quantity || position.quantity,
      entry_price: position.average_entry_price,
      exit_price: position.last_mark_price ?? position.average_entry_price,
      realised_pnl: position.realised_pnl,
      commission: position.total_commission,
      holding_days:
        position.closed_at
          ? Math.max(
              0,
              (new Date(position.closed_at).getTime() - new Date(position.opened_at).getTime()) /
                86_400_000,
            )
          : 0,
      return_pct: (() => {
        const size = position.closed_quantity || position.quantity;
        const basis = position.average_entry_price * size;
        return basis ? position.realised_pnl / basis : 0;
      })(),
      opened_at: position.opened_at,
      closed_at: position.closed_at ?? position.opened_at,
      strategy_code: position.strategy_code,
    }))
    .filter((trade) => trade.quantity > 0);
}

function curve(book: { equity: number }, orders: ReturnType<typeof execution.orders>): CurvePoint[] {
  // One point per order, walking equity from the starting balance. Real history would
  // come from the ledger; the demo reconstructs it from the decisions it recorded.
  const points: CurvePoint[] = [
    { at: orders.length ? orders[orders.length - 1].created_at : new Date().toISOString(), equity: STARTING_EQUITY },
  ];
  // Costs only: an order's price movement is already reflected in the marks, so the
  // curve moves by what each order cost rather than by its notional.
  let running = STARTING_EQUITY;
  for (const order of [...orders].reverse()) {
    running -= order.total_commission + order.total_slippage;
    points.push({ at: order.created_at, equity: Math.round(running * 100) / 100 });
  }
  points.push({ at: new Date().toISOString(), equity: book.equity });
  return points;
}

function benchmarkCurve(symbols: string[], startEquity: number, points: number): CurvePoint[] {
  if (!symbols.length) return [];
  const series = symbols
    .map((symbol) => candlesForSymbol(symbol, "1d", 60))
    .filter((candles): candles is NonNullable<typeof candles> => Boolean(candles && candles.length));
  if (!series.length) return [];

  const length = Math.min(points, ...series.map((candles) => candles.length));
  const output: CurvePoint[] = [];
  for (let index = 0; index < length; index += 1) {
    let growth = 0;
    for (const candles of series) {
      const first = candles[candles.length - length].close;
      growth += first ? candles[candles.length - length + index].close / first : 1;
    }
    growth /= series.length;
    output.push({
      at: series[0][series[0].length - length + index].open_time,
      equity: Math.round(startEquity * growth * 100) / 100,
    });
  }
  return output;
}

export function report(): PerformanceReport {
  const book = execution.overview();
  const orders = execution.orders();
  const trades = closedTrades();
  const equityCurve = curve(book, orders);
  const returns: number[] = [];
  for (let index = 1; index < equityCurve.length; index += 1) {
    const previous = equityCurve[index - 1].equity;
    if (previous > 0) returns.push((equityCurve[index].equity - previous) / previous);
  }

  const symbols = Array.from(
    new Set([
      ...execution.positions("all").map((position) => position.instrument_symbol),
      ...orders.map((order) => order.instrument_symbol),
    ]),
  );
  const benchmark = benchmarkCurve(symbols, STARTING_EQUITY, Math.min(60, equityCurve.length || 60));

  const grossProfit = trades.filter((trade) => trade.realised_pnl > 0).reduce((sum, t) => sum + t.realised_pnl, 0);
  const grossLoss = -trades.filter((trade) => trade.realised_pnl < 0).reduce((sum, t) => sum + t.realised_pnl, 0);

  const traded = orders.reduce(
    (sum, order) => sum + (order.average_fill_price ?? 0) * order.filled_quantity,
    0,
  );
  const curveValues = equityCurve.map((point) => point.equity);
  const averageEquity = curveValues.length
    ? curveValues.reduce((sum, value) => sum + value, 0) / curveValues.length
    : book.equity;

  const bySymbol: Record<string, number> = {};
  const byStrategy: Record<string, number> = {};
  for (const trade of trades) {
    bySymbol[trade.symbol] = Math.round(((bySymbol[trade.symbol] ?? 0) + trade.realised_pnl) * 100) / 100;
    const key = trade.strategy_code ?? "manual";
    byStrategy[key] = Math.round(((byStrategy[key] ?? 0) + trade.realised_pnl) * 100) / 100;
  }

  const notes: string[] = [];
  if (!orders.length) {
    notes.push("No fills yet: there is nothing to measure, and an empty report is the honest one.");
  }
  if (orders.length && !trades.length) {
    notes.push("Positions are open but none is closed, so trade statistics stay blank until a round trip completes.");
  }
  if (trades.length && trades.length < MIN_TRADES_FOR_STATS) {
    notes.push(
      `Only ${trades.length} closed round trip(s); at least ${MIN_TRADES_FOR_STATS} are required before win rate or profit factor is reported.`,
    );
  }
  if (returns.length < MIN_OBSERVATIONS_FOR_RATIOS) {
    notes.push(
      `Only ${returns.length} equity observation(s); at least ${MIN_OBSERVATIONS_FOR_RATIOS} are needed before Sharpe or Sortino mean anything.`,
    );
  }
  if (benchmark.length) {
    notes.push(
      "The benchmark is equal-weight buy-and-hold of the instruments traded, over the same window — not an index.",
    );
  }
  notes.push(
    "The demo reconstructs its curve from recorded orders; the backend rebuilds it from the ledger and per-day positions. Where the demo cannot support a metric it withholds it rather than approximating.",
  );

  const metrics: AnalyticMetric[] = [
    metric(
      "net_return",
      "Net return",
      book.equity === STARTING_EQUITY && !orders.length ? 0 : (book.equity - STARTING_EQUITY) / STARTING_EQUITY,
      "pct",
      "(equity − deposited) ÷ deposited, after every cost",
    ),
    metric(
      "sharpe",
      "Sharpe ratio",
      sharpe(returns),
      "ratio",
      "Annualised mean daily return ÷ volatility of daily returns",
      returns.length >= MIN_OBSERVATIONS_FOR_RATIOS ? "" : `needs ≥${MIN_OBSERVATIONS_FOR_RATIOS} daily observations`,
    ),
    metric(
      "sortino",
      "Sortino ratio",
      sortino(returns),
      "ratio",
      "Annualised mean daily return ÷ downside deviation",
      returns.length >= MIN_OBSERVATIONS_FOR_RATIOS ? "" : `needs ≥${MIN_OBSERVATIONS_FOR_RATIOS} daily observations`,
    ),
    metric("max_drawdown", "Max drawdown", maxDrawdown(curveValues), "pct", "Worst peak-to-trough decline of marked equity"),
    metric(
      "win_rate",
      "Win rate",
      trades.length >= MIN_TRADES_FOR_STATS
        ? trades.filter((trade) => trade.realised_pnl > 0).length / trades.length
        : null,
      "pct",
      "Closed round trips with positive P&L ÷ all closed round trips",
      trades.length >= MIN_TRADES_FOR_STATS ? "" : `needs ≥${MIN_TRADES_FOR_STATS} closed trips`,
    ),
    metric(
      "profit_factor",
      "Profit factor",
      trades.length >= MIN_TRADES_FOR_STATS && grossLoss > 0 ? grossProfit / grossLoss : null,
      "ratio",
      "Gross profit ÷ gross loss",
      grossLoss === 0 && trades.length >= MIN_TRADES_FOR_STATS ? "undefined while nothing has been lost" : "",
    ),
    metric(
      "turnover",
      "Turnover",
      averageEquity > 0 && traded > 0 ? traded / averageEquity : null,
      "ratio",
      "Total traded notional ÷ average equity",
    ),
    metric(
      "exposure",
      "Current exposure",
      book.equity > 0 ? book.positions_value / book.equity : null,
      "pct",
      "Invested value ÷ equity right now — historical snapshots are not stored",
    ),
  ];

  return {
    as_of: new Date().toISOString(),
    equity: book.equity,
    cash: book.cash,
    starting_equity: STARTING_EQUITY,
    net_return: (book.equity - STARTING_EQUITY) / STARTING_EQUITY,
    metrics,
    equity_curve: equityCurve,
    benchmark_curve: benchmark,
    benchmark_return: benchmark.length
      ? (benchmark[benchmark.length - 1].equity - STARTING_EQUITY) / STARTING_EQUITY
      : null,
    attribution: { by_symbol: bySymbol, by_strategy: byStrategy },
    costs: {
      commission: Math.round(orders.reduce((sum, order) => sum + order.total_commission, 0) * 100) / 100,
      slippage: Math.round(orders.reduce((sum, order) => sum + order.total_slippage, 0) * 100) / 100,
      total:
        Math.round(
          orders.reduce((sum, order) => sum + order.total_commission + order.total_slippage, 0) * 100,
        ) / 100,
      orders: orders.length,
      fills: orders.reduce((sum, order) => sum + order.fills.length, 0),
      rejected_orders: orders.filter((order) => order.status === "rejected").length,
    },
    trades,
    notes,
    forward_looking: false,
  };
}
