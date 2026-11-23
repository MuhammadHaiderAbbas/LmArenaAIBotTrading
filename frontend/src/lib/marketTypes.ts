// Market-data contracts — mirrors backend `app/schemas/market.py`.
//
// Kept in sync by hand: the demo build has no server to introspect, so the
// shapes here are the contract both transports must satisfy.

export type AssetClass = "crypto" | "equity" | "etf" | "forex";
export type Timeframe = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d" | "1w";
export type SessionState = "open" | "closed" | "pre_open" | "post_close" | "always_on";
export type QualitySeverity = "info" | "warning" | "critical";

export interface Venue {
  code: string;
  name: string;
  asset_class: AssetClass;
  timezone: string;
  session_open: string | null;
  session_close: string | null;
  is_24_7: boolean;
}

export interface MarketSession {
  venue: string;
  state: SessionState;
  timezone: string;
  local_time: string;
  is_open: boolean;
  session_open: string | null;
  session_close: string | null;
}

export interface Quote {
  bid: number | null;
  ask: number | null;
  mid: number | null;
  last: number;
  open_24h: number | null;
  change_24h_pct: number | null;
  volume_24h: number | null;
  spread_bps: number | null;
  provider: string;
  observed_at: string;
  received_at: string;
  age_seconds: number;
}

export interface Candle {
  open_time: string;
  close_time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap: number | null;
  is_final: boolean;
  provider: string;
}

export interface Indicators {
  sma_20: number | null;
  sma_50: number | null;
  ema_20: number | null;
  rsi_14: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_histogram: number | null;
  atr_14: number | null;
  bb_upper: number | null;
  bb_middle: number | null;
  bb_lower: number | null;
  volatility_20: number | null;
  change_pct_1: number | null;
  change_pct_5: number | null;
  change_pct_20: number | null;
}

export interface Instrument {
  id: string;
  symbol: string;
  name: string;
  asset_class: AssetClass;
  base_currency: string;
  quote_currency: string;
  price_scale: number;
  tick_size: number;
  lot_size: number;
  status: string;
  is_tradable: boolean;
  description: string;
  venue: Venue;
  providers: string[];
}

export type DataQuality = "ok" | "warning" | "critical" | "no_data";

export interface MarketRow {
  instrument: Instrument;
  session: MarketSession;
  quote: Quote | null;
  indicators: Indicators | null;
  sparkline: number[];
  data_quality: DataQuality;
}

export interface CandleSeries {
  instrument: Instrument;
  timeframe: Timeframe;
  candles: Candle[];
  indicators: Indicators;
  session: MarketSession;
  quality_events: number;
  is_stale: boolean;
}

export interface QualityEvent {
  id: string;
  provider: string;
  event_type: string;
  severity: QualitySeverity;
  timeframe: Timeframe | null;
  message: string;
  detail: string;
  window_start: string | null;
  window_end: string | null;
  resolved: boolean;
  created_at: string;
}

export interface QualitySummary {
  total_open: number;
  by_severity: Record<string, number>;
  by_type: Record<string, number>;
  stale_instruments: string[];
  latest: QualityEvent[];
}

export interface ProviderHealth {
  provider: string;
  display_name: string;
  healthy: boolean;
  message: string;
  asset_classes: string[];
  timeframes: string[];
  instruments_covered: number;
}

export interface IngestionRun {
  id: string;
  provider: string;
  kind: string;
  status: string;
  symbols: string;
  rows_written: number;
  rows_skipped: number;
  duration_ms: number;
  error: string;
  started_at: string;
  finished_at: string | null;
}

export interface SyncResult {
  instrument: string;
  provider: string;
  timeframe: Timeframe;
  bars_written: number;
  bars_skipped: number;
  findings: number;
  duration_ms: number;
  ok: boolean;
  error: string | null;
}

export interface SyncResponse {
  results: SyncResult[];
  succeeded: number;
  failed: number;
  duration_ms: number;
}
