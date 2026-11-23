/**
 * Demo implementation of the AI orchestration layer.
 *
 * The rules are the backend's rules, in miniature: every finding carries
 * evidence, missing data is recorded as an absence rather than a neutral stance,
 * nothing is tradeable, and confidence is a labelled rule score. The numbers come
 * from the deterministic demo market engine, so two runs of the same instrument
 * agree.
 *
 * Nothing here is a model. It is a small rule set over synthetic prices, and the
 * UI says so.
 */

import { candlesForSymbol } from "./marketEngine";
import type {
  Agent,
  AgentRun,
  AIOverview,
  Evidence,
  Finding,
  Stance,
} from "../aiTypes";

export const DEMO_AGENTS: Agent[] = [
  {
    code: "technical",
    name: "Technical Analyst",
    role: "technical",
    description: "Trend, momentum and volatility from price structure and indicators.",
    status: "active",
    implemented: true,
    deterministic: true,
    version: "1.0.0",
    inputs: "OHLCV candles, SMA/EMA/RSI/MACD/ATR/Bollinger",
    outputs: "Directional stance from indicator agreement, with evidence per indicator",
  },
  {
    code: "regime",
    name: "Market Regime Analyst",
    role: "regime",
    description: "Volatility regime and drift relative to the instrument's own history.",
    status: "active",
    implemented: true,
    deterministic: true,
    version: "1.0.0",
    inputs: "Realised volatility series over the instrument's history",
    outputs: "Regime classification (high/normal/low) and drift, with no direction",
  },
  {
    code: "news",
    name: "News Analyst",
    role: "news",
    description: "Summarises verified research evidence and source quality. Reports no stance.",
    status: "active",
    implemented: true,
    deterministic: true,
    version: "1.0.0",
    inputs: "Phase 4 reports, claims, verdicts and source trust tiers",
    outputs: "Evidence summary with verification coverage and freshness limits",
  },
  {
    code: "portfolio",
    name: "Portfolio Analyst",
    role: "portfolio",
    description: "Exposure and concentration for the attached paper account.",
    status: "active",
    implemented: true,
    deterministic: true,
    version: "1.0.0",
    inputs: "Reconciled ledger balances and open positions",
    outputs: "Exposure multiple and concentration notes",
  },
  {
    code: "quality",
    name: "Data Quality Monitor",
    role: "risk",
    description: "Surfaces stale, gapped or outlier market data as a finding in its own right.",
    status: "active",
    implemented: true,
    deterministic: true,
    version: "1.0.0",
    inputs: "Phase 3 quality events",
    outputs: "Quality state and the specific events behind it",
  },
  {
    code: "fundamental",
    name: "Fundamental Analyst",
    role: "fundamental",
    description: "Filings, earnings and valuation context from SEC and macro data.",
    status: "shadow",
    implemented: false,
    deterministic: true,
    version: "1.0.0",
    inputs: "SEC filings, financial statements",
    outputs: "Valuation and financial-health context",
  },
  {
    code: "sentiment",
    name: "Sentiment Analyst",
    role: "sentiment",
    description: "Licensed social and positioning signals with crowding indicators.",
    status: "shadow",
    implemented: false,
    deterministic: true,
    version: "1.0.0",
    inputs: "Licensed sentiment and positioning feeds",
    outputs: "Sentiment measures, crowding flags",
  },
  {
    code: "quantitative",
    name: "Quantitative Analyst",
    role: "quantitative",
    description: "Model forecasts with calibration and uncertainty from Phase 6 models.",
    status: "shadow",
    implemented: false,
    deterministic: true,
    version: "1.0.0",
    inputs: "Validated model outputs and feature sets",
    outputs: "Forecasts, uncertainty, calibration evidence",
  },
];

export const DEMO_AI_NOTE =
  "Demo mode: agents run locally over the synthetic price series. No language model is involved, and no proposal is tradeable.";

function sma(values: number[], period: number): number | null {
  if (values.length < period) return null;
  const window = values.slice(-period);
  return window.reduce((total, value) => total + value, 0) / period;
}

function rsi(values: number[], period = 14): number | null {
  if (values.length < period + 1) return null;
  let gains = 0;
  let losses = 0;
  for (let index = values.length - period; index < values.length; index += 1) {
    const change = values[index] - values[index - 1];
    if (change >= 0) gains += change;
    else losses -= change;
  }
  if (losses === 0) return 100;
  const rs = gains / period / (losses / period);
  return 100 - 100 / (1 + rs);
}

function volatility(values: number[], period = 20): number | null {
  if (values.length < period + 1) return null;
  const returns: number[] = [];
  for (let index = values.length - period; index < values.length; index += 1) {
    returns.push(Math.log(values[index] / values[index - 1]));
  }
  const mean = returns.reduce((total, value) => total + value, 0) / returns.length;
  const variance =
    returns.reduce((total, value) => total + (value - mean) ** 2, 0) / returns.length;
  return Math.sqrt(variance) * Math.sqrt(252) * 100;
}

function closesFor(symbol: string): number[] {
  const candles = candlesForSymbol(symbol, "1d", 180);
  if (!candles) return [];
  return candles.map((candle) => candle.close);
}

const clamp = (value: number) => Math.max(0, Math.min(1, value));

export function technicalFinding(symbol: string): Finding {
  const closes = closesFor(symbol);
  if (closes.length < 20) {
    return {
      agent_code: "technical",
      ordinal: 0,
      headline: "No price history available for this instrument",
      stance: "unknown",
      confidence: 0,
      confidence_kind: "rule_score",
      facts: "",
      interpretation: "",
      uncertainties: "",
      limitations: "Technical analysis requires price history; none was present.",
      data_available: false,
      duration_ms: 3,
      evidence: [
        {
          kind: "absence",
          label: "market candles",
          detail:
            "No candles are stored for this instrument and timeframe, so no technical read is possible. This is a data gap, not a neutral signal.",
          value: null,
          unit: "",
          reference_id: null,
          url: "",
          observed_at: null,
        },
      ],
    };
  }

  const price = closes[closes.length - 1];
  const fast = sma(closes, 20)!;
  const slow = sma(closes, 50);
  const momentum = rsi(closes);
  const vol = volatility(closes);

  const facts = [
    `Last close ${price.toFixed(4)}`,
    `${closes.length} bars analysed at 1d`,
    `SMA(20) ${fast.toFixed(4)}`,
  ];
  const evidence: Evidence[] = [
    {
      kind: "market_candle",
      label: "last close",
      detail: `${symbol} 1d close`,
      value: price,
      unit: "price",
      reference_id: null,
      url: "",
      observed_at: null,
    },
    {
      kind: "indicator",
      label: "SMA(20)",
      detail: `close is ${(((price - fast) / fast) * 100).toFixed(2)}% versus the 20-period mean`,
      value: fast,
      unit: "price",
      reference_id: null,
      url: "",
      observed_at: null,
    },
  ];

  let score = Math.sign(price - fast) * clamp(Math.abs((price - fast) / fast) * 25);
  let weight = 1;
  if (slow) {
    facts.push(`SMA(50) ${slow.toFixed(4)}`);
    score += Math.sign(fast - slow) * clamp(Math.abs((fast - slow) / slow) * 40);
    weight += 1;
    evidence.push({
      kind: "indicator",
      label: "SMA(20) vs SMA(50)",
      detail: fast > slow ? "20-period mean above 50-period mean" : "20-period mean below 50-period mean",
      value: ((fast - slow) / slow) * 100,
      unit: "%",
      reference_id: null,
      url: "",
      observed_at: null,
    });
  }
  if (momentum !== null) {
    facts.push(`RSI(14) ${momentum.toFixed(1)}`);
    evidence.push({
      kind: "indicator",
      label: "RSI(14)",
      detail: "momentum oscillator on 14 periods",
      value: momentum,
      unit: "index",
      reference_id: null,
      url: "",
      observed_at: null,
    });
  }

  const normalised = score / weight;
  const stance: Stance = normalised > 0.15 ? "bullish" : normalised < -0.15 ? "bearish" : "neutral";
  let confidence = clamp(0.35 + Math.abs(normalised) * 0.45);
  if (!slow) confidence = Math.min(confidence, 0.45);

  const uncertainties: string[] = [];
  if (momentum !== null && (momentum >= 70 || momentum <= 30)) {
    uncertainties.push(
      `RSI ${momentum.toFixed(1)} is ${momentum >= 70 ? "overbought" : "oversold"}; momentum is extended.`,
    );
    confidence -= 0.05;
  }
  if (vol !== null && vol > 40) {
    uncertainties.push(`Realised volatility ${vol.toFixed(1)}% is high; stops implied by this are wide.`);
    confidence -= 0.05;
  }

  return {
    agent_code: "technical",
    ordinal: 0,
    headline:
      stance === "bullish"
        ? "Trend indicators lean constructive"
        : stance === "bearish"
          ? "Trend indicators lean defensive"
          : "Trend indicators are mixed",
    stance,
    confidence: clamp(confidence),
    confidence_kind: "rule_score",
    facts: facts.join("\n"),
    interpretation:
      "Indicator agreement is measured, not predicted: this describes recent price structure and makes no claim about future returns.",
    uncertainties: uncertainties.join("\n"),
    limitations:
      "Rule-based scoring over indicators; not validated against forward returns. Indicators are lagging by construction.",
    data_available: true,
    duration_ms: 4,
    evidence,
  };
}

export function regimeFinding(symbol: string): Finding {
  const closes = closesFor(symbol);
  const vol = volatility(closes);
  if (closes.length < 21 || vol === null) {
    return {
      agent_code: "regime",
      ordinal: 1,
      headline: "Not enough history to classify volatility",
      stance: "unknown",
      confidence: 0,
      confidence_kind: "rule_score",
      facts: `${closes.length} closes analysed`,
      interpretation: "",
      uncertainties: "",
      limitations: "Realised volatility needs at least 21 closes.",
      data_available: false,
      duration_ms: 2,
      evidence: [
        {
          kind: "absence",
          label: "market candles",
          detail: "Regime classification needs a price series; none is stored.",
          value: null,
          unit: "",
          reference_id: null,
          url: "",
          observed_at: null,
        },
      ],
    };
  }

  const state = vol > 35 ? "high" : vol < 12 ? "low" : "normal";
  const drift = ((closes[closes.length - 1] - closes[closes.length - 20]) / closes[closes.length - 20]) * 100;

  return {
    agent_code: "regime",
    ordinal: 1,
    headline:
      state === "high"
        ? "High-volatility regime"
        : state === "low"
          ? "Low-volatility regime"
          : "Volatility is within its normal range for this instrument",
    stance: "unknown",
    confidence: 0.62,
    confidence_kind: "rule_score",
    facts: [
      `${closes.length} closes analysed`,
      `Realised 20-bar volatility ${vol.toFixed(2)}%`,
      `Net move over last 20 bars ${drift >= 0 ? "+" : ""}${drift.toFixed(2)}%`,
    ].join("\n"),
    interpretation:
      `The current window is classified '${state}' against this instrument's own recent volatility. A regime is not a direction.`,
    uncertainties:
      state === "high"
        ? "High volatility widens the range of plausible outcomes; position sizing, not direction, is the relevant response."
        : "",
    limitations:
      "Single-instrument volatility only; cross-asset correlation regimes are not modelled.",
    data_available: true,
    duration_ms: 3,
    evidence: [
      {
        kind: "indicator",
        label: "realised volatility",
        detail: "standard deviation of 20 log returns, annualised",
        value: vol,
        unit: "%",
        reference_id: null,
        url: "",
        observed_at: null,
      },
      {
        kind: "computation",
        label: "20-bar drift",
        detail: "percentage change from the 20th-most-recent close to the last close",
        value: drift,
        unit: "%",
        reference_id: null,
        url: "",
        observed_at: null,
      },
    ],
  };
}

export function newsFinding(symbol: string, claims: Array<{ text: string; verdict: string; url: string }>): Finding {
  if (!claims.length) {
    return {
      agent_code: "news",
      ordinal: 2,
      headline: "No relevant research evidence was retrieved",
      stance: "unknown",
      confidence: 0,
      confidence_kind: "rule_score",
      facts: "",
      interpretation: "",
      uncertainties: "",
      limitations: "The research corpus is empty for this query; no conclusion can be drawn.",
      data_available: false,
      duration_ms: 5,
      evidence: [
        {
          kind: "absence",
          label: "research corpus",
          detail:
            "No ingested document matched this instrument or topic. Absence of evidence is recorded as absence, not as a neutral read.",
          value: null,
          unit: "",
          reference_id: null,
          url: "",
          observed_at: null,
        },
      ],
    };
  }

  const verified = claims.filter((claim) => claim.verdict === "supported");
  const unverified = claims.filter((claim) => claim.verdict !== "supported");
  const coverage = claims.length ? verified.length / claims.length : 0;

  return {
    agent_code: "news",
    ordinal: 2,
    headline: `${verified.length} verified claim(s) across the research corpus for ${symbol}`,
    stance: "unknown",
    confidence: clamp(coverage * 0.5 + 0.3),
    confidence_kind: "rule_score",
    facts: [
      `${claims.length} claim(s) retrieved`,
      `${verified.length} verified verbatim against their source`,
      `${unverified.length} unverified`,
    ].join("\n"),
    interpretation:
      "This agent summarises what sources say and how well it is verified. It does not translate text into a bullish or bearish view: that requires a model validated against outcomes, which Phase 5 does not have.",
    uncertainties: coverage < 0.5 ? `Only ${(coverage * 100).toFixed(0)}% of claims verified; coverage is thin.` : "",
    limitations: "Retrieval is lexical with declared synonyms; genuine paraphrase may be missed.",
    data_available: true,
    duration_ms: 6,
    evidence: verified.slice(0, 4).map((claim) => ({
      kind: "research_claim" as const,
      label: claim.text.slice(0, 180),
      detail: "verdict: supported",
      value: null,
      unit: "",
      reference_id: null,
      url: claim.url,
      observed_at: null,
    })),
  };
}

export function portfolioFinding(): Finding {
  return {
    agent_code: "portfolio",
    ordinal: 3,
    headline: "No account context supplied",
    stance: "unknown",
    confidence: 0,
    confidence_kind: "rule_score",
    facts: "",
    interpretation: "",
    uncertainties: "",
    limitations: "No account was attached to this run, so no exposure analysis is possible.",
    data_available: false,
    duration_ms: 2,
    evidence: [
      {
        kind: "absence",
        label: "account state",
        detail: "No account was attached to this run, so no exposure analysis is possible.",
        value: null,
        unit: "",
        reference_id: null,
        url: "",
        observed_at: null,
      },
    ],
  };
}

export function qualityFinding(): Finding {
  const state = "ok";
  return {
    agent_code: "quality",
    ordinal: 4,
    headline: "Market data is clean",
    stance: "unknown",
    confidence: 0.9,
    confidence_kind: "rule_score",
    facts: `Quality state: ${state}\nNewest bar is within the expected window`,
    interpretation: "Quality is reported before any other agent's evidence is trusted.",
    uncertainties: "",
    limitations: "Quality checks are structural (gaps, staleness, outliers), not correctness checks.",
    data_available: true,
    duration_ms: 2,
    evidence: [
      {
        kind: "computation",
        label: "no quality events",
        detail: "Ingestion recorded no gaps, duplicates, outliers or stale windows for this instrument.",
        value: null,
        unit: "",
        reference_id: null,
        url: "",
        observed_at: null,
      },
    ],
  };
}

export interface DemoRunInput {
  symbol: string;
  timeframe?: string;
  question?: string;
  claims?: Array<{ text: string; verdict: string; url: string }>;
}

export function buildRun(input: DemoRunInput): AgentRun {
  const findings: Finding[] = [
    technicalFinding(input.symbol),
    regimeFinding(input.symbol),
    newsFinding(input.symbol, input.claims ?? []),
    portfolioFinding(),
    qualityFinding(),
  ];

  const technical = findings[0];
  const regime = findings[1];
  const directional = technical.data_available;

  let status: AgentRun["status"] = "completed";
  let stance: Stance = "unknown";
  let confidence: number | null = null;
  let conflict = false;
  let reason = "";
  let blockedReason = "";

  if (!directional) {
    status = "refused";
    reason =
      "No directional agent produced a usable finding; the run declines to propose a stance.";
    blockedReason = "no directional evidence";
  } else {
    const parts = [`Technical: ${technical.headline.toLowerCase()} (${technical.stance}).`];
    if (regime.data_available && regime.headline.includes("High-volatility")) {
      conflict = true;
      parts.push("Regime: volatility is elevated, which argues against acting on a trend signal alone.");
    }
    if (technical.stance === "neutral") {
      stance = "neutral";
      confidence = technical.confidence * 0.6;
      status = "partial";
      reason = `${parts.join(" ")} Indicators do not agree enough to propose a direction.`;
      blockedReason = "indicators are mixed";
    } else {
      stance = technical.stance;
      confidence = technical.confidence * (conflict ? 0.6 : 1);
      reason = parts.join(" ");
      blockedReason =
        "Execution is not enabled: the independent risk controller arrives in Phase 9, and no order path exists in Phase 5.";
      status = findings.some((finding) => !finding.data_available) ? "partial" : "completed";
    }
  }

  const created = "2026-11-23T09:30:00Z";
  return {
    id: `run-${input.symbol.replace(/[^A-Za-z0-9]/g, "")}-${Math.round((confidence ?? 0) * 1000)}`,
    instrument_symbol: input.symbol,
    timeframe: input.timeframe ?? "1d",
    trigger: "manual",
    status,
    question: input.question ?? "",
    proposal_stance: stance,
    proposal_confidence: confidence === null ? null : Math.round(confidence * 10000) / 10000,
    proposal_conflict: conflict,
    proposal_reason: reason,
    tradeable: false,
    blocked_reason: blockedReason || "Execution is not enabled in Phase 5.",
    duration_ms: 22,
    started_at: created,
    finished_at: created,
    error: "",
    created_at: created,
    findings,
  };
}

const HISTORY: AgentRun[] = [
  buildRun({ symbol: "GBP/USD" }),
  buildRun({ symbol: "SPY" }),
  buildRun({ symbol: "BTC/USD" }),
];

export const demoAiApi = {
  async agents(): Promise<Agent[]> {
    return DEMO_AGENTS;
  },
  async overview(): Promise<AIOverview> {
    return {
      runs_total: HISTORY.length,
      runs_completed: HISTORY.filter((run) => run.status === "completed").length,
      runs_refused: HISTORY.filter((run) => run.status === "refused").length,
      runs_partial: HISTORY.filter((run) => run.status === "partial").length,
      agents_implemented: DEMO_AGENTS.filter((agent) => agent.implemented).length,
      agents_total: DEMO_AGENTS.length,
      tradeable_proposals: 0,
      latest_runs: HISTORY,
      stance_breakdown: HISTORY.reduce<Record<string, number>>((accumulator, run) => {
        accumulator[run.proposal_stance] = (accumulator[run.proposal_stance] ?? 0) + 1;
        return accumulator;
      }, {}),
    };
  },
  async runs(): Promise<AgentRun[]> {
    return HISTORY;
  },
  async run(input: DemoRunInput): Promise<AgentRun> {
    return buildRun(input);
  },
};
