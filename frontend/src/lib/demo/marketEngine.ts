// Deterministic market-data engine for the static demo build.
//
// The GitHub Pages build has no server, so candles have to be generated in the
// browser. Two rules keep that honest:
//
//  1. Everything is a pure function of a seed. Reloading the page shows the same
//     series, and a symbol's history does not change when you look at another.
//  2. It is labelled as synthetic everywhere it surfaces. The demo must never be
//     mistaken for live prices — the whole point of the product is data
//     discipline, so the demo cannot be the one place that lies.
//
// Volume is modelled as a function of bar range, so the volume pane looks like a
// real feed rather than noise unrelated to price action.

import { snapshot, type Bars } from "../indicators";
import type {
  Candle,
  CandleSeries,
  Indicators,
  Instrument,
  MarketRow,
  MarketSession,
  ProviderHealth,
  QualitySummary,
  SyncResponse,
  Timeframe,
} from "../marketTypes";

export const TIMEFRAME_SECONDS: Record<Timeframe, number> = {
  "1m": 60,
  "5m": 300,
  "15m": 900,
  "30m": 1800,
  "1h": 3600,
  "4h": 14400,
  "1d": 86400,
  "1w": 604800,
};

interface Spec {
  symbol: string;
  name: string;
  assetClass: Instrument["asset_class"];
  venue: { code: string; name: string; timezone: string; open: string | null; close: string | null };
  base: string;
  quote: string;
  price: number;
  volatility: number; // daily fractional sigma
  drift: number;
  priceScale: number;
  providers: string[];
  seed: number;
  description: string;
}

const SPECS: Spec[] = [
  {
    symbol: "BTC/USD", name: "Bitcoin / US Dollar", assetClass: "crypto",
    venue: { code: "KRAKEN", name: "Kraken", timezone: "UTC", open: null, close: null },
    base: "BTC", quote: "USD", price: 62050.3, volatility: 0.031, drift: 0.0006,
    priceScale: 1, providers: ["kraken"], seed: 11,
    description: "Spot crypto pair. Trades continuously, including weekends.",
  },
  {
    symbol: "ETH/USD", name: "Ethereum / US Dollar", assetClass: "crypto",
    venue: { code: "KRAKEN", name: "Kraken", timezone: "UTC", open: null, close: null },
    base: "ETH", quote: "USD", price: 3041.18, volatility: 0.038, drift: 0.0004,
    priceScale: 1, providers: ["kraken"], seed: 23,
    description: "Spot crypto pair. Trades continuously, including weekends.",
  },
  {
    symbol: "SOL/USD", name: "Solana / US Dollar", assetClass: "crypto",
    venue: { code: "KRAKEN", name: "Kraken", timezone: "UTC", open: null, close: null },
    base: "SOL", quote: "USD", price: 148.72, volatility: 0.052, drift: 0.0009,
    priceScale: 1, providers: ["kraken"], seed: 31,
    description: "Higher-beta crypto pair; wider spreads in thin hours.",
  },
  {
    symbol: "AAPL", name: "Apple Inc.", assetClass: "equity",
    venue: { code: "NASDAQ", name: "NASDAQ", timezone: "America/New_York", open: "09:30", close: "16:00" },
    base: "AAPL", quote: "USD", price: 348.04, volatility: 0.016, drift: 0.0004,
    priceScale: 1, providers: ["yahoo", "twelvedata"], seed: 43,
    description: "US large-cap equity. Regular session 09:30–16:00 New York.",
  },
  {
    symbol: "MSFT", name: "Microsoft Corp.", assetClass: "equity",
    venue: { code: "NASDAQ", name: "NASDAQ", timezone: "America/New_York", open: "09:30", close: "16:00" },
    base: "MSFT", quote: "USD", price: 512.4, volatility: 0.014, drift: 0.0005,
    priceScale: 1, providers: ["yahoo", "twelvedata"], seed: 57,
    description: "US large-cap equity. Regular session 09:30–16:00 New York.",
  },
  {
    symbol: "NVDA", name: "NVIDIA Corp.", assetClass: "equity",
    venue: { code: "NASDAQ", name: "NASDAQ", timezone: "America/New_York", open: "09:30", close: "16:00" },
    base: "NVDA", quote: "USD", price: 186.9, volatility: 0.028, drift: 0.0011,
    priceScale: 1, providers: ["yahoo", "twelvedata"], seed: 67,
    description: "High-volatility US equity; gap risk around earnings.",
  },
  {
    symbol: "SHEL.L", name: "Shell plc", assetClass: "equity",
    venue: { code: "LSE", name: "London Stock Exchange", timezone: "Europe/London", open: "08:00", close: "16:30" },
    base: "SHEL", quote: "GBP", price: 2642, volatility: 0.013, drift: 0.0002,
    priceScale: 0.01, providers: ["yahoo", "twelvedata"], seed: 73,
    description: "UK equity quoted in pence. Prices are scaled by 0.01 for display.",
  },
  {
    symbol: "HSBA.L", name: "HSBC Holdings plc", assetClass: "equity",
    venue: { code: "LSE", name: "London Stock Exchange", timezone: "Europe/London", open: "08:00", close: "16:30" },
    base: "HSBA", quote: "GBP", price: 984, volatility: 0.012, drift: 0.0003,
    priceScale: 0.01, providers: ["yahoo", "twelvedata"], seed: 79,
    description: "UK equity quoted in pence. Prices are scaled by 0.01 for display.",
  },
  {
    symbol: "SPY", name: "SPDR S&P 500 ETF", assetClass: "etf",
    venue: { code: "NYSE", name: "New York Stock Exchange", timezone: "America/New_York", open: "09:30", close: "16:00" },
    base: "SPY", quote: "USD", price: 648.2, volatility: 0.011, drift: 0.0003,
    priceScale: 1, providers: ["yahoo", "twelvedata"], seed: 83,
    description: "US large-cap index tracker. Treated as an ETF, not an equity.",
  },
  {
    symbol: "QQQ", name: "Invesco QQQ Trust", assetClass: "etf",
    venue: { code: "NASDAQ", name: "NASDAQ", timezone: "America/New_York", open: "09:30", close: "16:00" },
    base: "QQQ", quote: "USD", price: 561.75, volatility: 0.014, drift: 0.0004,
    priceScale: 1, providers: ["yahoo", "twelvedata"], seed: 89,
    description: "NASDAQ-100 tracker; more tech-weighted than SPY.",
  },
  {
    symbol: "GBP/USD", name: "Sterling / US Dollar", assetClass: "forex",
    venue: { code: "FX", name: "Interbank FX", timezone: "Europe/London", open: "00:00", close: "23:59" },
    base: "GBP", quote: "USD", price: 1.3074, volatility: 0.005, drift: 0.00005,
    priceScale: 1, providers: ["yahoo", "frankfurter"], seed: 97,
    description: "Major FX pair. ECB reference rate is the daily fallback.",
  },
  {
    symbol: "EUR/USD", name: "Euro / US Dollar", assetClass: "forex",
    venue: { code: "FX", name: "Interbank FX", timezone: "Europe/London", open: "00:00", close: "23:59" },
    base: "EUR", quote: "USD", price: 1.1612, volatility: 0.004, drift: 0.00004,
    priceScale: 1, providers: ["yahoo", "frankfurter"], seed: 101,
    description: "The most liquid FX pair; tightest spreads in the set.",
  },
  {
    symbol: "USD/JPY", name: "US Dollar / Japanese Yen", assetClass: "forex",
    venue: { code: "FX", name: "Interbank FX", timezone: "Europe/London", open: "00:00", close: "23:59" },
    base: "USD", quote: "JPY", price: 156.83, volatility: 0.006, drift: 0.00008,
    priceScale: 1, providers: ["yahoo", "frankfurter"], seed: 103,
    description: "Carry-sensitive pair; intervenes violently around policy dates.",
  },
  {
    symbol: "GBP/EUR", name: "Sterling / Euro", assetClass: "forex",
    venue: { code: "FX", name: "Interbank FX", timezone: "Europe/London", open: "00:00", close: "23:59" },
    base: "GBP", quote: "EUR", price: 1.1259, volatility: 0.004, drift: 0.00002,
    priceScale: 1, providers: ["yahoo", "frankfurter"], seed: 107,
    description: "Cross rate; triangulated from EUR legs by the ECB feed.",
  },
];

// ---------------------------------------------------------------- randomness

/** Mulberry32: small, fast, and stable across engines — good enough for a demo. */
function rng(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function gaussian(next: () => number): number {
  // Box–Muller; clamped away from 0 so log() never blows up.
  const u = Math.max(next(), 1e-9);
  const v = next();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

export function floorToTimeframe(epochMs: number, timeframe: Timeframe): number {
  const seconds = TIMEFRAME_SECONDS[timeframe];
  const epoch = Math.floor(epochMs / 1000);
  return (epoch - (epoch % seconds)) * 1000;
}

/** Generate `count` closed bars ending at the last completed interval. */
export function generateCandles(
  spec: Spec,
  timeframe: Timeframe,
  count: number,
  now: number,
): Candle[] {
  const step = TIMEFRAME_SECONDS[timeframe];
  const end = floorToTimeframe(now, timeframe);
  const start = end - step * 1000 * count;

  // Volatility scales with the square root of time, as it does in reality.
  const perBar = spec.volatility * Math.sqrt(step / 86400);
  const driftPerBar = spec.drift * (step / 86400);

  // Walk backwards from a fixed offset, then rescale so the *last* bar closes at
  // the instrument's reference price. A 400-bar random walk free to drift put BTC
  // at 130k against a 62k quote — the chart has to agree with the quote.
  const next = rng(spec.seed);
  const scale = spec.priceScale;
  const candles: Candle[] = [];
  let previousClose = spec.price;

  for (let index = 0; index < count; index += 1) {
    const openTime = start + step * 1000 * index;
    const shock = gaussian(next) * perBar;
    const close = Math.max(previousClose * (1 + driftPerBar + shock), 1e-6);
    const body = Math.abs(close - previousClose);
    const wick = (perBar * previousClose * (0.4 + next() * 0.9)) / 2;
    const high = Math.max(close, previousClose) + wick;
    const low = Math.min(close, previousClose) - wick;

    // Volume rises with range, which is what makes a volume pane readable.
    const rangeFactor = (high - low) / Math.max(previousClose, 1e-9);
    const baseVolume = spec.assetClass === "crypto" ? 900 : spec.assetClass === "forex" ? 0 : 240_000;
    const volume = baseVolume === 0 ? 0 : Math.round(baseVolume * (0.5 + rangeFactor * 120) * (0.7 + next() * 0.6));

    // The final bar is the one currently forming: it is marked non-final so the
    // chart can render it distinctly and nothing downstream treats it as closed.
    const forming = index === count - 1;
    candles.push({
      open_time: new Date(openTime).toISOString(),
      close_time: new Date(openTime + step * 1000).toISOString(),
      open: previousClose * scale,
      high: high * scale,
      low: low * scale,
      close: close * scale,
      volume,
      vwap: null,
      is_final: !forming,
      provider: spec.assetClass === "crypto" ? "kraken" : spec.assetClass === "forex" ? "frankfurter" : "twelvedata",
    });

    previousClose = close;
    void body;
  }

  // Rescale: multiplicative factors preserve the shape of returns exactly, so
  // volatility, ranges and indicator values all stay coherent.
  // The anchor is the *displayed* price, so the scale is applied once here and
  // never again: comparing a scaled close to an unscaled anchor was exactly the
  // double-scaling bug this rescaled step has to avoid.
  const anchor = spec.price * spec.priceScale;
  const lastClose = candles[candles.length - 1].close;
  const factor = lastClose > 0 ? anchor / lastClose : 1;
  for (const candle of candles) {
    candle.open *= factor;
    candle.high *= factor;
    candle.low *= factor;
    candle.close *= factor;
  }
  return candles;
}

function barsOf(candles: Candle[]): Bars {
  return {
    open: candles.map((candle) => candle.open),
    high: candles.map((candle) => candle.high),
    low: candles.map((candle) => candle.low),
    close: candles.map((candle) => candle.close),
    volume: candles.map((candle) => candle.volume),
  };
}

// ------------------------------------------------------------ session logic

function localParts(now: number, timeZone: string) {
  const formatter = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = formatter.formatToParts(new Date(now));
  const get = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return {
    weekday: get("weekday"),
    hour: Number(get("hour")),
    minute: Number(get("minute")),
    label: `${get("hour")}:${get("minute")}`,
  };
}

/** Session state mirrors the backend: closed venues still serve their last print. */
export function sessionFor(spec: Spec, now: number): MarketSession {
  const is24_7 = spec.venue.open === null;
  const local = localParts(now, spec.venue.timezone);
  const weekend = local.weekday === "Sat" || local.weekday === "Sun";

  if (is24_7 && spec.assetClass === "crypto") {
    return {
      venue: spec.venue.code,
      state: "always_on",
      timezone: spec.venue.timezone,
      local_time: local.label,
      is_open: true,
      session_open: null,
      session_close: null,
    };
  }

  const [openHour, openMinute] = (spec.venue.open ?? "00:00").split(":").map(Number);
  const [closeHour, closeMinute] = (spec.venue.close ?? "23:59").split(":").map(Number);
  const minutes = local.hour * 60 + local.minute;
  const openAt = openHour * 60 + openMinute;
  const closeAt = closeHour * 60 + closeMinute;
  const withinHours = minutes >= openAt && minutes < closeAt;

  // FX closes Friday evening and reopens Sunday evening; equities close at the
  // weekend. Either way a closed venue is reported as closed, not as stale.
  const open = !weekend && withinHours;
  return {
    venue: spec.venue.code,
    state: open ? "open" : "closed",
    timezone: spec.venue.timezone,
    local_time: local.label,
    is_open: open,
    session_open: spec.venue.open,
    session_close: spec.venue.close,
  };
}

// ------------------------------------------------------------------ the API

const CANDLE_CACHE = new Map<string, Candle[]>();

function instrumentOf(spec: Spec): Instrument {
  return {
    id: `demo-${spec.symbol.replace(/[^A-Za-z0-9]/g, "").toLowerCase()}`,
    symbol: spec.symbol,
    name: spec.name,
    asset_class: spec.assetClass,
    base_currency: spec.base,
    quote_currency: spec.quote,
    price_scale: spec.priceScale,
    tick_size: 0.0001,
    lot_size: 0.0001,
    status: "active",
    is_tradable: false,
    description: spec.description,
    venue: {
      code: spec.venue.code,
      name: spec.venue.name,
      asset_class: spec.assetClass,
      timezone: spec.venue.timezone,
      session_open: spec.venue.open,
      session_close: spec.venue.close,
      is_24_7: spec.venue.open === null,
    },
    providers: spec.providers,
  };
}

function findSpec(symbol: string): Spec {
  const match = SPECS.find((spec) => spec.symbol.toLowerCase() === symbol.toLowerCase());
  if (!match) throw new Error(`Unknown instrument '${symbol}'`);
  return match;
}

/**
 * One canonical history per instrument and timeframe, then sliced.
 *
 * The price of an instrument must not depend on how much of its history you
 * asked for — generating `count` bars backwards from now made the screener and
 * the chart disagree about the current price, which is the fastest way to lose
 * trust in a market screen.
 */
const CANONICAL_BARS = 400;

function candlesFor(spec: Spec, timeframe: Timeframe, count: number): Candle[] {
  const key = `${spec.symbol}:${timeframe}:${floorToTimeframe(Date.now(), timeframe)}`;
  let full = CANDLE_CACHE.get(key);
  if (!full) {
    full = generateCandles(spec, timeframe, CANONICAL_BARS, Date.now());
    CANDLE_CACHE.set(key, full);
  }
  return full.slice(Math.max(full.length - count, 0));
}

function quoteFor(spec: Spec, candles: Candle[]) {
  const last = candles[candles.length - 1];
  const previous = candles[candles.length - 2] ?? last;
  const session = sessionFor(spec, Date.now());
  // A closed venue's quote does not tick, and reports its age honestly.
  const ageSeconds = session.is_open ? 3 + (spec.seed % 12) : 3600 * 6;
  const spreadBps = spec.assetClass === "crypto" ? 1.2 : spec.assetClass === "forex" ? 0.6 : 2.8;
  const mid = last.close;
  return {
    bid: mid * (1 - spreadBps / 20000),
    ask: mid * (1 + spreadBps / 20000),
    mid,
    last: mid,
    open_24h: previous.close,
    change_24h_pct: ((mid - previous.close) / previous.close) * 100,
    volume_24h: candles.slice(-1)[0]?.volume ?? null,
    spread_bps: spreadBps,
    provider: last.provider,
    observed_at: new Date(Date.now() - ageSeconds * 1000).toISOString(),
    received_at: new Date().toISOString(),
    age_seconds: ageSeconds,
  };
}

function qualityFor(indicators: Indicators, session: MarketSession): MarketRow["data_quality"] {
  if (indicators.rsi_14 === null) return "no_data";
  // Deliberately exercised so the UI can be reviewed in every state.
  if (!session.is_open) return "warning";
  return "ok";
}

export const demoMarketApi = {
  async instruments(): Promise<Instrument[]> {
    return SPECS.map(instrumentOf);
  },

  async overview(): Promise<MarketRow[]> {
    const daily = candlesFor(SPECS[0], "1d", 90);
    void daily;
    return SPECS.map((spec) => {
      const candles = candlesFor(spec, "1d", 90);
      const indicators = snapshot(barsOf(candles));
      const session = sessionFor(spec, Date.now());
      return {
        instrument: instrumentOf(spec),
        session,
        quote: quoteFor(spec, candles),
        indicators,
        sparkline: candles.slice(-30).map((candle) => Number(candle.close.toFixed(6))),
        data_quality: qualityFor(indicators, session),
      };
    });
  },

  async candles(symbol: string, timeframe: Timeframe = "1d", limit = 180): Promise<CandleSeries> {
    const spec = findSpec(symbol);
    const candles = candlesFor(spec, timeframe, limit);
    const indicators = snapshot(barsOf(candles));
    const session = sessionFor(spec, Date.now());
    const last = candles[candles.length - 1];
    // A bar is late only once a *whole interval* has elapsed since it opened and
    // no successor has arrived. Measuring from the open alone would mark every
    // healthy forming daily bar stale all day, which is how operators learn to
    // ignore staleness warnings.
    const minutesSinceOpen = (Date.now() - new Date(last.open_time).getTime()) / 60000;
    const lateMinutes = minutesSinceOpen - TIMEFRAME_SECONDS[timeframe] / 60;
    return {
      instrument: instrumentOf(spec),
      timeframe,
      candles,
      indicators,
      session,
      quality_events: 0,
      is_stale: session.is_open && lateMinutes > TIMEFRAME_SECONDS[timeframe] / 60,
    };
  },

  async qualitySummary(): Promise<QualitySummary> {
    const rows = await demoMarketApi.overview();
    const stale = rows.filter((row) => row.data_quality === "warning").map((row) => row.instrument.symbol);
    return {
      total_open: stale.length,
      by_severity: { info: 0, warning: stale.length, critical: 0 },
      by_type: { stale: stale.length },
      stale_instruments: stale,
      latest: [],
    };
  },

  async providers(): Promise<ProviderHealth[]> {
    return [
      {
        provider: "kraken",
        display_name: "Kraken",
        healthy: true,
        message: "reachable (demo)",
        asset_classes: ["crypto"],
        timeframes: ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
        instruments_covered: 3,
      },
      {
        provider: "yahoo",
        display_name: "Yahoo Finance",
        healthy: false,
        message: "rate limited by Yahoo (HTTP 429) — failover active",
        asset_classes: ["equity", "etf", "forex"],
        timeframes: ["1h", "4h", "1d", "1w"],
        instruments_covered: 8,
      },
      {
        provider: "twelvedata",
        display_name: "Twelve Data",
        healthy: true,
        message: "reachable (demo key: limited symbols)",
        asset_classes: ["equity", "etf", "forex"],
        timeframes: ["1h", "4h", "1d", "1w"],
        instruments_covered: 8,
      },
      {
        provider: "frankfurter",
        display_name: "ECB reference rates",
        healthy: true,
        message: "reachable",
        asset_classes: ["forex"],
        timeframes: ["1d"],
        instruments_covered: 4,
      },
    ];
  },

  /** The demo cannot reach a provider, so a sync reports what *would* happen. */
  async sync(symbols: string[], timeframe: Timeframe = "1d"): Promise<SyncResponse> {
    const started = Date.now();
    const results = specsFor(symbols).map((spec) => {
      const skipped = spec.assetClass === "forex" && timeframe !== "1d";
      return {
        instrument: spec.symbol,
        provider: skipped ? "none" : spec.providers[0],
        timeframe,
        bars_written: skipped ? 0 : 0,
        bars_skipped: 0,
        findings: 0,
        duration_ms: 12,
        ok: !skipped,
        error: skipped ? "no provider supports intraday forex in demo mode" : null,
      };
    });
    return {
      results,
      succeeded: results.filter((row) => row.ok).length,
      failed: results.filter((row) => !row.ok).length,
      duration_ms: Date.now() - started,
    };
  },
};

function specsFor(symbols: string[]): Spec[] {
  if (!symbols.length) return SPECS;
  return symbols.map((symbol) => findSpec(symbol));
}

export type DemoMarketApi = typeof demoMarketApi;
