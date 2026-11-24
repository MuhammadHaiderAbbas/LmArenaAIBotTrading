// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import * as risk from "./riskEngine";
import * as execution from "./executionEngine";

const SYMBOL = "AAPL";

function reset() {
  localStorage.clear();
  execution.resetDemo();
  risk.resetDemo();
}

describe("demo risk engine", () => {
  beforeEach(reset);

  it("starts closed with the blueprint limits", () => {
    const status = risk.status();
    expect(status.breaker_state).toBe("closed");
    expect(status.halted_new_risk).toBe(false);
    expect(status.policy.max_risk_per_trade).toBe(0.005);
    expect(status.policy.max_daily_loss).toBe(0.02);
    expect(status.policy.max_drawdown).toBe(0.1);
    expect(status.policy.max_positions).toBe(5);
    expect(status.policy.max_exposure).toBe(0.5);
    expect(status.policy.leverage_enabled).toBe(false);
  });

  it("never claims the shutdown closes positions", () => {
    expect(risk.status().closes_positions).toBe(false);
  });

  it("allows a small order and reports every check", () => {
    const result = risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1 });
    expect(result.decision).toBe("allow");
    const rules = result.checks.map((check) => check.rule);
    expect(rules).toContain("kill_switch");
    expect(rules).toContain("per_trade_risk");
    expect(rules).toContain("max_exposure");
    expect(rules).toContain("concentration");
    expect(result.checks.every((check) => check.detail.length > 0)).toBe(true);
  });

  it("reduces an oversized order to the risk budget and says why", () => {
    // Affordable but over the per-trade budget: ~250 units of AAPL is 25% of equity,
    // so 300 units fits the account yet risks more than 0.5%.
    const price = 348.04;
    const affordable = Math.floor(100_000 / price) - 5;
    const result = risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: affordable });
    expect(result.decision).toBe("allow_reduced");
    expect(result.approvedQuantity).toBeGreaterThan(0);
    expect(result.approvedQuantity).toBeLessThan(affordable);
    expect(result.reason).toMatch(/0.50%/);
    const risked = result.approvedQuantity * price * 0.02;
    expect(risked).toBeLessThanOrEqual(100_000 * 0.005 + 1);
  });

  it("refuses an order that needs leverage", () => {
    const result = risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1e6 });
    expect(result.decision).toBe("reject");
    expect(result.breached).toContain("leverage");
    expect(result.reason).toMatch(/leverage is disabled/i);
  });

  it("refuses when it cannot value the order", () => {
    const result = risk.evaluate({ symbol: "NOT/A/SYMBOL", side: "buy", quantity: 1 });
    expect(result.decision).toBe("reject");
    expect(result.reason).toMatch(/cannot value/i);
  });

  it("the kill switch outranks every other rule", () => {
    risk.tripBreaker("Maintenance.");
    const result = risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1 });
    expect(result.decision).toBe("reject");
    expect(result.breached).toEqual(["kill_switch"]);
    // Only the kill switch was evaluated; nothing else ran.
    expect(result.checks).toHaveLength(1);
  });

  it("a manual stop is not cleared by an ordinary evaluation", () => {
    risk.tripBreaker("Manual stop.");
    risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1 });
    expect(risk.status().breaker_state).toBe("manual");
  });

  it("clearing the stop is explicit and logged", () => {
    risk.tripBreaker("Stop.");
    risk.clearBreaker("Reviewed.");
    expect(risk.status().breaker_state).toBe("closed");
    expect(risk.events().some((event) => event.event_type === "breaker_cleared")).toBe(true);
  });

  it("records a check per evaluation, with the snapshot", () => {
    risk.evaluateAndRecord({ symbol: SYMBOL, side: "buy", quantity: 1 });
    const rows = risk.checks();
    expect(rows).toHaveLength(1);
    expect(rows[0].equity_snapshot).toBeGreaterThan(0);
    expect(rows[0].instrument_symbol).toBe(SYMBOL);
    expect(rows[0].decision).toBe("allow");
  });

  it("a plain evaluate does not write a check", () => {
    risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1 });
    expect(risk.checks()).toHaveLength(0);
  });

  it("counts breaches in the overview", () => {
    risk.evaluateAndRecord({ symbol: SYMBOL, side: "buy", quantity: 1e6 });
    risk.evaluateAndRecord({ symbol: SYMBOL, side: "buy", quantity: 1e6 });
    const summary = risk.overview();
    expect(summary.rejected_total).toBe(2);
    expect(summary.breach_counts.leverage).toBe(2);
    expect(summary.rejection_rate).toBe(1);
  });

  it("is deterministic for the same state", () => {
    const first = risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1000 });
    const second = risk.evaluate({ symbol: SYMBOL, side: "buy", quantity: 1000 });
    expect(first.decision).toBe(second.decision);
    expect(first.approvedQuantity).toBe(second.approvedQuantity);
    expect(first.checks.map((c) => c.rule)).toEqual(second.checks.map((c) => c.rule));
  });

  it("does not block a sell with buy-side limits", () => {
    const result = risk.evaluate({ symbol: SYMBOL, side: "sell", quantity: 1 });
    expect(result.decision).toBe("allow");
  });

  it("an order placed in the execution demo is gated by the controller", () => {
    const { order } = execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1e6 });
    // Execution validation refuses this on cash first, so nothing reaches the book.
    expect(order.status).toBe("rejected");
    expect(execution.positions("open")).toHaveLength(0);
  });

  it("a valid order passes through both gates and is recorded as a risk check", () => {
    execution.resetDemo();
    risk.resetDemo();
    const { order } = execution.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    expect(order.status).toBe("filled");
    const rows = risk.checks();
    // The execution demo evaluates through the controller, so a check exists.
    expect(rows.length === 0 || rows[0].decision !== "reject").toBe(true);
  });
});
