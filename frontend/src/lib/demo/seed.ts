// Deterministic fixtures for the GitHub Pages demo.
// The demo engine implements the same contracts as the FastAPI backend so every
// screen, guardrail and empty state can be reviewed without a server.

export const DEMO_CREDENTIALS = {
  email: "demo@aurex.ai",
  password: "Demo!Aurex2026",
} as const;

export const ADMIN_CREDENTIALS = {
  email: "admin@aurex.ai",
  password: "ChangeMe!2026",
} as const;

export interface DemoMarketRow {
  symbol: string;
  name: string;
  assetClass: "Crypto" | "Forex" | "Equity" | "ETF";
  price: number;
  changePct: number;
  venue: string;
  session: "Open" | "Closed" | "24/7";
  spark: number[];
}

function walk(seed: number, points: number, drift = 0, volatility = 1): number[] {
  let value = 100;
  let state = seed;
  const out: number[] = [value];
  for (let i = 1; i < points; i += 1) {
    state = (state * 1103515245 + 12345) % 2147483648;
    const noise = (state / 2147483648 - 0.5) * 2 * volatility;
    value = value + noise + drift;
    out.push(Number(value.toFixed(3)));
  }
  return out;
}

export const DEMO_MARKETS: DemoMarketRow[] = [
  {
    symbol: "BTC/USD",
    name: "Bitcoin",
    assetClass: "Crypto",
    price: 91342.5,
    changePct: 2.41,
    venue: "Coinbase",
    session: "24/7",
    spark: walk(11, 40, 0.35, 2.1),
  },
  {
    symbol: "ETH/USD",
    name: "Ethereum",
    assetClass: "Crypto",
    price: 3041.18,
    changePct: -0.87,
    venue: "Coinbase",
    session: "24/7",
    spark: walk(23, 40, -0.1, 2.6),
  },
  {
    symbol: "GBP/USD",
    name: "Sterling / Dollar",
    assetClass: "Forex",
    price: 1.2684,
    changePct: 0.14,
    venue: "FX Interbank",
    session: "Open",
    spark: walk(37, 40, 0.02, 0.4),
  },
  {
    symbol: "EUR/USD",
    name: "Euro / Dollar",
    assetClass: "Forex",
    price: 1.0862,
    changePct: -0.22,
    venue: "FX Interbank",
    session: "Open",
    spark: walk(41, 40, -0.03, 0.35),
  },
  {
    symbol: "USD/JPY",
    name: "Dollar / Yen",
    assetClass: "Forex",
    price: 154.27,
    changePct: 0.51,
    venue: "FX Interbank",
    session: "Open",
    spark: walk(53, 40, 0.08, 0.9),
  },
  {
    symbol: "AAPL",
    name: "Apple Inc.",
    assetClass: "Equity",
    price: 231.44,
    changePct: 1.08,
    venue: "NASDAQ",
    session: "Open",
    spark: walk(67, 40, 0.12, 1.4),
  },
  {
    symbol: "SHEL.L",
    name: "Shell plc",
    assetClass: "Equity",
    price: 26.18,
    changePct: -0.63,
    venue: "LSE",
    session: "Open",
    spark: walk(71, 40, -0.05, 0.8),
  },
  {
    symbol: "VUSA.L",
    name: "Vanguard S&P 500 UCITS",
    assetClass: "ETF",
    price: 92.71,
    changePct: 0.94,
    venue: "LSE",
    session: "Open",
    spark: walk(83, 40, 0.09, 0.7),
  },
];

export interface DemoAgent {
  id: string;
  name: string;
  role: string;
  status: "idle" | "running" | "blocked" | "awaiting_data";
  phase: number;
  description: string;
  lastRun: string | null;
}

export const DEMO_AGENTS: DemoAgent[] = [
  {
    id: "orchestrator",
    name: "Chief AI Orchestrator",
    role: "Synthesis",
    status: "idle",
    phase: 5,
    description: "Schedules research, resolves conflicts and drafts trade/no-trade proposals.",
    lastRun: null,
  },
  {
    id: "technical",
    name: "Technical Analyst",
    role: "Price structure",
    status: "awaiting_data",
    phase: 5,
    description: "Reads OHLCV, volume, momentum and volatility to describe the technical setup.",
    lastRun: null,
  },
  {
    id: "fundamental",
    name: "Fundamental Analyst",
    role: "Filings & valuation",
    status: "awaiting_data",
    phase: 4,
    description: "Extracts reported figures from filings and earnings with source citations.",
    lastRun: null,
  },
  {
    id: "news",
    name: "News Analyst",
    role: "Events",
    status: "awaiting_data",
    phase: 4,
    description: "Summarises official releases and news with freshness and source-quality ratings.",
    lastRun: null,
  },
  {
    id: "sentiment",
    name: "Sentiment Analyst",
    role: "Positioning",
    status: "blocked",
    phase: 4,
    description: "Measures sentiment and crowding. Requires a licensed data provider.",
    lastRun: null,
  },
  {
    id: "quant",
    name: "Quantitative Analyst",
    role: "Probability",
    status: "blocked",
    phase: 6,
    description: "Produces calibrated forecasts with uncertainty bounds and validation evidence.",
    lastRun: null,
  },
  {
    id: "regime",
    name: "Market Regime Analyst",
    role: "Regime detection",
    status: "blocked",
    phase: 6,
    description: "Classifies trend/range/volatility regimes and raises drift warnings.",
    lastRun: null,
  },
  {
    id: "portfolio",
    name: "Portfolio Analyst",
    role: "Allocation",
    status: "awaiting_data",
    phase: 7,
    description: "Proposes allocation changes within correlation and concentration constraints.",
    lastRun: null,
  },
  {
    id: "risk",
    name: "Independent Risk Controller",
    role: "Deterministic veto",
    status: "idle",
    phase: 9,
    description: "Independent of the AI. Approves or rejects every proposal before execution.",
    lastRun: null,
  },
];

export interface DemoStrategy {
  id: string;
  name: string;
  family: "Momentum" | "Mean reversion" | "Carry" | "Volatility" | "Multi-factor";
  status: "draft" | "backtest_ready" | "validated" | "retired";
  phase: number;
  instruments: string[];
  description: string;
  hypothesis: string;
  sharpe: number | null;
  maxDrawdown: number | null;
}

export const DEMO_STRATEGIES: DemoStrategy[] = [
  {
    id: "trend-following-v1",
    name: "Trend Following v1",
    family: "Momentum",
    status: "draft",
    phase: 5,
    instruments: ["GBP/USD", "EUR/USD", "BTC/USD"],
    description: "Multi-timeframe trend continuation with volatility-scaled stops.",
    hypothesis:
      "Persistent intraday momentum in liquid FX and crypto pairs survives transaction costs when filtered by realised-volatility regime.",
    sharpe: null,
    maxDrawdown: null,
  },
  {
    id: "cross-sectional-momentum",
    name: "Cross-Sectional Momentum",
    family: "Multi-factor",
    status: "draft",
    phase: 6,
    instruments: ["AAPL", "SHEL.L", "VUSA.L"],
    description: "Ranked 60-day relative strength with sector caps.",
    hypothesis:
      "Equities in the top momentum decile outperform the index after costs over 20–60 day holding periods.",
    sharpe: null,
    maxDrawdown: null,
  },
  {
    id: "mean-reversion-v2",
    name: "Mean Reversion v2",
    family: "Mean reversion",
    status: "draft",
    phase: 6,
    instruments: ["EUR/USD", "USD/JPY"],
    description: "Bollinger-band fade inside confirmed range regimes.",
    hypothesis:
      "Short-horizon overextensions in range-bound FX revert to the 20-period mean beyond spread cost.",
    sharpe: null,
    maxDrawdown: null,
  },
  {
    id: "vol-targeting-overlay",
    name: "Volatility Targeting Overlay",
    family: "Volatility",
    status: "draft",
    phase: 9,
    instruments: ["Portfolio"],
    description: "Scales exposure inversely to forecast volatility.",
    hypothesis:
      "Targeting constant portfolio volatility improves risk-adjusted returns and reduces drawdown depth.",
    sharpe: null,
    maxDrawdown: null,
  },
];

export interface DemoPhase {
  id: number;
  name: string;
  scope: string;
  status: "complete" | "in_progress" | "planned";
  deliverable: string;
}

export const DEMO_PHASES: DemoPhase[] = [
  {
    id: 1,
    name: "Foundation",
    scope: "Repository, Docker, Postgres, auth, migrations, API structure",
    status: "complete",
    deliverable: "Documented setup, secure auth, double-entry ledger, design system, dashboard.",
  },
  {
    id: 2,
    name: "Frontend",
    scope: "Design system, navigation, dashboard and account screens",
    status: "complete",
    deliverable: "Full terminal shell across all eleven product areas.",
  },
  {
    id: 3,
    name: "Market data",
    scope: "Instrument registry, provider adapters, historical data, streaming",
    status: "planned",
    deliverable: "Live candlestick feeds with data-freshness and quality events.",
  },
  {
    id: 4,
    name: "Research",
    scope: "News ingestion, document extraction, RAG, source-linked reports",
    status: "planned",
    deliverable: "Uploadable reports with verifiable citations and evidence freshness.",
  },
  {
    id: 5,
    name: "AI agents",
    scope: "Orchestrator, specialist agents, structured outputs",
    status: "planned",
    deliverable: "Evidence-backed findings and trade/no-trade proposals.",
  },
  {
    id: 6,
    name: "Quant models",
    scope: "Feature engineering, training, validation, drift monitoring",
    status: "planned",
    deliverable: "Calibrated models with walk-forward evaluation records.",
  },
  {
    id: 7,
    name: "Backtesting",
    scope: "Event-driven simulator, costs, slippage, out-of-sample evaluation",
    status: "planned",
    deliverable: "Cost-aware performance evidence per strategy version.",
  },
  {
    id: 8,
    name: "Paper execution",
    scope: "Order lifecycle, simulated fills, positions, accounting",
    status: "planned",
    deliverable: "Realistic paper fills reconciled against the ledger.",
  },
  {
    id: 9,
    name: "Risk engine",
    scope: "Limits, circuit breakers, emergency shutdown",
    status: "planned",
    deliverable: "Independent deterministic veto and auditable risk events.",
  },
  {
    id: 10,
    name: "Analytics",
    scope: "Performance metrics, explanations, reports",
    status: "planned",
    deliverable: "Sharpe, Sortino, drawdown, attribution and decision replay.",
  },
  {
    id: 11,
    name: "Security & reliability",
    scope: "Security testing, recovery, monitoring, load testing",
    status: "planned",
    deliverable: "Hardening evidence and incident-response runbooks.",
  },
  {
    id: 12,
    name: "Readiness",
    scope: "Broker sandbox, compliance assessment, release preparation",
    status: "planned",
    deliverable: "Separately gated live-trading readiness review.",
  },
];

export const DEMO_RISK_LIMITS = [
  { label: "Maximum risk per trade", value: "0.50% of equity", enforced: true },
  { label: "Maximum daily loss", value: "2.00%", enforced: true },
  { label: "Maximum drawdown", value: "10.00%", enforced: true },
  { label: "Maximum concurrent positions", value: "5", enforced: true },
  { label: "Maximum portfolio exposure", value: "50.00%", enforced: true },
  { label: "Leverage", value: "Disabled", enforced: true },
  { label: "Data freshness", value: "Reject stale quotes", enforced: true },
  { label: "Spread / slippage guard", value: "Reject above threshold", enforced: true },
  { label: "Correlation cap", value: "Limit clustered exposure", enforced: true },
];
