import { describe, expect, it } from "vitest";
import { DEFAULT_COSTS, STRATEGIES, computeMetrics, judge, runBacktest, signals } from "./backtestEngine";

describe("signals", () => {
  it("emits one signal per bar for every strategy", () => {
    const closes = Array.from({ length: 300 }, (_, index) => 100 + Math.sin(index / 7) * 5 + index * 0.05);
    for (const spec of STRATEGIES) {
      expect(signals(spec.code, closes)).toHaveLength(closes.length);
    }
  });

  it("stays flat through warmup", () => {
    const closes = Array.from({ length: 300 }, (_, index) => 100 + index * 0.1);
    const result = signals("trend_ma_v1", closes);
    expect(result.slice(0, 59).every((signal) => signal.direction === "flat")).toBe(true);
    expect(result.slice(100).some((signal) => signal.direction === "long")).toBe(true);
  });
});

describe("backtest", () => {
  it("refuses a symbol the demo cannot price", () => {
    const result = runBacktest("trend_ma_v1", "NOT/REAL");
    expect(result.status).toBe("refused");
    expect(result.refusalReason).toContain("no priceable history");
    expect(result.metrics).toBeNull();
  });

  it("produces trades, an equity curve and metrics", () => {
    const result = runBacktest("trend_ma_v1", "GBP/USD");
    expect(result.status).toBe("completed");
    expect(result.trades.length).toBeGreaterThan(0);
    expect(result.equity.length).toBeGreaterThan(100);
    expect(result.metrics).not.toBeNull();
    expect(result.metrics!.baselineReturn).not.toBeNull();
    expect(result.metrics!.edgeOverBaseline).not.toBeNull();
  });

  it("treats buy and hold as a single decision", () => {
    const result = runBacktest("buy_and_hold_v1", "SPY");
    expect(result.trades).toHaveLength(1);
    expect(result.trades[0].exitReason).toBe("end_of_data");
  });

  it("makes a strategy worse when costs rise", () => {
    const cheap = runBacktest("trend_ma_v1", "EUR/USD", "1d", {
      ...DEFAULT_COSTS,
      spreadBps: 0,
      commissionBps: 0,
      slippageBps: 0,
    });
    const dear = runBacktest("trend_ma_v1", "EUR/USD", "1d", {
      ...DEFAULT_COSTS,
      spreadBps: 40,
      commissionBps: 20,
      slippageBps: 20,
    });
    expect(dear.metrics!.netReturn!).toBeLessThan(cheap.metrics!.netReturn!);
    expect(dear.metrics!.totalCosts).toBeGreaterThan(cheap.metrics!.totalCosts);
  });

  it("reports costs separately from gross on every trade", () => {
    const result = runBacktest("mean_reversion_v1", "AAPL");
    for (const trade of result.trades) {
      expect(trade.costs).toBeGreaterThanOrEqual(0);
      expect(trade.netPnl).toBeLessThanOrEqual(trade.grossPnl + 1e-9);
    }
  });

  it("never opens a position on the final bar", () => {
    const result = runBacktest("trend_ma_v1", "BTC/USD");
    for (const trade of result.trades) {
      expect(trade.barsHeld).toBeGreaterThan(0);
    }
  });

  it("is deterministic", () => {
    const first = runBacktest("trend_ma_v1", "GBP/USD");
    const second = runBacktest("trend_ma_v1", "GBP/USD");
    expect(first.trades.length).toBe(second.trades.length);
    expect(first.metrics!.netReturn).toBe(second.metrics!.netReturn);
  });

  it("summarises exits so the shape of the trades is visible", () => {
    const result = runBacktest("trend_ma_v1", "SPY");
    expect(result.tradeSummary.wins + result.tradeSummary.losses).toBe(result.metrics!.trades);
    expect(Object.keys(result.tradeSummary.exitReasons).length).toBeGreaterThan(0);
  });
});

describe("the acceptance gate", () => {
  const base = {
    trades: 40,
    winRate: 0.55,
    profitFactor: 1.4,
    expectancy: 20,
    grossReturn: 0.2,
    netReturn: 0.18,
    baselineReturn: 0.05,
    edgeOverBaseline: 0.13,
    totalCosts: 100,
    costDrag: 0.01,
    sharpe: 1.1,
    sortino: 1.4,
    maxDrawdown: -0.08,
    exposure: 0.4,
    turnover: 3,
    averageBarsHeld: 8,
  };

  it("refuses too few trades", () => {
    const decision = judge({ ...base, trades: 5 });
    expect(decision.passed).toBe(false);
    expect(decision.reason).toContain("at least 20");
  });

  it("refuses a negative net return", () => {
    expect(judge({ ...base, netReturn: -0.03 }).passed).toBe(false);
  });

  it("refuses a strategy that loses to buy and hold", () => {
    const decision = judge({ ...base, netReturn: 0.1, baselineReturn: 0.3, edgeOverBaseline: -0.2 });
    expect(decision.passed).toBe(false);
    expect(decision.reason).toContain("buy-and-hold");
  });

  it("refuses excessive drawdown", () => {
    expect(judge({ ...base, maxDrawdown: -0.4 }).passed).toBe(false);
  });

  it("refuses a Sharpe indistinguishable from noise", () => {
    expect(judge({ ...base, sharpe: 0.05 }).passed).toBe(false);
  });

  it("passes a genuinely good result and says what it beat", () => {
    const decision = judge(base);
    expect(decision.passed).toBe(true);
    expect(decision.reason).toContain("buy-and-hold");
  });

  it("does not pass any shipped strategy on synthetic data", () => {
    // The honest outcome: random-walk demo data has no exploitable edge, so the
    // demo must not claim a validated strategy.
    for (const symbol of ["GBP/USD", "SPY", "BTC/USD"]) {
      for (const spec of STRATEGIES) {
        const result = runBacktest(spec.code, symbol);
        if (result.status !== "completed") continue;
        expect(result.passesGate, `${spec.code} on ${symbol} should not pass`).toBe(false);
      }
    }
  });
});

describe("metrics", () => {
  it("computes a drawdown that is never positive", () => {
    const result = runBacktest("trend_ma_v1", "GBP/USD");
    expect(result.metrics!.maxDrawdown!).toBeLessThanOrEqual(0);
  });

  it("keeps exposure inside 0..1", () => {
    const result = runBacktest("trend_ma_v1", "GBP/USD");
    expect(result.metrics!.exposure).toBeGreaterThanOrEqual(0);
    expect(result.metrics!.exposure).toBeLessThanOrEqual(1);
  });

  it("returns empty metrics safely for an empty curve", () => {
    const metrics = computeMetrics([], [], DEFAULT_COSTS, [{ close: 100 }]);
    expect(metrics.trades).toBe(0);
    expect(metrics.winRate).toBeNull();
    expect(metrics.maxDrawdown).toBeNull();
  });
});
