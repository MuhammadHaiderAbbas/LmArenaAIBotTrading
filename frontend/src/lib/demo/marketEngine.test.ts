// The demo market engine stands in for the real providers on GitHub Pages. These
// tests lock the properties that keep it honest: determinism, correct OHLC
// geometry, timeframe alignment and session awareness.
import { describe, expect, it } from "vitest";
import { TIMEFRAME_SECONDS, demoMarketApi, floorToTimeframe, generateCandles } from "./marketEngine";
import type { Timeframe } from "../marketTypes";

describe("demo market engine", () => {
  it("is deterministic: the same inputs give the same series", () => {
    const now = Date.UTC(2026, 10, 23, 12, 0, 0);
    const spec = {
      symbol: "BTC/USD", name: "Bitcoin", assetClass: "crypto" as const,
      venue: { code: "KRAKEN", name: "Kraken", timezone: "UTC", open: null, close: null },
      base: "BTC", quote: "USD", price: 62000, volatility: 0.03, drift: 0.0005,
      priceScale: 1, providers: ["kraken"], seed: 11, description: "",
    };
    const first = generateCandles(spec, "1d", 30, now);
    const second = generateCandles(spec, "1d", 30, now);
    expect(first).toEqual(second);
  });

  it("produces valid OHLC geometry", () => {
    const candles = generateCandles(
      {
        symbol: "X", name: "X", assetClass: "equity", venue: { code: "LSE", name: "LSE",
          timezone: "Europe/London", open: "08:00", close: "16:30" },
        base: "X", quote: "GBP", price: 100, volatility: 0.02, drift: 0,
        priceScale: 1, providers: ["yahoo"], seed: 5, description: "",
      },
      "1h",
      50,
      Date.UTC(2026, 10, 23, 12),
    );
    expect(candles).toHaveLength(50);
    for (const candle of candles) {
      expect(candle.high).toBeGreaterThanOrEqual(Math.max(candle.open, candle.close) - 1e-9);
      expect(candle.low).toBeLessThanOrEqual(Math.min(candle.open, candle.close) + 1e-9);
      expect(candle.close).toBeGreaterThan(0);
    }
  });

  it("keeps bars contiguous and aligned to the timeframe", () => {
    const candles = generateCandles(
      {
        symbol: "X", name: "X", assetClass: "crypto", venue: { code: "KRAKEN", name: "Kraken",
          timezone: "UTC", open: null, close: null }, base: "X", quote: "USD", price: 50,
        volatility: 0.03, drift: 0, priceScale: 1, providers: ["kraken"], seed: 3, description: "",
      },
      "4h",
      20,
      Date.UTC(2026, 10, 23, 13, 37),
    );
    const stepMs = TIMEFRAME_SECONDS["4h"] * 1000;
    for (let index = 1; index < candles.length; index += 1) {
      const previous = new Date(candles[index - 1].open_time).getTime();
      const current = new Date(candles[index].open_time).getTime();
      expect(current - previous).toBe(stepMs);
    }
    const lastOpen = new Date(candles[candles.length - 1].open_time).getTime();
    // Every bar starts on a boundary: 4h bars are 00:00, 04:00, 08:00 …
    expect(lastOpen % stepMs).toBe(0);
  });

  it("floors to the timeframe boundary", () => {
    const midday = Date.UTC(2026, 10, 23, 13, 37, 42);
    expect(floorToTimeframe(midday, "1h")).toBe(Date.UTC(2026, 10, 23, 13));
    expect(floorToTimeframe(midday, "1d")).toBe(Date.UTC(2026, 10, 23));
  });

  it("scales pence-quoted instruments by price_scale", () => {
    const series = generateCandles(
      {
        symbol: "SHEL.L", name: "Shell", assetClass: "equity", venue: { code: "LSE", name: "LSE",
          timezone: "Europe/London", open: "08:00", close: "16:30" }, base: "SHEL", quote: "GBP",
        price: 2642, volatility: 0.01, drift: 0, priceScale: 0.01, providers: ["yahoo"],
        seed: 73, description: "",
      },
      "1d",
      30,
      Date.UTC(2026, 10, 23),
    );
    // LSE lines are quoted in pence and carry price_scale 0.01. The spec holds
    // pence (2642) and the stored candle holds pounds (26.42) — applying the
    // scale twice would show 0.24, which is what this guards against.
    const last = series[series.length - 1];
    expect(last.close).toBeGreaterThan(20);
    expect(last.close).toBeLessThan(35);
    expect(last.high).toBeLessThan(40);
  });

  it("lands each series on its reference price, whatever the requested length", async () => {
    const short = await demoMarketApi.candles("BTC/USD", "1d", 60);
    const long = await demoMarketApi.candles("BTC/USD", "1d", 300);
    const lastShort = short.candles[short.candles.length - 1].close;
    const lastLong = long.candles[long.candles.length - 1].close;
    // Same instrument, same instant: the latest price must not depend on how
    // much history was asked for.
    expect(lastShort).toBeCloseTo(lastLong, 6);
    expect(lastShort).toBeGreaterThan(40_000);
    expect(lastShort).toBeLessThan(90_000);
  });

  it("does not call a healthy forming bar stale", async () => {
    const series = await demoMarketApi.candles("BTC/USD", "1d", 120);
    // The newest daily bar opened this morning; only the previous ones are closed.
    expect(series.candles[series.candles.length - 1].is_final).toBe(false);
    expect(series.is_stale).toBe(false);
  });

  it("serves a screener covering every asset class", async () => {
    const rows = await demoMarketApi.overview();
    expect(rows.length).toBeGreaterThanOrEqual(10);
    const classes = new Set(rows.map((row) => row.instrument.asset_class));
    expect(classes).toEqual(new Set(["crypto", "equity", "etf", "forex"]));
    for (const row of rows) {
      expect(row.quote).not.toBeNull();
      expect(row.indicators?.rsi_14).not.toBeNull();
      expect(row.sparkline.length).toBe(30);
      expect(row.instrument.is_tradable).toBe(false);
    }
  });

  it("reports crypto sessions as always-on and equities by clock", async () => {
    const rows = await demoMarketApi.overview();
    const btc = rows.find((row) => row.instrument.symbol === "BTC/USD");
    expect(btc?.session.state).toBe("always_on");
    const aapl = rows.find((row) => row.instrument.symbol === "AAPL");
    expect(["open", "closed"]).toContain(aapl?.session.state);
  });

  it("returns a candle series with indicators for a symbol", async () => {
    const series = await demoMarketApi.candles("BTC/USD", "1d", 120);
    expect(series.candles).toHaveLength(120);
    expect(series.timeframe).toBe("1d");
    expect(series.indicators.rsi_14).not.toBeNull();
    expect(series.indicators.sma_50).not.toBeNull();
    expect(series.instrument.price_scale).toBe(1);
  });

  it("rejects an unknown symbol instead of inventing data", async () => {
    await expect(demoMarketApi.candles("DOGE/USD")).rejects.toThrow(/Unknown instrument/);
  });

  it("reports provider health including a degraded feed", async () => {
    const providers = await demoMarketApi.providers();
    expect(providers.map((provider) => provider.provider)).toEqual([
      "kraken", "yahoo", "twelvedata", "frankfurter",
    ]);
    // Yahoo is shown throttled on purpose: the UI must be reviewable in its
    // degraded state, not only when everything is green.
    expect(providers.find((provider) => provider.provider === "yahoo")?.healthy).toBe(false);
  });

  it("explains why an intraday forex sync is not available", async () => {
    const result = await demoMarketApi.sync(["GBP/USD"], "1h" as Timeframe);
    expect(result.failed).toBe(1);
    expect(result.results[0].error).toMatch(/no provider supports/);
  });
});
