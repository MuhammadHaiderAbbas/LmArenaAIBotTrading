// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import * as analytics from "./analyticsEngine";
import * as execution from "./executionEngine";

const SYMBOL = "AAPL";

describe("demo analytics", () => {
  beforeEach(() => {
    localStorage.clear();
    execution.resetDemo();
  });

  it("reports nothing and says so on an empty book", () => {
    const report = analytics.report();
    expect(report.trades).toEqual([]);
    expect(report.forward_looking).toBe(false);
    expect(report.notes.some((note) => note.includes("nothing to measure"))).toBe(true);
  });

  it("withholds ratios rather than showing zero", () => {
    const report = analytics.report();
    const byKey = Object.fromEntries(report.metrics.map((m) => [m.key, m]));
    expect(byKey.sharpe.value).toBeNull();
    expect(byKey.win_rate.value).toBeNull();
    expect(byKey.sharpe.note).toMatch(/observations/);
  });

  it("every metric carries a definition", () => {
    for (const metric of analytics.report().metrics) {
      expect(metric.definition.length).toBeGreaterThan(0);
      expect(metric.label.length).toBeGreaterThan(0);
    }
  });

  it("records a closed trade after a round trip", () => {
    execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    execution.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 1 });
    const report = analytics.report();
    expect(report.trades).toHaveLength(1);
    expect(report.trades[0].symbol).toBe(SYMBOL);
    expect(report.costs.commission).toBeGreaterThan(0);
  });

  it("does not count an open position as a trade", () => {
    execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    const report = analytics.report();
    expect(report.trades).toHaveLength(0);
    expect(report.notes.some((note) => note.includes("none is closed"))).toBe(true);
  });

  it("nets the trade against commission", () => {
    execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    execution.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 1 });
    const trade = analytics.report().trades[0];
    // Same-day, same price: the round trip must cost money, not make it.
    expect(trade.realised_pnl).toBeLessThan(0);
    expect(trade.commission).toBeGreaterThan(0);
  });

  it("attributes P&L by instrument and origin", () => {
    execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    execution.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 1 });
    const report = analytics.report();
    expect(report.attribution.by_symbol[SYMBOL]).toBeDefined();
    expect(report.attribution.by_strategy.manual).toBeDefined();
  });

  it("counts rejected orders separately from fills", () => {
    execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1e6 });
    const report = analytics.report();
    expect(report.costs.rejected_orders).toBe(1);
    expect(report.costs.fills).toBe(0);
  });

  it("never claims to be forward looking", () => {
    execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    expect(analytics.report().forward_looking).toBe(false);
  });
});
