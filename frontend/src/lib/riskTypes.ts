// Risk contracts — mirrors backend `app/schemas/risk.py`.

export type BreakerState = "closed" | "open" | "manual";
export type RiskDecision = "allow" | "allow_reduced" | "reject";

export interface RiskPolicy {
  id: string;
  name: string;
  version: number;
  is_active: boolean;
  max_risk_per_trade: number;
  max_daily_loss: number;
  max_drawdown: number;
  max_positions: number;
  max_exposure: number;
  max_position_concentration: number;
  max_correlation: number;
  max_spread_bps: number;
  max_orders_per_hour: number;
  leverage_enabled: boolean;
  notes: string;
}

export interface RiskStatus {
  breaker_state: BreakerState;
  halted_new_risk: boolean;
  breaker_trigger: string | null;
  breaker_reason: string | null;
  breaker_since: string | null;
  equity: number;
  exposure: number;
  open_positions: number;
  drawdown: number;
  daily_pnl: number;
  daily_loss_fraction: number;
  policy: RiskPolicy;
  applied_rules: string[];
  advisory_rules: string[];
  positions_affected: number;
  orders_cancelled: number;
  closes_positions: boolean;
}

export interface RiskRuleCheck {
  rule: string;
  passed: boolean;
  observed: number | string | boolean | null;
  limit: number | string | boolean | null;
  detail: string;
}

export interface RiskCheckRecord {
  id: string;
  order_id: string | null;
  instrument_symbol: string;
  side: string;
  requested_quantity: number;
  approved_quantity: number;
  decision: RiskDecision;
  reason: string;
  breached_rules: string[];
  checks: RiskRuleCheck[];
  equity_snapshot: number;
  exposure_snapshot: number;
  open_positions_snapshot: number;
  created_at: string;
}

export interface RiskEventRecord {
  id: string;
  event_type: string;
  severity: "info" | "warning" | "critical";
  message: string;
  detail: Record<string, unknown>;
  created_at: string;
}

export interface RiskOverview {
  breaker_state: BreakerState;
  checks_total: number;
  rejected_total: number;
  reduced_total: number;
  rejection_rate: number;
  breach_counts: Record<string, number>;
  policy_version: number;
  limits: Record<string, number | boolean>;
  recent_events: RiskEventRecord[];
  rules_supported: string[];
  rules_not_implemented: string[];
}

export interface RiskEvaluateResult {
  decision: RiskDecision;
  approved_quantity: number;
  reason: string;
  breached_rules: string[];
  checks: RiskRuleCheck[];
  equity: number;
  exposure: number;
  open_positions: number;
}
