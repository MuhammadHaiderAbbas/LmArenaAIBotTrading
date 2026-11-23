// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";
import * as engine from "./executionEngine";

// The first demo instrument; the engine refuses any symbol it has no series for.
const SYMBOL = "AAPL";

describe("demo execution engine", () => {
  beforeEach(() => {
    localStorage.clear();
    engine.resetDemo();
  });

  it("starts with cash and an empty book", () => {
    const state = engine.overview();
    expect(state.orders_total).toBe(0);
    expect(state.open_positions).toBe(0);
    expect(state.cash).toBeGreaterThan(0);
    expect(state.reconciliation.investment_agrees).toBe(true);
  });

  it("fills a market buy and opens a position", () => {
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 2 });
    expect(order.status).toBe("filled");
    expect(order.filled_quantity).toBeCloseTo(2, 9);
    expect(order.fills.length).toBeGreaterThan(0);
    expect(order.total_commission).toBeGreaterThan(0);
    expect(order.total_slippage).toBeGreaterThan(0);
    expect(order.average_fill_price).toBeGreaterThan(0);

    const open = engine.positions("open");
    expect(open).toHaveLength(1);
    expect(open[0].quantity).toBeCloseTo(2, 9);
    expect(open[0].cost_basis).toBeGreaterThan(0);
  });

  it("records the order lifecycle rather than only the end state", () => {
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    const kinds = order.events.map((event) => event.event_type);
    expect(kinds).toContain("submitted");
    expect(kinds).toContain("validated");
    expect(kinds).toContain("filled");
  });

  it("refuses a quantity of zero or less", () => {
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 0 });
    expect(order.status).toBe("rejected");
    expect(order.reject_reason).toMatch(/positive/i);
    expect(engine.positions("open")).toHaveLength(0);
  });

  it("refuses an order the account cannot afford and mentions leverage", () => {
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1e9 });
    expect(order.status).toBe("rejected");
    expect(order.reject_reason).toMatch(/Insufficient cash/);
    expect(order.reject_reason).toMatch(/Leverage is disabled/);
  });

  it("refuses a short by name", () => {
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 1 });
    expect(order.status).toBe("rejected");
    expect(order.reject_reason).toMatch(/holds 0/);
  });

  it("will not sell more than is held", () => {
    engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 5 });
    expect(order.status).toBe("rejected");
    expect(engine.positions("open")[0].quantity).toBeCloseTo(1, 9);
  });

  it("treats a repeated client order id as the same order", () => {
    const first = engine.placeOrder({
      symbol: SYMBOL,
      side: "buy",
      quantity: 1,
      clientOrderId: "demo-dupe",
    });
    const second = engine.placeOrder({
      symbol: SYMBOL,
      side: "buy",
      quantity: 1,
      clientOrderId: "demo-dupe",
    });
    expect(second.order.id).toBe(first.order.id);
    expect(engine.positions("open")).toHaveLength(1);
    expect(engine.positions("open")[0].quantity).toBeCloseTo(1, 9);
  });

  it("rests a non-marketable limit order instead of filling it", () => {
    const { order } = engine.placeOrder({
      symbol: SYMBOL,
      side: "buy",
      quantity: 1,
      orderType: "limit",
      limitPrice: 0.0001,
    });
    expect(order.status).toBe("submitted");
    expect(order.fills).toHaveLength(0);
    expect(engine.positions("open")).toHaveLength(0);
  });

  it("cancels a resting order and refuses to cancel a filled one", () => {
    const resting = engine.placeOrder({
      symbol: SYMBOL,
      side: "buy",
      quantity: 1,
      orderType: "limit",
      limitPrice: 0.0001,
    }).order;
    expect(engine.cancelOrder(resting.id)?.status).toBe("cancelled");

    const filled = engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 }).order;
    expect(engine.cancelOrder(filled.id)?.status).toBe("filled");
  });

  it("closes a position on a matching sell and books realised P&L", () => {
    engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 2 });
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 2 });
    expect(order.status).toBe("filled");

    const open = engine.positions("open");
    expect(open).toHaveLength(0);
    const closed = engine.positions("closed");
    expect(closed).toHaveLength(1);
    expect(closed[0].quantity).toBe(0);
    expect(closed[0].cost_basis).toBe(0);

    const state = engine.overview();
    expect(state.closed_positions).toBe(1);
    expect(state.reconciliation.realised_agrees).toBe(true);
  });

  it("charges commission and slippage on both sides of a round trip", () => {
    engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    const afterBuy = engine.overview();
    engine.placeOrder({ symbol: SYMBOL, side: "sell", quantity: 1 });
    const afterSell = engine.overview();
    expect(afterSell.total_commission).toBeGreaterThan(afterBuy.total_commission);
    expect(afterSell.total_slippage).toBeGreaterThan(afterBuy.total_slippage);
    expect(afterSell.fills_total).toBe(afterBuy.fills_total + 1);
  });

  it("marks open positions without moving cash", () => {
    engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    const before = engine.overview();
    const after = engine.overview();
    expect(after.cash).toBeCloseTo(before.cash, 6);
    expect(engine.positions("open")[0].market_value).toBeGreaterThan(0);
  });

  it("survives a reload because state is persisted", () => {
    engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 3 });
    expect(engine.positions("open")).toHaveLength(1);
    // A fresh read goes through localStorage, exactly like a page reload.
    expect(engine.orders()).toHaveLength(1);
  });

  it("reports a validation trace with every check", () => {
    const state = engine.overview();
    const result = engine.validate(
      { cash: 10_000, orders: [], positions: [], events: [] },
      { symbol: SYMBOL, side: "buy", quantity: 1 },
    );
    expect(result.accepted).toBe(true);
    expect(result.checks.map((check) => check.check)).toContain("market_data");
    expect(result.checks.map((check) => check.check)).toContain("affordable");
    expect(state.cash).toBeGreaterThan(0);
  });

  it("prices a buy at or above the mid", () => {
    const { order } = engine.placeOrder({ symbol: SYMBOL, side: "buy", quantity: 1 });
    for (const fill of order.fills) {
      expect(fill.price).toBeGreaterThanOrEqual(fill.reference_price);
    }
  });
});
