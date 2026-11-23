import { describe, expect, it } from "vitest";
import { atr, bollinger, ema, macd, rsi, sma, snapshot, volatility, type Bars } from "./indicators";
// Generated once from the Python implementation and pasted in: the two must not
// drift, and a shared fixture is the only way to prove they agree.
const FIXTURE_CLOSES: number[] = [
  100.15, 100.66, 101.66, 103.04, 104.64, 106.28, 107.78, 109.16, 109.88, 110.07, 109.7,
  108.8, 107.46, 105.83, 104.28, 102.6, 101.17, 100.14, 99.61, 99.65, 100.24, 101.5,
  102.93, 104.55, 106.18, 107.64, 108.76, 109.41, 109.71, 109.25, 108.28, 106.89, 105.23,
  103.48, 101.83, 100.65, 99.68, 99.24, 99.36, 100.02, 101.15, 102.62, 104.45, 106.07,
  107.49, 108.54, 109.12, 109.14, 108.6, 107.75, 106.31, 104.63, 102.88, 101.25, 99.93,
  99.04, 98.88, 99.08, 99.83, 101.01,
];

const FIXTURE_HIGHS: number[] = [
  100.95, 101.61, 102.76, 104.29, 106.04, 107.08, 108.73, 110.26, 111.13, 111.47, 110.5,
  109.75, 108.56, 107.08, 105.68, 103.4, 102.12, 101.24, 100.86, 101.05, 101.04, 102.45,
  104.03, 105.8, 107.58, 108.44, 109.71, 110.51, 110.96, 110.65, 109.08, 107.84, 106.33,
  104.73, 103.23, 101.45, 100.63, 100.34, 100.61, 101.42, 101.95, 103.57, 105.55, 107.32,
  108.89, 109.34, 110.07, 110.24, 109.85, 109.15, 107.11, 105.58, 103.98, 102.5, 101.33,
  99.84, 99.83, 100.18, 101.08, 102.41,
];

const FIXTURE_LOWS: number[] = [
  99.25, 99.56, 100.36, 101.54, 103.74, 105.18, 106.48, 107.66, 108.98, 108.97, 108.4,
  107.3, 106.56, 104.73, 102.98, 101.1, 100.27, 99.04, 98.31, 98.15, 99.34, 100.4,
  101.63, 103.05, 105.28, 106.54, 107.46, 107.91, 108.81, 108.15, 106.98, 105.39, 104.33,
  102.38, 100.53, 99.15, 98.78, 98.14, 98.06, 98.52, 100.25, 101.52, 103.15, 104.57,
  106.59, 107.44, 107.82, 107.64, 107.7, 106.65, 105.01, 103.13, 101.98, 100.15, 98.63,
  97.54, 97.98, 97.98, 98.53, 99.51,
];

const FIXTURE: Bars = {
  open: FIXTURE_CLOSES.map((close) => close - 0.2),
  high: FIXTURE_HIGHS,
  low: FIXTURE_LOWS,
  close: FIXTURE_CLOSES,
  volume: FIXTURE_CLOSES.map((_, index) => 1000 + index),
};

describe("indicators mirror the backend", () => {
  it("computes the same SMA as the backend", () => {
    const series = sma(FIXTURE_CLOSES, 20);
    expect(series[19]).toBeCloseTo(104.628, 4);
    expect(series[59]).toBeCloseTo(103.8885, 4);
  });

  it("computes the same EMA as the backend", () => {
    expect(ema(FIXTURE_CLOSES, 20)[59]).toBeCloseTo(102.605721, 5);
  });

  it("computes the same RSI as the backend", () => {
    expect(rsi(FIXTURE_CLOSES, 14)[59]).toBeCloseTo(43.010378, 5);
  });

  it("computes the same ATR as the backend", () => {
    const series = atr(FIXTURE, 14);
    expect(series[13]).toBeCloseTo(2.397143, 5);
    expect(series[59]).toBeCloseTo(2.51856, 5);
  });

  it("computes the same Bollinger Bands as the backend", () => {
    const bands = bollinger(FIXTURE_CLOSES, 20, 2);
    expect(bands.upper[59]).toBeCloseTo(111.201229, 5);
    expect(bands.middle[59]).toBeCloseTo(103.8885, 5);
    expect(bands.lower[59]).toBeCloseTo(96.575771, 5);
  });

  it("computes the same MACD as the backend", () => {
    const result = macd(FIXTURE_CLOSES);
    expect(result.macd[59]).toBeCloseTo(-1.328378, 5);
    expect(result.signal[59]).toBeCloseTo(-0.743906, 5);
    expect(result.histogram[59]).toBeCloseTo(-0.584472, 5);
  });

  it("keeps the histogram equal to line minus signal", () => {
    const result = macd(FIXTURE_CLOSES);
    expect(result.histogram[59]).toBeCloseTo(
      (result.macd[59] as number) - (result.signal[59] as number),
      10,
    );
  });

  it("returns null before warm-up rather than a partial window", () => {
    const series = rsi(FIXTURE_CLOSES, 14);
    for (let index = 0; index < 14; index += 1) expect(series[index]).toBeNull();
    expect(sma([1, 2, 3], 5)).toEqual([null, null, null]);
    expect(ema([1, 2, 3], 5)).toEqual([null, null, null]);
  });

  it("matches the published StockCharts RSI worked example", () => {
    const closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.1, 45.42, 45.84,
      46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.0, 46.03, 46.41, 46.22, 45.64];
    const series = rsi(closes, 14);
    expect(series[14]).toBeCloseTo(70.4641, 4);
    expect(series[15]).toBeCloseTo(66.2496, 4);
  });

  it("reports volatility as a positive annualised percentage", () => {
    const value = volatility(FIXTURE_CLOSES, 20)[59] as number;
    expect(value).toBeGreaterThan(0);
    expect(Number.isFinite(value)).toBe(true);
  });

  it("produces a complete snapshot for a warmed-up series", () => {
    const shot = snapshot(FIXTURE);
    expect(shot.sma_20).toBeCloseTo(103.8885, 4);
    expect(shot.sma_50).toBeCloseTo(104.0368, 4);
    expect(shot.ema_20).toBeCloseTo(102.605721, 5);
    expect(shot.rsi_14).toBeCloseTo(43.010378, 5);
    expect(shot.macd).toBeCloseTo(-1.328378, 5);
    expect(shot.atr_14).toBeCloseTo(2.51856, 5);
    expect(shot.bb_upper).toBeCloseTo(111.201229, 5);
    expect(shot.change_pct_1).not.toBeNull();
    expect(shot.change_pct_20).not.toBeNull();
  });

  it("leaves long lookbacks empty when history is short", () => {
    const short = FIXTURE_CLOSES.slice(0, 25);
    const shot = snapshot({
      open: short, high: short, low: short, close: short, volume: short,
    });
    expect(shot.sma_50).toBeNull();
    expect(shot.sma_20).not.toBeNull();
  });
});
