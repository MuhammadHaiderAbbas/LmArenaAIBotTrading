# Phase 8 — Paper execution

**Status:** complete · **Scope owner:** trading operations · **Date:** 23 November 2026

The blueprint's Phase 8 deliverable is:

> Virtual broker, fills, positions, accounting.

Everything before this phase produced analysis. This is the first and only place in
the platform that can move money — and it moves simulated money, on purpose.

## 1. Objective

A paper execution engine is easy to build in a way that flatters itself: fill
everything instantly at the mid, ignore costs, and report a beautiful P&L. That
engine teaches you nothing. The one implemented here is built to be *uncomfortable*:

| Failure the engine blocks | How |
| --- | --- |
| Trading on a price nobody could have got | Freshness gate — a bar older than its own timeframe (minimum two minutes, measured from the bar's **close**) is refused. |
| Going short without borrowing rules | Shorts are refused by name; the book is long-only and leverage is disabled per the blueprint default. |
| Orders appearing from nowhere | Only `create_order` → `validate_proposal` → `execute_order` can produce an `Order`; only an `Order` can move money. |
| Positions that silently disagree with cash | Every fill posts a balanced double-entry transaction; `reconcile_positions` compares the position view against `INVESTMENT` and `REALISED_PNL`. |
| Retrying a submission into a double position | `client_order_id` is unique per account; a repeat returns the original order. |
| Instant fills at one price | Orders larger than 15% of median 50-bar volume fill in up to four bites, each at a worse price. |

## 2. Delivered scope

### 2.1 Models (`app/models/execution.py`, 4 tables)

| Table | Holds |
| --- | --- |
| `orders` | Intent and lifecycle: side, type, TIF, status, quantity, filled quantity, costs, `reject_reason`, `origin` (`manual` or a strategy code), `client_order_id` |
| `order_fills` | One row per bite: quantity, price, `reference_price`, measured `slippage`, `commission`, notional, the ledger transaction it posted |
| `positions` | The long-only book: quantity, average entry, cost basis, realised P&L, last mark — a **view** over the ledger, reconciled not trusted |
| `execution_events` | Append-only lifecycle log: submitted, validated, rejected, acknowledged, partial fill, filled, cancelled, expired, position opened/closed |

`TERMINAL_STATUSES` (`filled`, `rejected`, `cancelled`, `expired`) is the single
authority on when an order stops being actionable; expiry and cancellation both read it.

### 2.2 Validation (`app/services/execution/orders.py`)

`validate_proposal` runs a fixed, deterministic sequence and returns the full check
trace, not just a boolean:

1. Instrument exists and has bars for the requested timeframe
2. **Data freshness** — bar age ≤ `max(timeframe, 120s)`, measured from the bar's close
3. No unresolved `CRITICAL` `DataQualityEvent` for the instrument
4. Quantity is positive and finite
5. Affordability — notional + commission ≤ cash (no leverage)
6. Sells cannot exceed the quantity held (no shorts)
7. A limit price, if given, is positive

A refusal is a normal outcome, not an exception: the order is stored with
`status=REJECTED`, the reason in plain English, and the check trace in its event
detail. **A rejected order creates no position and moves no money** — asserted
directly in `test_a_rejected_order_creates_no_position`.

### 2.3 Simulated fills (`app/services/execution/broker.py`)

| Parameter | Value |
| --- | --- |
| Participation cap | 15% of median 50-bar volume |
| Maximum bites | 4 |
| Slippage | 1.5 bps, degrading 50% per additional bite; stores measured slippage per fill |
| Commission | 2 bps of notional, per fill |
| Limit orders | Fill only if marketable against the latest close; otherwise rest `SUBMITTED` |

Buy fills land at or above the mid, sells at or below — the direction of the cost is
asserted in the tests rather than assumed.

### 2.4 Accounting

Per fill, one balanced ledger transaction (`idempotency_key=f"fill:{fill.id}"`):

- **Buy** — debit `INVESTMENT`, credit `CASH`; commission debits `FEE_EXPENSE`, credits `CASH`.
- **Sell** — credit `CASH`, debit `INVESTMENT` for the released basis, with the
  remainder posted to `REALISED_PNL`.

`reconcile_positions` sums cost basis and realised P&L over **all** positions — open
and closed — because a closed position's realised P&L is invisible if you only look
at the open book. That bug was found by the smoke test before it was found by a user.

### 2.5 API (8 paths)

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/execution/orders` | Submit a proposal; returns the order with fills and lifecycle events |
| `GET` | `/execution/orders` | Recent orders, optionally filtered by status |
| `GET` | `/execution/orders/{id}` | One order with its fills and event log |
| `POST` | `/execution/orders/{id}/cancel` | Cancel a resting order |
| `GET` | `/execution/positions` | Open or closed positions, marked to the latest close |
| `GET` | `/execution/positions/{id}` | One position with unrealised percentage |
| `GET` | `/execution/overview` | Equity, cash, costs, rejection rate, reconciliation report |
| `POST` | `/execution/strategy-orders` | Ask a **validated** strategy for a proposal and submit it |

Order submission is rate-limited (20/minute; 10/minute for strategy orders) and every
route enforces account ownership — an intruder sees `404`, not another user's fills.

### 2.6 Strategy bridge (`strategy_bridge.py`)

The blueprint boundary is explicit: agents create structured **proposals**, a
deterministic service validates them, and only the execution service submits. The
bridge enforces it:

- `StrategyStatus.VALIDATED` is required. A `testing` strategy — one that merely
  backtested — is refused, because otherwise the Phase 7 gate would be decorative.
- Long-only. A flat or short signal produces no order, with the reason returned.
- Sizing is risk-based: `equity × risk_per_trade / (price × stop_pct)`.
- `client_order_id = f"{code}:{symbol}:{date}"`, so a strategy cannot double up by
  running twice in one day.

## 3. Deliberate exclusions

| Not built | Why |
| --- | --- |
| **Autonomous loop / scheduler** | The blueprint's autonomous loop requires the independent risk controller of Phase 9. Shipping an unattended order submitter before the controller that can veto it would invert the safety ordering. |
| **Risk limits** (0.5% per trade, 2% daily loss, 10% drawdown, 5 positions, 50% exposure) | Phase 9. They are *not* smuggled in as validated checks here, because a half-implemented risk engine claiming to enforce limits is worse than an absent one. |
| **Shorts and leverage** | Need borrow/locate and financing rules this phase does not model. Refused by name. |
| **Stop-loss enforcement** | A risk-control concern; belongs with the Phase 9 limits engine. |
| **Broker adapters** | No credentials, no vendor integration, no live order path. The venue is a simulator and every screen says so. |

## 4. Frontend

`/trading` now renders the **Execution** screen (`src/pages/Execution.tsx`), backed by
`src/lib/demo/executionEngine.ts` — the same rules reimplemented in the browser so the
static demo is a working walkthrough rather than a screenshot. It shows equity,
unrealised and realised P&L, open positions, the full order history with rejection
reasons, commission and slippage totals, and states plainly on the page that no
broker is connected and what Phase 8 does not do.

## 5. Evidence

| Check | Result |
| --- | --- |
| Backend tests | **318** passing (40 new for Phase 8) |
| Frontend tests | **156** passing (23 new) |
| Migration | `2950e651c5a3` after `ff9b9b408cf1`; 44 → **48** tables; upgrade → downgrade → upgrade verified |
| Smoke (temp SQLite) | 50,000 funding → buy 0.05 BTC/USD filled at 61,230.98 in one bite, commission 0.61 → basis 3,061.55 → sell → cash 49,997.86, realised −0.92 → reconciliation agrees on both sides, trial balance 0 |

The realised P&L on the round trip is **negative**. That is the honest result: two
sides of commission and slippage on a flat market cost money, and an engine that
reported a profit there would be lying.

## 6. What a reviewer should try

1. Open **Execution**, buy 1 unit, and watch cash fall by notional *and* commission.
2. Submit an order for a million units — read the refusal, which quotes what the order
   needs and what the account holds.
3. Try to sell something not held — the refusal names the borrow rule that does not exist.
4. Place a limit order far from the market — it rests; cancel it and check the event log.
5. Compare **Equity** to cash + positions value in the footer: they reconcile because
   both trace to the same ledger entries.
