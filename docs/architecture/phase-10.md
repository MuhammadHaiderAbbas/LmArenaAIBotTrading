# Phase 10 — Analytics

**Status:** complete · **Scope owner:** quantitative engineering · **Date:** 23 November 2026

The blueprint's Phase 10 deliverable is:

> Performance metrics, explanations and reports.

The blueprint also sets the standard for what a good metric is:

> Measure success through reliability, evidence quality and risk-adjusted performance —
> not dashboard appearance.

That line is the whole design brief. A dashboard can make any strategy look good: show
a Sharpe ratio computed from four data points, an "infinite" profit factor for a
strategy that has never lost, and a curve drawn from today's open positions. Every one
of those is a lie that looks like rigour. This phase is built to refuse them.

## 1. The central rule

> **A metric is reported only if the sample can support it, and it is refused with a
> reason when it cannot.**

Every metric is `{key, label, value, unit, definition, note}`. `value` is `null` — never
`0.0` — when the data is insufficient, and `note` says what is missing. The report also
carries a `notes` array repeating the caveats in prose, and the UI has a panel titled
*"Why a number is missing"*.

| Metric | Refused when |
| --- | --- |
| Sharpe / Sortino | fewer than 10 daily equity observations |
| Win rate / profit factor | fewer than 5 closed round trips |
| Profit factor | nothing has been lost — "infinity" is flattery, not information |
| Any ratio | the denominator is zero (a flat curve has no volatility to divide by) |

## 2. Three accounting decisions

Each of these is a place analytics usually lies, and each has a test.

### 2.1 The book is rebuilt from fills, not from the position table

A closed position row has its quantity zeroed on close. Valuing history from *today's*
open positions therefore marked every past day as flat — and a profitable account
showed a 10% "max drawdown" that never happened. Found by running the smoke test, not
by reading the code.

`equity_curve` now reconstructs the quantity held at each moment from cumulative fills
and prices it at that day's close:

```
equity(t) = cash(t) + Σ quantity_held_at(t, symbol) × close(symbol, t)
```

`test_the_curve_does_not_invent_a_drawdown_on_a_profitable_account` is the regression
guard.

### 2.2 Round trips are FIFO over fills, and net of commission

A sell closes the oldest open lot for that symbol, so a partial exit leaves the
remainder open — the correct interpretation, and the reason closed-trade P&L can be
less than total realised P&L until the book is flat.

**Trade P&L is net; the position table is gross.** The broker posts realised P&L as
proceeds − released basis and books commission separately to `FEE_EXPENSE`. Since the
blueprint asks for "net return after costs", the trade list reports net — a list whose
winners are gross while the headline is net invites a reader to double-count. The two
therefore differ by exactly the commissions, and the test asserts that as an identity:

```python
fifo_net + fifo_commission == positions.realised_pnl
```

### 2.3 The benchmark is what was traded, not an index

Equal-weight buy-and-hold of the instruments the account actually traded, over the same
window. An index answers "did the market go up?"; the question worth asking is "was
*selecting these trades* better than simply holding what was traded?". Every report
carries a note saying so.

## 3. Delivered scope

### 3.1 Service (`app/services/analytics.py`)

| Function | Purpose |
| --- | --- |
| `fill_timeline` | Every fill, oldest first — the single source for curves and trips |
| `round_trips` | FIFO closed trades with net P&L and holding period |
| `quantity_held_at` | Net quantity from cumulative fills at a moment |
| `equity_curve` | Daily marked equity, rebuilt from fills; capped at 400 points |
| `benchmark_curve` | Equal-weight buy-and-hold of traded instruments |
| `sharpe` / `sortino` / `max_drawdown` / `profit_factor` / `win_rate` | Each returns `None` rather than a misleading number |
| `build_report` | Assembles metrics, curves, attribution, costs and caveats |

Attribution splits realised P&L by instrument and by the strategy that opened the
position, so "where did the money come from?" has an answer that is not a single total.

### 3.2 API (3 paths)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/analytics/performance` | Full report, with `trade_limit` |
| `GET` | `/analytics/trades` | Closed round trips |
| `GET` | `/analytics/equity-curve` | Marked equity series |

The report is deliberately **one call**: a Sharpe ratio shown without the curve it came
from invites a reader to trust a number they cannot check.

`PerformanceReport.forward_looking` is always `false`, in the contract, so no client
can present the page as a forecast.

### 3.3 Database

**No new tables.** Analytics is computed from the orders, fills, positions and ledger
entries that Phases 8 and 9 already record — which is the point: if a metric needed a
table of its own, it would be a number the platform maintains rather than one it
derives. Schema stays at **52 tables**; migration head stays `bf24b2c1f957`.

### 3.4 Frontend

`/analytics` renders the real report, replacing the Phase 1 placeholder:

- Equity vs buy-and-hold on one scale, with the y-range derived from the data rather
  than forced to zero — a 100k→101k account is flat, and drawing that as a mountain
  would be a chart that lies.
- Every metric with its definition and, where withheld, its reason.
- Cost breakdown (commission and slippage separately, not lumped).
- Attribution by instrument and by origin.
- The closed-trade list, net of commission.
- A closing panel titled *"What this page refuses to do"*.

`src/lib/demo/analyticsEngine.ts` mirrors the same rules in the browser so the static
demo shows a working report, and states where the demo's derivation is weaker than the
backend's (it reconstructs from orders; the backend rebuilds from the ledger).

## 4. Evidence

| Check | Result |
| --- | --- |
| Backend tests | **392** passing (25 service, 7 API new) |
| Frontend tests | **226** passing (9 engine, 8 page, plus the responsive suite) |
| Migration | none required; autogenerate reports no pending changes at 52 tables |
| API paths | 89 → **92** |
| Smoke | funded 100,000 → buy 100 ANA/T at 100 → mark to 110 → sell → equity 100,992.65, net +0.99%, drawdown **0.00%** (was 10.0% before the fill-reconstruction fix), commission 4.20 + slippage 3.15, FIFO net + commission = position-table realised exactly |

## 5. What a reviewer should try

1. Open **Analytics** on a fresh account: Sharpe, win rate and profit factor read "—"
   with reasons, not `0.00`.
2. Place a buy and a sell on **Execution**, return to **Analytics**: one closed trade
   appears, net of commission, and the cost panel separates commission from slippage.
3. Read the *"Why a number is missing"* panel — it should still be explaining why
   Sharpe is withheld, because two trades are not a sample.
4. Compare the strategy curve to the dashed buy-and-hold line; on a single round trip
   they sit almost on top of each other, and the report says so.

## 6. Responsive pass (cross-cutting)

Shipped with this phase because it could not be deferred any further: **ten panels
across nine pages silently clipped their tables on a narrow screen.** `Panel` sets
`overflow-hidden` so content cannot spill out of the rounded corners, which meant every
table with nowhere to scroll was simply cut off — no scrollbar, no indication, just
missing columns.

Fixed structurally rather than page by page:

- `Panel` gained a `scrollBody` prop; every panel holding a table uses it.
- Three tables outside a `Panel` body (Models' evaluation table, Strategies' returns and
  risk tables) got their own `overflow-x-auto` wrapper.
- Timestamp cells changed from `whitespace-nowrap` to `sm:whitespace-nowrap`, so they
  wrap on a phone and stay on one line on a desktop.
- The Markets data-quality grid falls back to one column below `sm`.
- AIDesk's filter field no longer forces a 14rem minimum inside a narrow flex row.
- Auth screens use tighter padding at the smallest breakpoint.
- The footer no longer claims "Phase 3 Market Data".

Two guards now keep it that way:

| Guard | What it enforces |
| --- | --- |
| `tools/responsive_audit.py` | Flags unprefixed multi-column grids, fixed pixel widths, nowrap cells, and any `Panel` holding a table without `scrollBody` |
| `src/responsive.test.tsx` | Mounts **all 14 pages** at 320px and asserts every table has a scroll container and no element sets a width wider than the viewport |

```
$ python3 tools/responsive_audit.py
No responsive issues found
$ npx vitest run src/responsive.test.tsx
Tests  29 passed (29)
```

jsdom does not lay out, so these prove structure, not appearance. What they do catch is
the class of regression that already happened once.

## 7. Deliberately not in Phase 10

- **The autonomous loop.** Still not built; scheduling trades is Phase 11+ scope.
- **Historical exposure snapshots.** Exposure is reported as a *current* figure and
  labelled as such, because the platform stores no position snapshots to average over.
- **Forward projections, targets or expected returns.** Nothing here is a forecast.
- **An index benchmark.** Deliberately the traded basket instead.
- **Live broker reporting.** No broker is connected.
