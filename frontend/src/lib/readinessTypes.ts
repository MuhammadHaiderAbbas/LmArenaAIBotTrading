// Readiness contracts — mirrors backend `app/services/compliance.py` and `release.py`.

export type ReadinessStatus = "done" | "partial" | "blocked" | "not_implemented";

export interface ReadinessItem {
  key: string;
  title: string;
  category: string;
  status: ReadinessStatus;
  evidence: string;
  blocks_live: boolean;
  unblocked_by: string | null;
}

export interface BlockingItem {
  key: string;
  title: string;
  unblocked_by: string | null;
}

export interface LiveTradingStatement {
  live_trading_flag: boolean;
  real_money_flag: boolean;
  live_adapter_registered: boolean;
  reachable: boolean;
  reason: string;
}

export interface ComplianceAssessment {
  generated_at: string;
  scope: string;
  items: ReadinessItem[];
  counts: Record<ReadinessStatus, number>;
  blocking_for_live: BlockingItem[];
  live_ready: boolean;
  live_ready_reason: string;
  live_trading: LiveTradingStatement;
}

export interface GateCheck {
  check: string;
  passed: boolean;
  detail: string;
  blocking?: boolean;
}

export interface ReleaseGate {
  verdict: "pass" | "fail";
  scope: string;
  checks: GateCheck[];
  blocking_checks: string[];
  compliance_blocking_count: number;
  evaluated_at: string;
  statement: string;
}

export interface BrokerDescriptor {
  key: string;
  name: string;
  sandbox_supported: boolean;
  live_supported: boolean;
  credential_fields: string[];
  notes: string;
}

export interface BrokerCatalogue {
  brokers: BrokerDescriptor[];
  live_trading: LiveTradingStatement;
}

export interface BrokerSessionRow {
  stage: string;
  ok: boolean;
  detail: string;
  latency_ms: number | null;
  clock_skew_ms: number | null;
  occurred_at: string;
}

export interface BrokerConnection {
  id: string;
  broker: string;
  label: string;
  mode: string;
  status: string;
  withdraw_allowed: boolean;
  scopes: string;
  ip_allowlist: string;
  has_credentials: boolean;
  last_connected_at: string | null;
  last_error: string | null;
  sessions: BrokerSessionRow[];
  live_trading: LiveTradingStatement;
  settings_live_flag: boolean;
}

export interface StageEntry {
  key: string;
  label: string;
  status: string;
  requirement: string;
  evidence?: GateCheck[];
}

export interface StageReport {
  current: {
    stage: string;
    label: string;
    since: string | null;
    next: string;
    next_reachable: boolean;
    notes: string;
  };
  stages: StageEntry[];
  checked_at: string;
}
