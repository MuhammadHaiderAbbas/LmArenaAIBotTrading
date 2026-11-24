/**
 * Demo risk engine — the Phase 9 controller in the browser.
 *
 * The rules, the order they run in, and the wording of refusals match the backend.
 * The demo account here is the same simulated book the execution demo trades, so a
 * reviewer can place an order on the Execution screen and then watch the Risk Centre
 * decide whether it should have been allowed.
 */

import { candlesForSymbol } from "./marketEngine";
import * as execution from "./executionEngine";
import type {
  BreakerState,
  RiskCheckRecord,
  RiskDecision,
  RiskEventRecord,
  RiskOverview,
  RiskRuleCheck,
  RiskStatus,
} from "../riskTypes";

const STORAGE_KEY = "aurex.demo.risk.v1";
const PAPER_SPREAD_BPS = 1.5;
const DEFAULT_STOP_PCT = 0.02;

/** The blueprint's illustrative paper-mode defaults. */
export const DEFAULT_POLICY = {
  max_risk_per_trade: 0.005,
  max_daily_loss: 0.02,
  max_drawdown: 0.1,
  max_positions: 5,
  max_exposure: 0.5,
  max_position_concentration: 0.3,
  max_correlation: 0.85,
  max_spread_bps: 25,
  max_orders_per_hour: 30,
  leverage_enabled: false,
};

export const APPLIED_RULES = [
  "concentration",
  "correlation",
  "daily_loss",
  "data_freshness",
  "kill_switch",
  "leverage",
  "max_drawdown",
  "max_exposure",
  "max_positions",
  "order_rate",
  "per_trade_risk",
  "spread",
];

interface RiskState {
  breaker: BreakerState;
  breakerReason: string;
  breakerTrigger: string;
  breakerSince: string | null;
  checks: RiskCheckRecord[];
  events: RiskEventRecord[];
  policy: typeof DEFAULT_POLICY;
  policyVersion: number;
}

function blank(): RiskState {
  return {
    breaker: "closed",
    breakerReason: "",
    breakerTrigger: "",
    breakerSince: null,
    checks: [],
    events: [],
    policy: { ...DEFAULT_POLICY },
    policyVersion: 1,
  };
}

function load(): RiskState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return blank();
    const parsed = JSON.parse(raw) as RiskState;
    if (!parsed || typeof parsed !== "object" || !parsed.policy) return blank();
    return { ...blank(), ...parsed };
  } catch {
    return blank();
  }
}

function save(state: RiskState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage may be full or blocked; the session still works in memory.
  }
}

function event(
  type: string,
  message: string,
  severity: RiskEventRecord["severity"] = "info",
  detail: Record<string, unknown> = {},
): RiskEventRecord {
  return {
    id: `re-${Math.random().toString(36).slice(2, 10)}`,
    event_type: type,
    severity,
    message,
    detail,
    created_at: new Date().toISOString(),
  };
}

function latestPrice(symbol: string): number | null {
  const candles = candlesForSymbol(symbol, "1d", 60);
  if (!candles || candles.length === 0) return null;
  return candles[candles.length - 1].close;
}

function openPositions() {
  return execution.positions("open");
}

function exposure(): number {
  const book = execution.overview();
  return book.equity > 0 ? book.positions_value / book.equity : 0;
}

function drawdown(): number {
  // The demo has no stored history, so the peak is the starting equity plus any
  // realised gain. This is the honest limitation and it is stated in the UI.
  const book = execution.overview();
  const peak = Math.max(100_000, book.equity);
  return peak > 0 ? Math.max(0, (peak - book.equity) / peak) : 0;
}

interface Check {
  rule: string;
  passed: boolean;
  observed: number | string | boolean | null;
  limit: number | string | boolean | null;
  detail: string;
}

export interface Evaluation {
  decision: RiskDecision;
  approvedQuantity: number;
  reason: string;
  breached: string[];
  checks: Check[];
  equity: number;
  exposure: number;
  openPositions: number;
}

export function evaluate(input: {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  price?: number | null;
  stopPct?: number;
  policy?: typeof DEFAULT_POLICY;
}): Evaluation {
  const state = load();
  const policy = input.policy ?? state.policy;
  const price = input.price ?? latestPrice(input.symbol);
  const stopPct = input.stopPct ?? DEFAULT_STOP_PCT;
  const book = execution.overview();
  const equity = book.equity;
  const positions = openPositions();
  const checks: Check[] = [];
  const breached: string[] = [];

  const allow = (): Evaluation => ({
    decision: "allow",
    approvedQuantity: input.quantity,
    reason: "All risk rules passed.",
    breached,
    checks,
    equity,
    exposure: exposure(),
    openPositions: positions.length,
  });
  const reject = (rule: string, detail: string, observed: unknown, limit: unknown): Evaluation => {
    checks.push({ rule, passed: false, observed: observed as never, limit: limit as never, detail });
    breached.push(rule);
    return {
      decision: "reject",
      approvedQuantity: 0,
      reason: detail,
      breached,
      checks,
      equity,
      exposure: exposure(),
      openPositions: positions.length,
    };
  };

  if (price === null || price <= 0) {
    return reject(
      "data_freshness",
      `No price for ${input.symbol}; the risk engine will not approve an order it cannot value.`,
      null,
      null,
    );
  }

  if (state.breaker !== "closed") {
    return reject(
      "kill_switch",
      state.breaker === "manual"
        ? "A manual emergency stop is in force. New risk is halted until an authorised user clears it."
        : "The circuit breaker is open: a risk limit was breached and new orders are halted.",
      state.breaker,
      "closed",
    );
  }
  checks.push({ rule: "kill_switch", passed: true, observed: "closed", limit: "closed", detail: "Breaker closed." });

  const recent = execution.orders().filter((order) => order.status !== "rejected").length;
  if (recent >= policy.max_orders_per_hour) {
    return reject(
      "order_rate",
      `${recent} orders in this session; the policy allows ${policy.max_orders_per_hour}. This is the runaway-loop guard.`,
      recent,
      policy.max_orders_per_hour,
    );
  }
  checks.push({
    rule: "order_rate",
    passed: true,
    observed: recent,
    limit: policy.max_orders_per_hour,
    detail: "Within the hourly order budget.",
  });

  const dailyLoss = book.realised_pnl < 0 && equity > 0 ? -book.realised_pnl / equity : 0;
  if (dailyLoss >= policy.max_daily_loss) {
    return reject(
      "daily_loss",
      `Realised loss is ${(dailyLoss * 100).toFixed(2)}% of equity, at or beyond the ${(policy.max_daily_loss * 100).toFixed(2)}% daily limit.`,
      Number(dailyLoss.toFixed(6)),
      policy.max_daily_loss,
    );
  }
  checks.push({
    rule: "daily_loss",
    passed: true,
    observed: Number(dailyLoss.toFixed(6)),
    limit: policy.max_daily_loss,
    detail: "Within the daily loss budget.",
  });

  const dd = drawdown();
  if (dd >= policy.max_drawdown) {
    return reject(
      "max_drawdown",
      `Drawdown is ${(dd * 100).toFixed(2)}%, at or beyond the ${(policy.max_drawdown * 100).toFixed(2)}% limit.`,
      Number(dd.toFixed(6)),
      policy.max_drawdown,
    );
  }
  checks.push({ rule: "max_drawdown", passed: true, observed: Number(dd.toFixed(6)), limit: policy.max_drawdown, detail: "Within the drawdown limit." });

  let notional = price * input.quantity;
  if (input.side === "buy" && !policy.leverage_enabled && notional > equity) {
    return reject(
      "leverage",
      `Order notional ${notional.toLocaleString("en-GB", { maximumFractionDigits: 2 })} exceeds equity ${equity.toLocaleString("en-GB", { maximumFractionDigits: 2 })} and leverage is disabled. The order would require borrowing.`,
      Number(notional.toFixed(2)),
      Number(equity.toFixed(2)),
    );
  }
  checks.push({
    rule: "leverage",
    passed: true,
    observed: policy.leverage_enabled,
    limit: false,
    detail: input.side === "buy" ? "No leverage required." : "A sell reduces exposure.",
  });

  const requestedRisk = notional * stopPct;
  const riskBudget = equity * policy.max_risk_per_trade;
  let approved = input.quantity;
  let decision: RiskDecision = "allow";
  let reason = "All risk rules passed.";

  if (input.side === "buy" && riskBudget > 0 && requestedRisk > riskBudget) {
    approved = riskBudget / (price * stopPct);
    if (approved <= 1e-12) {
      return reject(
        "per_trade_risk",
        "The risk budget is too small to size any position in this instrument.",
        Number(requestedRisk.toFixed(4)),
        Number(riskBudget.toFixed(4)),
      );
    }
    decision = "allow_reduced";
    reason = `Order reduced from ${input.quantity} to ${approved.toFixed(4)} ${input.symbol}: a stop at ${(stopPct * 100).toFixed(2)}% on the requested size would risk ${requestedRisk.toLocaleString("en-GB", { maximumFractionDigits: 2 })}, above the ${(policy.max_risk_per_trade * 100).toFixed(2)}% per-trade budget of ${riskBudget.toLocaleString("en-GB", { maximumFractionDigits: 2 })}.`;
    checks.push({
      rule: "per_trade_risk",
      passed: false,
      observed: Number(requestedRisk.toFixed(4)),
      limit: Number(riskBudget.toFixed(4)),
      detail: "Reduced to fit the per-trade risk budget.",
    });
    notional = price * approved;
  } else {
    checks.push({
      rule: "per_trade_risk",
      passed: true,
      observed: Number(requestedRisk.toFixed(4)),
      limit: Number(riskBudget.toFixed(4)),
      detail: "Within the per-trade risk budget.",
    });
  }

  const alreadyOpen = positions.some((position) => position.instrument_symbol === input.symbol);
  if (input.side === "buy" && !alreadyOpen && positions.length >= policy.max_positions) {
    return reject(
      "max_positions",
      `The account already holds ${positions.length} positions; the policy allows ${policy.max_positions}. A sell or an add to an existing position is still permitted.`,
      positions.length,
      policy.max_positions,
    );
  }
  checks.push({
    rule: "max_positions",
    passed: true,
    observed: positions.length,
    limit: policy.max_positions,
    detail: "Within the position count limit.",
  });

  const projected = equity > 0 ? (book.positions_value + (input.side === "buy" ? notional : 0)) / equity : 1;
  if (input.side === "buy" && projected > policy.max_exposure) {
    return reject(
      "max_exposure",
      `The order takes invested exposure to ${(projected * 100).toFixed(2)}% of equity, above the ${(policy.max_exposure * 100).toFixed(2)}% policy limit.`,
      Number(projected.toFixed(6)),
      policy.max_exposure,
    );
  }
  checks.push({
    rule: "max_exposure",
    passed: true,
    observed: Number(projected.toFixed(6)),
    limit: policy.max_exposure,
    detail: "Within the exposure limit.",
  });

  if (input.side === "buy") {
    const held = positions.find((position) => position.instrument_symbol === input.symbol);
    const current = held && held.market_value ? held.market_value : 0;
    const share = equity > 0 ? (current + notional) / equity : 1;
    if (share > policy.max_position_concentration) {
      return reject(
        "concentration",
        `${input.symbol} would be ${(share * 100).toFixed(2)}% of equity, above the ${(policy.max_position_concentration * 100).toFixed(2)}% single-instrument limit.`,
        Number(share.toFixed(6)),
        policy.max_position_concentration,
      );
    }
    checks.push({
      rule: "concentration",
      passed: true,
      observed: Number(share.toFixed(6)),
      limit: policy.max_position_concentration,
      detail: "Within the concentration limit.",
    });
  }

  if (input.side === "buy" && positions.length > 0) {
    // The demo book has no return history per position, so the correlation rule
    // reports that it could not be measured rather than inventing a number.
    const other = positions.find((position) => position.instrument_symbol !== input.symbol);
    checks.push({
      rule: "correlation",
      passed: true,
      observed: null,
      limit: policy.max_correlation,
      detail: other
        ? `Not measured in the demo: no stored return series for ${other.instrument_symbol}. The backend computes it.`
        : "Nothing held to correlate against.",
    });
  }

  if (PAPER_SPREAD_BPS > policy.max_spread_bps) {
    return reject(
      "spread",
      `The modelled execution spread of ${PAPER_SPREAD_BPS} bps exceeds the ${policy.max_spread_bps} bps limit.`,
      PAPER_SPREAD_BPS,
      policy.max_spread_bps,
    );
  }
  checks.push({
    rule: "spread",
    passed: true,
    observed: PAPER_SPREAD_BPS,
    limit: policy.max_spread_bps,
    detail: "Modelled execution spread within the limit.",
  });

  checks.unshift({
    rule: "data_freshness",
    passed: true,
    observed: Number(price.toFixed(6)),
    limit: null,
    detail: "Valuation price available.",
  });

  return { ...allow(), decision, approvedQuantity: approved, reason, checks };
}

/** Evaluate and record, exactly like the server's wrapper does. */
export function evaluateAndRecord(input: {
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  price?: number | null;
  stopPct?: number;
}): Evaluation {
  const state = load();
  const result = evaluate(input);
  const record: RiskCheckRecord = {
    id: `rc-${Math.random().toString(36).slice(2, 10)}`,
    order_id: null,
    instrument_symbol: input.symbol.toUpperCase(),
    side: input.side,
    requested_quantity: input.quantity,
    approved_quantity: result.approvedQuantity,
    decision: result.decision,
    reason: result.reason,
    breached_rules: result.breached,
    checks: result.checks as RiskRuleCheck[],
    equity_snapshot: Number(result.equity.toFixed(4)),
    exposure_snapshot: Number(result.exposure.toFixed(6)),
    open_positions_snapshot: result.openPositions,
    created_at: new Date().toISOString(),
  };
  state.checks.unshift(record);
  state.checks = state.checks.slice(0, 200);
  if (result.decision === "reject") {
    state.events.unshift(
      event("check_rejected", result.reason, "warning", { rules: result.breached }),
    );
  }
  save(state);
  return result;
}

export function status(): RiskStatus {
  const state = load();
  const book = execution.overview();
  return {
    breaker_state: state.breaker,
    halted_new_risk: state.breaker !== "closed",
    breaker_trigger: state.breakerTrigger || null,
    breaker_reason: state.breakerReason || null,
    breaker_since: state.breakerSince,
    equity: book.equity,
    exposure: Number(exposure().toFixed(6)),
    open_positions: openPositions().length,
    drawdown: Number(drawdown().toFixed(6)),
    daily_pnl: book.realised_pnl,
    daily_loss_fraction: 0,
    policy: {
      id: "demo-policy",
      name: "blueprint-default",
      version: state.policyVersion,
      is_active: true,
      ...state.policy,
      notes:
        "Illustrative paper-mode defaults from the blueprint: 0.5% risk per trade, 2% daily loss, 10% drawdown, 5 positions, 50% exposure, leverage disabled.",
    },
    applied_rules: APPLIED_RULES,
    advisory_rules: [],
    positions_affected: state.breaker === "closed" ? 0 : openPositions().length,
    orders_cancelled: 0,
    closes_positions: false,
  };
}

export function overview(): RiskOverview {
  const state = load();
  const rejected = state.checks.filter((check) => check.decision === "reject");
  const reduced = state.checks.filter((check) => check.decision === "allow_reduced");
  const counts: Record<string, number> = {};
  for (const check of rejected) {
    for (const rule of check.breached_rules) counts[rule] = (counts[rule] ?? 0) + 1;
  }
  return {
    breaker_state: state.breaker,
    checks_total: state.checks.length,
    rejected_total: rejected.length,
    reduced_total: reduced.length,
    rejection_rate: state.checks.length ? rejected.length / state.checks.length : 0,
    breach_counts: counts,
    policy_version: state.policyVersion,
    limits: { ...state.policy },
    recent_events: state.events.slice(0, 10),
    rules_supported: APPLIED_RULES,
    rules_not_implemented: [],
  };
}

export function checks(): RiskCheckRecord[] {
  return load().checks;
}

export function events(): RiskEventRecord[] {
  return load().events;
}

export function tripBreaker(reason: string, trigger = "emergency_stop"): RiskStatus {
  const state = load();
  state.breaker = "manual";
  state.breakerReason = reason;
  state.breakerTrigger = trigger;
  state.breakerSince = new Date().toISOString();
  state.events.unshift(
    event("breaker_tripped", reason, "critical", {
      trigger,
      positions_affected: openPositions().length,
      closing_positions: "not attempted: stopping new risk is separate from closing exposure",
    }),
  );
  save(state);
  return status();
}

export function clearBreaker(reason = "Cleared by an authorised user."): RiskStatus {
  const state = load();
  const previous = state.breaker;
  state.breaker = "closed";
  state.breakerReason = "";
  state.breakerTrigger = "";
  state.breakerSince = null;
  state.events.unshift(event("breaker_cleared", reason, previous === "manual" ? "warning" : "info", { previous }));
  save(state);
  return status();
}

export function resetDemo(): void {
  save(blank());
}
