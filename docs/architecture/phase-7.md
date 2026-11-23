# Phase 7 — Backtesting & strategy evaluation

**Status:** complete · **Scope owner:** quantitative engineering · **Date:** 23 November 2026

The blueprint's Phase 7 deliverable is:

> Event-driven simulator, costs, slippage and out-of-sample evaluation.

Phase 6 could tell you whether a *model* beat a baseline. Phase 7 answers the harder
question: after spread, commission, slippage and financing, does a **strategy** beat
simply buying and holding the same instrument over the same window?

## 1. Objective

A backtest exists to be disbelieved. Most of them are wrong in one of three
predictable ways, so the harness is built around those three:

| Failure | How this implementation blocks it |
| --- | --- |
| **Look-ahead** — trading at a price that was used to make the decision | Signals from bar *i* fill at bar *i+1*'s open. Nothing is filled at the price that produced it. |
| **Free trading** — costs ignored, or charged once | Spread on both sides, commission per side, slippage per side, financing per bar held. Stored on the run. |
| **Flattering comparison** — measured against zero | Buy-and-hold over the *identical* window with the *identical* cost model is computed and stored beside every result. |

## 2. Delivered scope

### 2.1 Strategies (`app/services/strategies/rules.py`)

Three implemented families, each a pure function of the bar history up to the
decision bar:

| Code | Family | Rule | Purpose |
| --- | --- | --- | --- |
| `buy_and_hold_v1` | buy_and_hold | Long from the first tradable bar to the end | The benchmark. Never stopped out (a benchmark with 64 re-entries is not a benchmark) |
| `trend_ma_v1` | trend | Long while SMA(20) > SMA(60) | The classic, included because it is famously marginal against costs |
| `mean_reversion_v1` | mean_reversion | Long when the 20-bar z-score is ≤ −1.5, exit past −0.3 | The opposite hypothesis, so a harness that only reports "up" is caught |

Each carries a **hypothesis written before the result**, so the write-up cannot be
reverse-engineered from the equity curve.

### 2.2 The simulator (`simulator.py`)

Per bar, in this order:

1. **Resting stop/target resolved against this bar's range.** A bar that trades
   through the stop triggers it even if the close recovers — the single most common
   optimistic bug in amateur backtests.
2. **Previous bar's signal filled at this bar's open**, net of entry costs.
3. **Mark to market** at the close, record drawdown and exposure.

Position size is **risk-based**: quantity is set so a stop-out costs `risk_per_trade`
of current equity. A strategy cannot look good merely by being leveraged during a
calm period.

Closing an open position at the end of data is charged the exit costs, and nothing
is opened on the final bar — there would be no bar to exit into.

### 2.3 Cost model

| Cost | Charged |
| --- | --- |
| Spread | Half the quoted spread on entry, half on exit |
| Commission | Basis points of notional, per side |
| Slippage | Adverse basis points, per side (always against the trader) |
| Financing | Per bar held, on notional |

Every run stores the assumptions that produced it. A result whose costs are not
recorded is an anecdote.

### 2.4 Metrics and the acceptance gate

Recorded per run: trades, win rate, profit factor, average win/loss, expectancy,
gross return, **net return**, **baseline return**, **edge over baseline**, total
costs, cost drag, Sharpe, Sortino, maximum drawdown, volatility, exposure, turnover
and average bars held.

The gate refuses a strategy unless **all** hold:

| Condition | Threshold |
| --- | --- |
| Trades | ≥ 20 (a 9-trade 78% win rate is a story, not a result) |
| Net return | ≥ 0 after costs |
| Edge over buy-and-hold | ≥ +2% |
| Maximum drawdown | better than −25% |
| Sharpe | ≥ 0.30 |

### 2.5 Control flow

- A completed backtest moves a `draft` strategy to `testing` — and no further.
- `validated` is a **staff-only** action that is refused unless a passing backtest
  already exists. A strategy cannot be validated by intent.
- No strategy can become `live_eligible` in Phase 7. There is still no order path.

### 2.6 Persistence

Five tables: `strategies`, `backtests`, `backtest_trades`, `backtest_metrics`,
`backtest_equity_points`. Gross and net P&L are **separate columns on every trade**,
so a strategy that only works before costs is visible as such instead of collapsing
into one flattering number.

### 2.7 API

Seven routes under `/api/v1/strategies`: catalogue, overview, list/get backtests,
run a backtest, latest-per-strategy, validate (staff), retire (staff).

## 3. Evidence from this build

On 720 bars of synthetic BTC/USD with default costs:

```
buy_and_hold_v1      trades=  1  net=+23.4%  edge= +0.00%  gate=Fail (it is the baseline)
trend_ma_v1          trades= 42  net=+15.4%  edge= -8.1%   gate=Fail
mean_reversion_v1    trades= 29  net=+11.9%  edge=-11.5%   gate=Fail
```

Both active strategies are profitable in isolation and both **lose to buy-and-hold**
after costs. That is the correct answer on a random walk, and it is why the
comparison exists. A harness that reported "trend following: +15%, great" without
the baseline would be actively misleading.

## 4. Bugs found and fixed during Phase 7

1. **Double-charged, then zero-charged, spread.** The first version constructed a
   dead expression (`... * 0`) that always contributed zero to the cost column,
   making `total_costs` a lie. Costs are now derived from the difference between the
   raw and adjusted fill, so the column reconciles with the fills.
2. **Buy-and-hold re-entered 64 times.** The benchmark was stopped out and re-bought
   every time price dipped, turning one decision into sixty-four. Benchmarks now run
   without stops, so "buy and hold" means what it says.
3. **Buy-and-hold compared against itself.** The baseline was computed only for
   non-benchmark strategies but still displayed as a 0% comparator elsewhere. It is
   now computed from the same candles and warmup for every run.
4. **A position reopened on the final bar**, leaving a trade with no exit. Nothing
   opens on the last bar now.
5. **Backtests depended on endpoint order.** `POST /strategies/backtests` failed with
   "Unknown strategy" unless the catalogue had been fetched first. The service now
   seeds on first use.

## 5. Deliberately not in Phase 7

- **No parameter optimisation.** Sweeping `fast`/`slow` over this sample would fit
  the noise and destroy the out-of-sample claim that Phase 6 and 7 rest on.
- **No multi-instrument portfolio backtest.** Correlation and capital allocation are
  a portfolio concern that arrives with execution.
- **No walk-forward re-optimisation.** Phase 6 does that for models; doing it for
  strategies without parameter search would be theatre.
- **No orders.** A backtest is a measurement, not a trade.
- **No live-data paper trading.** Phase 8.

## 6. Review checklist

- [x] A signal is filled at the next bar's open (asserted directly in `test_a_signal_is_filled_at_the_next_bar_not_the_current_one`).
- [x] Truncating the future does not change past trades (asserted).
- [x] Stops resolve against the bar's low, not the close (asserted).
- [x] Costs are charged on both sides and always make a trade worse (asserted).
- [x] Every result carries a buy-and-hold comparison over the identical window.
- [x] A small sample cannot pass the gate.
- [x] A backtest cannot validate a strategy; validation is staff-only and needs a passing run.
- [x] `live_eligible` is false for every strategy.
- [x] Cost assumptions are stored with each run.
- [x] No shipped strategy passes the gate on synthetic data, and the UI says so.
