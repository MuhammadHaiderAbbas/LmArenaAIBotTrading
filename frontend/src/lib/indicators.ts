// Technical indicators — a deliberate mirror of `backend/app/services/market/indicators.py`.
//
// The GitHub Pages build has no server, so the demo engine computes indicators in
// the browser. A mismatch between these two implementations would mean the demo
// shows different numbers from the real product, so both are pinned to the same
// reference values in their test suites (RSI(14) Wilder = 70.46, ATR(14) = 1.0254,
// BB(20,2) = 47.115 / 45.409 / 43.703 on the shared fixture).
//
// Conventions that matter:
//  - Wildcard/Wilder smoothing seeds with a simple average of the first `period`
//    values, then applies `(prev * (period - 1) + current) / period`.
//  - Indicators return `null` until fully warmed up. Never a partial window: a
//    "20-period SMA" computed from 6 bars is worse than no value because nothing
//    downstream can tell the difference.
//  - Population standard deviation for Bollinger Bands (matches TA-Lib).

export interface Bars {
  open: number[];
  high: number[];
  low: number[];
  close: number[];
  volume: number[];
}

export function sma(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0) return out;
  let sum = 0;
  for (let i = 0; i < values.length; i += 1) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

export function ema(values: number[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (period <= 0 || values.length < period) return out;
  // Seed with the SMA of the first window; this is what charting packages and
  // TA-Lib do, and it keeps EMA and SMA comparable at the join point.
  let seed = 0;
  for (let i = 0; i < period; i += 1) seed += values[i];
  let previous = seed / period;
  out[period - 1] = previous;
  const k = 2 / (period + 1);
  for (let i = period; i < values.length; i += 1) {
    previous = values[i] * k + previous * (1 - k);
    out[i] = previous;
  }
  return out;
}

export function rsi(values: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length <= period) return out;

  let gains = 0;
  let losses = 0;
  for (let i = 1; i <= period; i += 1) {
    const change = values[i] - values[i - 1];
    if (change >= 0) gains += change;
    else losses -= change;
  }
  let avgGain = gains / period;
  let avgLoss = losses / period;
  out[period] = toRsi(avgGain, avgLoss);

  for (let i = period + 1; i < values.length; i += 1) {
    const change = values[i] - values[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = toRsi(avgGain, avgLoss);
  }
  return out;
}

function toRsi(avgGain: number, avgLoss: number): number {
  if (avgLoss === 0) return 100;
  const rs = avgGain / avgLoss;
  return 100 - 100 / (1 + rs);
}

export function trueRange(bars: Bars): number[] {
  const out: number[] = [];
  for (let i = 0; i < bars.close.length; i += 1) {
    if (i === 0) {
      out.push(bars.high[i] - bars.low[i]);
      continue;
    }
    const previousClose = bars.close[i - 1];
    out.push(
      Math.max(
        bars.high[i] - bars.low[i],
        Math.abs(bars.high[i] - previousClose),
        Math.abs(bars.low[i] - previousClose),
      ),
    );
  }
  return out;
}

export function atr(bars: Bars, period = 14): (number | null)[] {
  const ranges = trueRange(bars);
  const out: (number | null)[] = new Array(ranges.length).fill(null);
  if (ranges.length < period) return out;
  let sum = 0;
  for (let i = 0; i < period; i += 1) sum += ranges[i];
  let previous = sum / period;
  out[period - 1] = previous;
  for (let i = period; i < ranges.length; i += 1) {
    previous = (previous * (period - 1) + ranges[i]) / period;
    out[i] = previous;
  }
  return out;
}

export function macd(
  values: number[],
  fast = 12,
  slow = 26,
  signalPeriod = 9,
): { macd: (number | null)[]; signal: (number | null)[]; histogram: (number | null)[] } {
  const fastEma = ema(values, fast);
  const slowEma = ema(values, slow);
  const line: (number | null)[] = values.map((_, index) =>
    fastEma[index] !== null && slowEma[index] !== null
      ? (fastEma[index] as number) - (slowEma[index] as number)
      : null,
  );
  // The signal line is an EMA of the MACD line, so it can only start once the
  // MACD line itself has warmed up.
  const firstIndex = line.findIndex((value) => value !== null);
  const compact = firstIndex === -1 ? [] : (line.slice(firstIndex) as number[]);
  const compactSignal = ema(compact, signalPeriod);
  const signal: (number | null)[] = new Array(values.length).fill(null);
  compactSignal.forEach((value, index) => {
    if (value !== null) signal[firstIndex + index] = value;
  });
  const histogram = line.map((value, index) =>
    value !== null && signal[index] !== null ? value - (signal[index] as number) : null,
  );
  return { macd: line, signal, histogram };
}

export function bollinger(
  values: number[],
  period = 20,
  multiplier = 2,
): { upper: (number | null)[]; middle: (number | null)[]; lower: (number | null)[] } {
  const middle = sma(values, period);
  const upper: (number | null)[] = new Array(values.length).fill(null);
  const lower: (number | null)[] = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i += 1) {
    const mean = middle[i] as number;
    let variance = 0;
    for (let j = i - period + 1; j <= i; j += 1) variance += (values[j] - mean) ** 2;
    const deviation = Math.sqrt(variance / period); // population, per TA-Lib
    upper[i] = mean + multiplier * deviation;
    lower[i] = mean - multiplier * deviation;
  }
  return { upper, middle, lower };
}

/** Annualised realised volatility from log returns, as a percentage. */
export function volatility(values: number[], period = 20): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  if (values.length < period + 1) return out;
  const returns: number[] = [];
  for (let i = 1; i < values.length; i += 1) {
    if (values[i - 1] <= 0 || values[i] <= 0) {
      returns.push(0);
      continue;
    }
    returns.push(Math.log(values[i] / values[i - 1]));
  }
  for (let i = period - 1; i < returns.length; i += 1) {
    const window = returns.slice(i - period + 1, i + 1);
    const mean = window.reduce((sum, value) => sum + value, 0) / window.length;
    const variance = window.reduce((sum, value) => sum + (value - mean) ** 2, 0) / window.length;
    out[i + 1] = Math.sqrt(variance) * Math.sqrt(252) * 100;
  }
  return out;
}

function pctChange(values: number[], lookback: number): number | null {
  if (values.length <= lookback) return null;
  const previous = values[values.length - 1 - lookback];
  const current = values[values.length - 1];
  if (!previous) return null;
  return ((current - previous) / previous) * 100;
}

function last<T>(values: T[]): T | null {
  return values.length ? values[values.length - 1] : null;
}

/** Build the full indicator snapshot for the most recent bar, matching the API. */
export function snapshot(bars: Bars) {
  const close = bars.close;
  const macdResult = macd(close);
  const bands = bollinger(close);
  const vol = volatility(close);
  return {
    sma_20: last(sma(close, 20)),
    sma_50: last(sma(close, 50)),
    ema_20: last(ema(close, 20)),
    rsi_14: last(rsi(close, 14)),
    macd: last(macdResult.macd),
    macd_signal: last(macdResult.signal),
    macd_histogram: last(macdResult.histogram),
    atr_14: last(atr(bars, 14)),
    bb_upper: last(bands.upper),
    bb_middle: last(bands.middle),
    bb_lower: last(bands.lower),
    volatility_20: last(vol),
    change_pct_1: pctChange(close, 1),
    change_pct_5: pctChange(close, 5),
    change_pct_20: pctChange(close, 20),
  };
}
