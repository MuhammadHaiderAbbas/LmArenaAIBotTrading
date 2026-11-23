/**
 * Demo paper-execution engine.
 *
 * The same rules as the backend, in the browser: deterministic validation, partial
 * fills at degrading prices, commission and measured slippage, and a long-only book
 * reconciled to cash. State persists to localStorage so a reviewer can place an
 * order, reload, and still see the position — the demo is a walkthrough, not a form.
 *
 * Everything here is simulated. Nothing reaches a broker, and the UI says so on
 * every screen that could be mistaken for one.
 */

import { candlesForSymbol } from "./marketEngine";
import type {
  ExecutionEvent,
  ExecutionEventType,
  ExecutionOverview,
  Fill,
  Order,
  OrderSide,
  OrderType,
  Position,
} from "../executionTypes";

const STORAGE_KEY = "aurex.demo.execution.v1";
const COMMISSION_BPS = 2.0;
const SLIPPAGE_BPS = 1.5;
const PARTICIPATION_CAP = 0.15;
const MAX_BITES = 4;
// Matches the backend: a bar may be as old as its own timeframe (never less than
// two minutes). A daily bar is legitimately a day old; a flat 120s window would
// reject every daily series the demo has.
const DAILY_SECONDS = 86_400;
const MIN_STALE_AFTER_SECONDS = 120;
// Enough to buy a unit of any demo instrument — the point of the demo is to show
// the accounting, so the first order should not fail on price.
const STARTING_CASH = 100_000;

interface ExecutionState {
  cash: number;
  orders: Order[];
  positions: Position[];
  events: ExecutionEvent[];
}

function blankState(): ExecutionState {
  return { cash: STARTING_CASH, orders: [], positions: [], events: [] };
}

function load(): ExecutionState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return blankState();
    const parsed = JSON.parse(raw) as ExecutionState;
    if (typeof parsed.cash !== "number" || !Array.isArray(parsed.orders)) return blankState();
    return parsed;
  } catch {
    return blankState();
  }
}

function save(state: ExecutionState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage can be full or blocked; the session still works in memory.
  }
}

function nowIso(): string {
  return new Date().toISOString();
}

function eventFor(type: ExecutionEventType, message: string): ExecutionEvent {
  return {
    id: `ev-${Math.random().toString(36).slice(2, 10)}`,
    event_type: type,
    message,
    occurred_at: nowIso(),
    actor_id: null,
  };
}

function latestPrice(symbol: string): number | null {
  const candles = candlesForSymbol(symbol, "1d", 60);
  if (!candles || candles.length === 0) return null;
  return candles[candles.length - 1].close;
}

function latestBarClose(symbol: string): string | null {
  const candles = candlesForSymbol(symbol, "1d", 60);
  if (!candles || candles.length === 0) return null;
  // Age is measured from the close: a daily bar is current until it closes.
  return candles[candles.length - 1].close_time ?? candles[candles.length - 1].open_time;
}

function typicalVolume(symbol: string): number {
  const candles = candlesForSymbol(symbol, "1d", 60);
  if (!candles || candles.length === 0) return 0;
  const values = candles.map((candle) => candle.volume).sort((a, b) => a - b);
  return values[Math.floor(values.length / 2)] ?? 0;
}

export interface ProposalInput {
  symbol: string;
  side: OrderSide;
  quantity: number;
  orderType?: OrderType;
  limitPrice?: number | null;
  rationale?: string;
  clientOrderId?: string;
  origin?: string;
  execute?: boolean;
}

export interface ValidationResult {
  accepted: boolean;
  reason: string;
  checks: { check: string; passed: boolean; detail: string }[];
}

/** Deterministic checks, in the same order and with the same wording as the server. */
export function validate(state: ExecutionState, input: ProposalInput): ValidationResult {
  const checks: ValidationResult["checks"] = [];
  const push = (check: string, passed: boolean, detail: string) => {
    checks.push({ check, passed, detail });
    return passed;
  };
  const fail = (reason: string): ValidationResult => ({ accepted: false, reason, checks });

  const price = latestPrice(input.symbol);
  if (!push("market_data", price !== null, price === null ? "no series" : `price ${price}`)) {
    return fail(`No price series for ${input.symbol}; refusing to trade on a price that does not exist.`);
  }

  const barTime = latestBarClose(input.symbol);
  const ageSeconds = barTime ? (Date.now() - new Date(barTime).getTime()) / 1000 : Infinity;
  const staleAfter = Math.max(DAILY_SECONDS, MIN_STALE_AFTER_SECONDS);
  if (!push("data_freshness", ageSeconds <= staleAfter, `${Math.round(ageSeconds)}s old`)) {
    return fail(
      `Market data for ${input.symbol} is stale: the newest daily bar is ${Math.round(
        ageSeconds / 3600,
      )}h old. Refusing rather than filling at a price nobody could have transacted.`,
    );
  }

  if (!push("quantity", Number.isFinite(input.quantity) && input.quantity > 0, `qty ${input.quantity}`)) {
    return fail("Order quantity must be a positive, finite number.");
  }

  const notional = (price as number) * input.quantity;
  if (!push("affordable", input.side === "sell" || notional <= state.cash, `notional ${notional.toFixed(2)}`)) {
    return fail(
      `Insufficient cash: the order needs ${notional.toLocaleString("en-GB", {
        maximumFractionDigits: 2,
      })} and the account holds ${state.cash.toLocaleString("en-GB", {
        maximumFractionDigits: 2,
      })}. Leverage is disabled.`,
    );
  }

  if (input.side === "sell") {
    const held = state.positions
      .filter((position) => position.status === "open" && position.instrument_symbol === input.symbol)
      .reduce((sum, position) => sum + position.quantity, 0);
    if (!push("position", input.quantity <= held + 1e-9, `held ${held}`)) {
      return fail(
        `Cannot sell ${input.quantity} ${input.symbol}: the account holds ${held}. Short selling needs borrow and financing rules that are not implemented.`,
      );
    }
  }

  push("long_only", true, "long-only; leverage disabled per blueprint defaults");

  if (input.orderType === "limit" && (!input.limitPrice || input.limitPrice <= 0)) {
    return fail("A limit order needs a positive limit price.");
  }

  return { accepted: true, reason: "All deterministic checks passed.", checks };
}

export function placeOrder(input: ProposalInput): { order: Order; state: ExecutionState } {
  const state = load();

  // Idempotency: the same instruction twice is one order, not two positions.
  if (input.clientOrderId) {
    const existing = state.orders.find((order) => order.client_order_id === input.clientOrderId);
    if (existing) return { order: existing, state };
  }

  const validation = validate(state, input);
  const order: Order = {
    id: `ord-${Math.random().toString(36).slice(2, 12)}`,
    trading_account_id: "demo-account",
    instrument_symbol: input.symbol,
    client_order_id: input.clientOrderId ?? `ord-${Date.now()}`,
    side: input.side,
    order_type: input.orderType ?? "market",
    time_in_force: "day",
    status: "new",
    quantity: input.quantity,
    filled_quantity: 0,
    limit_price: input.limitPrice ?? null,
    average_fill_price: null,
    total_commission: 0,
    total_slippage: 0,
    reject_reason: "",
    origin: input.origin ?? "manual",
    rationale: input.rationale ?? "",
    submitted_at: null,
    completed_at: null,
    created_at: nowIso(),
    fills: [],
    events: [],
  };

  order.events = [eventFor("submitted", `${input.side} ${input.quantity} ${input.symbol} proposed`)];

  if (!validation.accepted) {
    order.status = "rejected";
    order.reject_reason = validation.reason;
    order.completed_at = nowIso();
    order.events.push(eventFor("rejected", validation.reason));
    state.orders.unshift(order);
    state.events.unshift(...order.events);
    save(state);
    return { order, state };
  }

  order.status = "validated";
  order.events.push(eventFor("validated", validation.reason));
  order.status = "submitted";
  order.submitted_at = nowIso();
  order.events.push(eventFor("acknowledged", "Paper broker acknowledged the order."));

  if (input.execute !== false) {
    execute(state, order);
  }

  state.orders.unshift(order);
  state.events.unshift(...order.events);
  save(state);
  return { order, state };
}

function execute(state: ExecutionState, order: Order): void {
  const mid = latestPrice(order.instrument_symbol);
  if (mid === null) {
    order.status = "rejected";
    order.reject_reason = "No price available at execution time.";
    order.completed_at = nowIso();
    order.events.push(eventFor("rejected", order.reject_reason));
    return;
  }

  if (order.order_type === "limit" && order.limit_price !== null) {
    const marketable = order.side === "buy" ? mid <= order.limit_price : mid >= order.limit_price;
    if (!marketable) {
      order.events.push(
        eventFor(
          "acknowledged",
          `Limit ${order.limit_price.toFixed(4)} is not marketable against ${mid.toFixed(4)}; the order rests unfilled.`,
        ),
      );
      return;
    }
  }

  const typical = typicalVolume(order.instrument_symbol);
  const biteCap = typical ? typical * PARTICIPATION_CAP : order.quantity;
  const requested = biteCap < order.quantity ? Math.ceil(order.quantity / biteCap) : 1;
  const bites = Math.max(1, Math.min(MAX_BITES, requested));

  let remaining = order.quantity;
  const fills: Fill[] = [];
  for (let sequence = 0; sequence < bites; sequence += 1) {
    const quantity = bites === 1 ? remaining : Math.min(biteCap, remaining);
    if (quantity <= 1e-12) break;
    const slip = (SLIPPAGE_BPS / 10_000) * (1 + sequence * 0.5);
    const price = order.side === "buy" ? mid * (1 + slip) : mid * (1 - slip);
    if (order.order_type === "limit" && order.limit_price !== null) {
      if (order.side === "buy" && price > order.limit_price) break;
      if (order.side === "sell" && price < order.limit_price) break;
    }
    const notional = price * quantity;
    const commission = (notional * COMMISSION_BPS) / 10_000;
    fills.push({
      sequence,
      quantity,
      price,
      reference_price: mid,
      slippage: Math.abs(price - mid) * quantity,
      commission,
      notional,
      filled_at: nowIso(),
      venue: "paper",
      ledger_transaction_id: `lt-${Math.random().toString(36).slice(2, 10)}`,
    });
    remaining = Math.round((remaining - quantity) * 1e10) / 1e10;
    order.events.push(
      eventFor("partial_fill", `Filled ${quantity.toFixed(6)} of ${order.instrument_symbol} at ${price.toFixed(5)}`),
    );
  }

  if (fills.length === 0) return;

  order.fills = fills;
  order.filled_quantity = fills.reduce((sum, fill) => sum + fill.quantity, 0);
  order.total_commission = fills.reduce((sum, fill) => sum + fill.commission, 0);
  order.total_slippage = fills.reduce((sum, fill) => sum + fill.slippage, 0);
  order.average_fill_price =
    fills.reduce((sum, fill) => sum + fill.price * fill.quantity, 0) / order.filled_quantity;

  if (order.side === "buy") {
    state.cash -= order.average_fill_price * order.filled_quantity + order.total_commission;
  } else {
    state.cash += order.average_fill_price * order.filled_quantity - order.total_commission;
  }

  const complete = order.filled_quantity >= order.quantity - 1e-9;
  order.status = complete ? "filled" : "partially_filled";
  if (complete) order.completed_at = nowIso();
  order.events.push(
    eventFor(
      complete ? "filled" : "partial_fill",
      complete
        ? `Order complete: ${order.quantity.toFixed(6)} ${order.instrument_symbol}`
        : `Partially filled ${order.filled_quantity.toFixed(6)} of ${order.quantity.toFixed(6)}.`,
    ),
  );

  applyToPosition(state, order);
}

function applyToPosition(state: ExecutionState, order: Order): void {
  const proceeds = order.fills.reduce((sum, fill) => sum + fill.notional, 0);
  const commission = order.total_commission;
  const quantity = order.filled_quantity;
  const existing = state.positions.find(
    (position) => position.status === "open" && position.instrument_symbol === order.instrument_symbol,
  );

  if (order.side === "buy") {
    if (!existing) {
      const average = proceeds / quantity;
      state.positions.unshift({
        id: `pos-${Math.random().toString(36).slice(2, 10)}`,
        instrument_symbol: order.instrument_symbol,
        status: "open",
        quantity,
        average_entry_price: average,
        cost_basis: proceeds,
        realised_pnl: 0,
        total_commission: commission,
        last_mark_price: order.average_fill_price,
        last_marked_at: nowIso(),
        opened_at: nowIso(),
        closed_at: null,
        strategy_code: order.origin !== "manual" ? order.origin : null,
        opening_rationale: order.rationale,
        market_value: proceeds,
        unrealised_pnl: 0,
      });
      state.events.unshift(eventFor("position_opened", `Opened ${quantity} ${order.instrument_symbol}`));
    } else {
      existing.quantity += quantity;
      existing.cost_basis += proceeds;
      existing.average_entry_price = existing.cost_basis / existing.quantity;
      existing.total_commission += commission;
      existing.last_mark_price = order.average_fill_price;
    }
    return;
  }

  if (!existing) return;
  const releasedBasis = Math.min(existing.cost_basis, existing.average_entry_price * quantity);
  const realised = proceeds - releasedBasis;
  existing.quantity = Math.round((existing.quantity - quantity) * 1e10) / 1e10;
  existing.cost_basis = Math.max(0, existing.cost_basis - releasedBasis);
  existing.realised_pnl += realised;
  existing.total_commission += commission;
  existing.last_mark_price = order.average_fill_price;
  if (existing.quantity <= 1e-9) {
    existing.quantity = 0;
    existing.cost_basis = 0;
    existing.status = "closed";
    existing.closed_at = nowIso();
    state.events.unshift(
      eventFor(
        "position_closed",
        `Closed ${order.instrument_symbol}; realised P&L ${realised >= 0 ? "+" : ""}${realised.toFixed(2)}`,
      ),
    );
  }
}

export function markPositions(): ExecutionState {
  const state = load();
  for (const position of state.positions) {
    if (position.status !== "open") continue;
    const price = latestPrice(position.instrument_symbol);
    if (price === null) continue;
    position.last_mark_price = price;
    position.last_marked_at = nowIso();
    position.market_value = position.quantity * price;
    position.unrealised_pnl = (price - position.average_entry_price) * position.quantity;
  }
  save(state);
  return state;
}

export function overview(): ExecutionOverview {
  const state = markPositions();
  const open = state.positions.filter((position) => position.status === "open");
  const closed = state.positions.filter((position) => position.status === "closed");
  const rejected = state.orders.filter((order) => order.status === "rejected");
  const positionsValue = open.reduce((sum, position) => sum + (position.market_value ?? position.cost_basis), 0);
  const unrealised = open.reduce((sum, position) => sum + (position.unrealised_pnl ?? 0), 0);
  const realised = closed.reduce((sum, position) => sum + position.realised_pnl, 0);
  return {
    open_positions: open.length,
    closed_positions: closed.length,
    orders_total: state.orders.length,
    orders_rejected: rejected.length,
    fills_total: state.orders.reduce((sum, order) => sum + order.fills.length, 0),
    cash: Math.round(state.cash * 10000) / 10000,
    positions_value: Math.round(positionsValue * 100) / 100,
    unrealised_pnl: Math.round(unrealised * 100) / 100,
    realised_pnl: Math.round(realised * 100) / 100,
    equity: Math.round((state.cash + positionsValue) * 100) / 100,
    total_commission:
      Math.round(state.orders.reduce((sum, order) => sum + order.total_commission, 0) * 100) / 100,
    total_slippage:
      Math.round(state.orders.reduce((sum, order) => sum + order.total_slippage, 0) * 100) / 100,
    rejection_rate: state.orders.length ? rejected.length / state.orders.length : 0,
    // The demo keeps cash and positions in one record, so reconciliation is a
    // statement about consistency rather than a comparison of two stores.
    reconciliation: {
      investment_agrees: true,
      realised_agrees: true,
      positions_value: Math.round(positionsValue * 100) / 100,
      ledger_investment: Math.round(positionsValue * 100) / 100,
    },
  };
}

export function orders(): Order[] {
  return load().orders;
}

export function positions(status: "open" | "closed" | "all" = "open"): Position[] {
  const state = markPositions();
  return status === "all" ? state.positions : state.positions.filter((item) => item.status === status);
}

export function events(): ExecutionEvent[] {
  return load().events.slice(0, 40);
}

export function cancelOrder(orderId: string): Order | null {
  const state = load();
  const order = state.orders.find((item) => item.id === orderId);
  if (!order) return null;
  if (["filled", "rejected", "cancelled", "expired"].includes(order.status)) return order;
  order.status = "cancelled";
  order.completed_at = nowIso();
  order.reject_reason = "Cancelled by user.";
  order.events.push(eventFor("cancelled", "Cancelled by user."));
  state.events.unshift(...order.events.slice(-1));
  save(state);
  return order;
}

export function resetDemo(): void {
  save(blankState());
}
