# Phase 3 — Market Data

**Status:** complete · **Scope owner:** platform engineering · **Date:** 23 November 2026

This document records exactly what Phase 3 implements, what it deliberately does not, and the
data decisions a reviewer should check before Phase 4 work is merged.

## 1. Objective

Give the platform a market-data foundation it can be trusted with: instruments that mean
something, candles that are correctly timestamped, and an explicit record of when data is
missing or degraded — *before* anything trades on it. Phase 3 adds data. It does not add
decision-making, and it does not make any instrument tradable.

## 2. Delivered scope

### 2.1 Provider adapters

Four adapters behind one `MarketDataProvider` interface (`backend/app/integrations/providers/`).

| Provider | Asset classes | Timeframes | Key | Role |
| --- | --- | --- | --- | --- |
| Kraken | crypto | 1m → 1w | none | primary crypto |
| Yahoo Finance | equity, ETF, FX | 1h → 1w | none | primary equities/FX |
| Twelve Data | equity, ETF, FX | 1h → 1w | `TWELVEDATA_API_KEY` (`demo` works for limited symbols) | equity fallback |
| Frankfurter (ECB) | FX | **D1 only** | none | FX fallback |

Each adapter declares `supported_asset_classes` and `supported_timeframes`, and the ingestion
service refuses to route a request a provider cannot serve. Every adapter is tested against a
recorded payload shape with no network access (`backend/tests/test_providers.py`), so a schema
change upstream fails a test rather than silently writing wrong prices.

**Why two equity sources.** Yahoo rate-limits by IP and throttles a host for extended periods;
this was observed during development, not theorised. A single-source equity stack would have
gone dark for hours. Twelve Data serves a `demo` key with limited symbol coverage, which is
enough for AAPL and friends, and a free registered key covers the rest.

### 2.2 Instrument registry

Five venues, eighteen instruments, twenty-two provider mappings, seeded idempotently on start
(`backend/app/services/market/registry.py`).

- Crypto: `BTC/USD`, `ETH/USD`, `SOL/USD`, `XRP/USD`, `ADA/USD` (Kraken, 24/7)
- US equity: `AAPL`, `MSFT`, `NVDA`, `TSLA` (NASDAQ)
- UK equity: `SHEL.L`, `HSBA.L` (LSE, `price_scale = 0.01` — quoted in pence)
- ETF: `SPY` (NYSE), `QQQ` (NASDAQ), `VUSA.L` (LSE, scale 0.01)
- FX: `GBP/USD`, `EUR/USD`, `USD/JPY`, `GBP/EUR` (Yahoo → Frankfurter)

Repository-seeded venues are documented as informational: using a real exchange name does not
imply a data licence. Commercial redistribution requires an entitled feed.

### 2.3 Storage

Eight new tables (`alembic` revision `ddad454c5a60`): `venues`, `instruments`,
`instrument_providers`, `market_candles`, `market_quotes`, `data_quality_events`,
`ingestion_runs`, `market_calendars`.

- `market_candles` is unique on `(instrument_id, timeframe, open_time)`.
- Closed bars are never rewritten. Re-ingesting a window updates only the forming bar.
- Instruments are `is_tradable = false` throughout Phase 3, enforced by test.

### 2.4 Indicators

`backend/app/services/market/indicators.py` — SMA, EMA, RSI (Wilder), ATR (Wilder), MACD,
Bollinger Bands, realised volatility and lookback returns.

Verified against the published StockCharts worked example: **RSI(14) = 70.46** at index 14 and
**66.25** at index 15, exact to two decimals. Every indicator returns `None` until its window is
complete; a 50-period average computed from 20 bars is not an estimate, it is a wrong number.

### 2.5 Data-quality monitoring

`backend/app/services/market/quality.py` records findings rather than raising exceptions:
gaps, duplicate timestamps, non-positive prices, OHLC inconsistency, outliers, staleness,
provider errors and clock skew. Severity is `info` / `warning` / `critical` and is stored, so a
reviewer can see not just that something failed but how badly.

### 2.6 API

`/api/v1/markets` — `GET /venues`, `/instruments`, `/instruments/{symbol}`, `/overview`,
`/{symbol}/candles`, `/{symbol}/indicators`, `/quality/summary`, `/quality/events`, `/providers`,
`/ingestion-runs`; `POST /sync` (staff only, rate-limited 6/60s), `POST /registry/seed`.

### 2.7 Frontend

The Markets screen now shows provider health, a screener across four asset classes with
sparklines and RSI, a candlestick chart with SMA overlays and a volume pane, an indicator
readout and open quality findings. The chart is hand-drawn SVG — a charting library would add
roughly 150 kB to render one candle style, and the demo build renders in a sandboxed iframe.

## 3. Decisions worth reviewing

**Quotes are stamped with when the observation actually happened.**
Kraken's ticker `c[1]` field is last-trade *volume*, not a timestamp; using it produced quotes
reported as "29,924,211 minutes old". Quotes now use the request time. Frankfurter is the
opposite case: the ECB publishes a rate *for a date*, so the quote is stamped at that fixing
(15:00 UTC). Stamping it with "now" would make a Friday rate look seconds old on a Sunday and
mask a dead feed.

**A closed venue is not a stale feed.**
A weekend equity quote is the last print, not a broken connection. Staleness is evaluated
against session state, and quotes from closed venues are recorded at `info`. Flagging them as
warnings trains operators to ignore the alert stream, which is how real outages get missed.

**Provider failure is expected, so it is designed for.**
Ingestion walks an ordered candidate list. A failing provider produces a quality event and the
next candidate is tried; only when every candidate fails is the sync reported as failed. A live
verification run during this phase had Yahoo throttled throughout: crypto (Kraken), equities
(Twelve Data) and FX (ECB) all still served.

**Unknown provider identifiers are skipped, never guessed at.**
A registry row naming a provider with no adapter is ignored rather than treated as a ticker.

**Pence-quoted instruments carry an explicit scale.**
LSE lines trade in pence. `price_scale = 0.01` is stored on the instrument so every consumer
applies the same conversion once. Applying it twice shows Shell at 0.24.

## 4. Verification

```
cd backend && python3 -m pytest -q          # 86 tests
cd frontend && npm run test                 # 32 tests
PYTHONPATH=backend python scripts/verify_market_data.py   # live providers
```

- 86 backend tests: providers (recorded payloads), quality, indicators, registry, API behaviour,
  idempotency, staff-only sync, rate limiting and total-provider-outage handling.
- 32 frontend tests, including a shared 60-bar fixture pinning the browser indicators to the
  Python implementation to five decimal places.
- `scripts/verify_market_data.py` is the live check. CI uses stubbed providers so builds never
  depend on a third party being reachable.

Live result during development, with Yahoo throttled:

```
kraken       OK   reachable
yahoo        FAIL rate limited by Yahoo (HTTP 429)
frankfurter  OK   reachable
twelvedata   OK   reachable (demo key: limited symbols)

BTC/USD  1h  ok     provider=kraken       written= 60
AAPL     1d  ok     provider=twelvedata   written= 60
SHEL.L   1d  FAILED provider=yahoo,twelvedata
GBP/USD  1d  ok     provider=frankfurter  written= 60
```

## 5. Explicitly not in Phase 3

- No instrument is tradable; no order, position or execution code exists.
- No websockets or streaming; ingestion is pull-based.
- No backtesting, strategy or signal logic — that is Phase 4.
- No real-money movement of any kind.

## 6. Known limitations

- Yahoo remains unreliable by IP. It is a primary source that must never be a single source.
- Twelve Data's `demo` key covers a limited symbol set; UK lines (`SHEL.L`) need a real key.
- FX intraday is unavailable: Yahoo is throttled and the ECB feed is daily by mandate. The
  system reports this rather than interpolating.
- Crypto volume comes from venue tickers and is not consolidated across venues.
