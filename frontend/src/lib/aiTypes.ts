// AI orchestration contracts — mirrors backend `app/schemas/ai.py`.

export type Stance = "bullish" | "bearish" | "neutral" | "unknown";
export type RunStatus = "pending" | "running" | "completed" | "partial" | "failed" | "refused";
export type ConfidenceKind = "rule_score" | "calibrated" | "uncalibrated";
export type EvidenceKind =
  | "research_claim"
  | "market_candle"
  | "indicator"
  | "quality_event"
  | "computation"
  | "absence";

export interface Agent {
  code: string;
  name: string;
  role: string;
  description: string;
  status: string;
  implemented: boolean;
  deterministic: boolean;
  version: string;
  inputs: string;
  outputs: string;
}

export interface Evidence {
  kind: EvidenceKind;
  label: string;
  detail: string;
  value: number | null;
  unit: string;
  reference_id: string | null;
  url: string;
  observed_at: string | null;
}

export interface Finding {
  agent_code: string;
  ordinal: number;
  headline: string;
  stance: Stance;
  confidence: number;
  confidence_kind: ConfidenceKind;
  facts: string;
  interpretation: string;
  uncertainties: string;
  limitations: string;
  data_available: boolean;
  duration_ms: number;
  evidence: Evidence[];
}

export interface AgentRun {
  id: string;
  instrument_symbol: string;
  timeframe: string;
  trigger: string;
  status: RunStatus;
  question: string;
  proposal_stance: Stance;
  proposal_confidence: number | null;
  proposal_conflict: boolean;
  proposal_reason: string;
  tradeable: boolean;
  blocked_reason: string;
  duration_ms: number;
  started_at: string | null;
  finished_at: string | null;
  error: string;
  created_at: string;
  findings?: Finding[];
}

export interface AIOverview {
  runs_total: number;
  runs_completed: number;
  runs_refused: number;
  runs_partial: number;
  agents_implemented: number;
  agents_total: number;
  tradeable_proposals: number;
  latest_runs: AgentRun[];
  stance_breakdown: Record<string, number>;
}
