# Phase 9 — Risk engine

**Status:** complete · **Scope owner:** trading operations · **Date:** 23 November 2026

The blueprint's Phase 9 deliverable is:

> Limits, circuit breakers and emergency shutdown.

Phase 8 built a venue that can move simulated money. This phase builds the thing that
decides whether it is allowed to — and, crucially, it is a *separate service* that the
order path cannot skip.

## 1. Objective

The blueprint is unambiguous about the ordering of authority:

> AI proposes; deterministic risk controls decide whether an order is allowed.

> Risk controls must be deterministic, independently testable and enforced immediately
> before order submission.

So the engine is designed around three properties, each with a test that would fail if
it were quietly lost:

| Property | Why it matters | Test |
| --- | --- | --- |
| **Independence** | A risk check folded into the execution service is a check the execution service can be refactored around. | `test_risk_replaces_the_cheap_check_when_cash_covers_the_order` — cash would allow the order; only risk refuses it |
| **Determinism** | A probabilistic or adaptive limit cannot be audited or replayed. | `test_the_decision_is_deterministic` — same state, same answer, twice |
| **Fail-closed** | A risk engine that fails open is compliance theatre. | `test_a_missing_price_is_refused_not_approved` — an unvaluable order is refused, not waved through |

## 2. The two gates, and why both exist

A common mistake is to treat "the account has the cash" as a risk control. It is not.
Execution validation answers *can this account pay for this order?* The risk controller
answers *should this account be taking this risk at all, right now?* Neither subsumes
the other, and the order path runs both, in that order:

```
Proposal → execution validation (well-formed, affordable, no shorts, fresh data)
         → risk controller (limits, breakers, sizing)
         → Order
```

`POST /execution/orders` cannot reach the venue without a risk decision, and the check
is recorded whatever the outcome.

## 3. Delivered scope

### 3.1 Tables (4, `app/models/risk.py`)

| Table | Holds |
| --- | --- |
| `risk_policies` | Named, versioned limit sets per account. A change is a new row, never an edit — loosening a limit must be visible afterwards |
| `risk_checks` | Immutable record of one evaluation: every rule with observed value, limit and verdict, plus the equity/exposure/position **snapshot** it was measured against |
| `risk_events` | Append-only narrative: checks, breaker transitions, policy changes, clears |
| `circuit_breaker_events` | Breaker state transitions, what was cancelled, what remains exposed, who cleared it |

There is deliberately **no** table holding a mutable "risk level" that anything can
overwrite. Every decision is recomputed from observable state and stored.

### 3.2 Rules (`app/services/risk/engine.py`)

Evaluated in a fixed order, cheapest and most fatal first. A rejection stops
evaluation, so the reason shown is the *first binding constraint* rather than a wall of
noise — but everything evaluated before it is still recorded.

| # | Rule | Behaviour |
| --- | --- | --- |
| 0 | **Valuation** | No price → refuse. Fail closed |
| 1 | **Kill switch** | A tripped breaker outranks every limit |
| 2 | **Order rate** | The runaway-loop guard: 30 orders/hour |
| 3 | **Daily loss** | 2% of equity realised today → refuse until tomorrow |
| 4 | **Drawdown** | 10% from peak equity → refuse |
| 5 | **Leverage** | Notional > equity with leverage off → refuse |
| 6 | **Per-trade risk** | 0.5% of equity, measured against the **stop**, not the notional → **reduces** rather than refuses |
| 7 | **Position count** | 5 distinct instruments; adds to an existing position stay permitted |
| 8 | **Exposure** | 50% of equity invested |
| 9 | **Concentration** | 30% in one instrument |
| 10 | **Correlation** | 0.85 against the held book — two positions that move together are one bet |
| 11 | **Spread** | Modelled execution cost against the configured threshold |

Three of these deserve comment:

- **Per-trade risk is measured against the stop.** A £100k trade with a 0.1% stop is a
  £100 risk, not a £100k one. Sizing on notional would refuse perfectly reasonable
  trades and call it prudence.
- **The engine can reduce, not only refuse.** An order slightly too large is shrunk to
  the budget (`allow_reduced`) instead of being thrown away. The original requested
  quantity stays in the event log, so the reduction is auditable.
- **Sells pass the buy-side limits.** Closing risk must always be possible; only
  opening it is limited.

### 3.3 Arithmetic coherence

The defaults are checked against each other, not just chosen:

> A full-risk trade is `0.5% ÷ 2% = 25%` of equity in notional.

A concentration cap of 20% would therefore refuse *every* correctly sized trade while
looking prudent — the worst kind of limit, one that never binds on the behaviour it
claims to control. The cap is 30%, and `test_limits_are_arithmetically_coherent`
asserts the relationship rather than the constant.

### 3.4 Circuit breaker (`app/services/risk/breakers.py`)

Two states, deliberately distinct:

- **`OPEN`** — tripped by a metric (drawdown or daily loss). May be cleared
  automatically on recovery, or by a human.
- **`MANUAL`** — a human pressed the button. Only a human clears it. Collapsing the two
  would let an automatic reset silently undo an emergency stop, so
  `test_a_manual_stop_halts_new_risk_and_is_not_cleared_by_a_metric` guards it.

### 3.5 Emergency shutdown

Each blueprint requirement, and what it actually does here:

| Blueprint requirement | Implementation |
| --- | --- |
| Reject all new orders and strategy submissions | `MANUAL` breaker, checked before risk evaluation and again inside it |
| Cancel eligible pending orders where safe | Cancels new/validated/submitted/partially-filled; a cancel failure is recorded in the event rather than aborting the trip |
| Freeze autonomous execution | There is no autonomous loop to freeze — Phase 8 shipped none deliberately. The API says so rather than toggling a decorative flag |
| Reconcile open positions and outstanding orders | Counted and stored on the breaker row (`positions_affected`, `orders_cancelled`) |
| Notify and create an auditable risk event | `RiskEvent` at severity `critical`, with the exposure left open |
| Require explicit authorised action to resume | `POST /risk/resume` is staff-only, attributed, and raises if the breaker is `MANUAL` and the caller is not authorised |

**The stop halts new risk and does not liquidate.** The blueprint is explicit that
blind liquidation in an illiquid or disrupted market can create the loss the shutdown
exists to prevent. `test_the_stop_distinguishes_halting_from_liquidating` asserts that
positions stay open and cash does not move; `RiskStatus.closes_positions` is in the API
contract, always `false`, so no client can infer otherwise from a green light.

### 3.6 API (9 paths under `/risk`)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/risk/status` | bearer | Posture: breaker, limits, applied rules, live metrics |
| `GET` | `/risk/policy` | bearer | The active policy |
| `PUT` | `/risk/policy` | **staff** | Replace the policy (new version, reason recorded) |
| `GET` | `/risk/checks` | bearer | Decision history with full traces |
| `GET` | `/risk/events` | bearer | Risk event log |
| `POST` | `/risk/evaluate` | bearer | Dry run — no order, no persistence |
| `POST` | `/risk/emergency-stop` | **staff** | Engage the shutdown |
| `POST` | `/risk/resume` | **staff** | Clear the halt |
| `GET` | `/risk/breakers` | bearer | Breaker history |
| `GET` | `/risk/overview` | bearer | Counts, breach frequency, rules not implemented |

`rules_not_implemented` is **computed** from the `RiskRule` enum rather than hardcoded
to `[]`, so the honest gap list cannot go stale if a rule is added later.

## 4. Bugs found and fixed while building this phase

Recorded because each one would have made the engine quietly useless:

1. **Refusals were never persisted.** All 13 early-exit paths returned before the
   write block, so the audit trail was empty for exactly the decisions worth auditing.
   Fixed structurally: `_evaluate` returns a decision, and a single wrapper records it,
   so no code path can skip persistence.
2. **Drawdown read zero.** The equity curve was sampled per *day* and clipped to a
   90-day window, so a funding and a same-day loss collapsed into one sample and the
   high-water mark was never seen — silently disabling the limit. Now sampled per
   transaction from the account's first entry.
3. **Correlation cap vs. risk budget.** With 20% concentration every full-risk trade was
   refused; the limits contradicted each other (see §3.3).
4. **Ledger sign convention.** The drawdown fixture initially *credited* cash for a
   loss. The ledger is debit-positive; a loss debits P&L and credits cash.

## 5. Frontend

`/risk` now renders the real **Risk Centre** (`src/pages/Risk.tsx`), replacing the
Phase 1 placeholder. It shows every limit with its meaning, live exposure and
drawdown against their limits, the reason for each recorded decision, the emergency
stop with its scope stated on the button's panel, and a section titled **"What this
controller is not"**.

`src/lib/demo/riskEngine.ts` mirrors the server rules in the browser and is wired into
the demo execution engine, so an order placed on the Execution screen is gated by the
same controller a reviewer can then inspect on the Risk Centre.

## 6. Evidence

| Check | Result |
| --- | --- |
| Backend tests | **360** passing (29 new for the engine, 13 for the API) |
| Frontend tests | **180** passing (24 new) |
| Migration | `bf24b2c1f957` after `2950e651c5a3`; 48 → **52** tables; upgrade → downgrade → upgrade verified |
| API paths | 80 → **89** |
| Smoke | equity 100,000 → 400-unit buy **reduced to 250** by the per-trade budget → stop engaged (`positions_affected: 1`, position still open) → new order refused → cleared → breaker closed |

## 7. What a reviewer should try

1. Open **Execution**, buy 1 unit, then open **Risk Centre** and read the recorded
   check with its equity and exposure snapshot.
2. Request a large-but-affordable order and watch the decision read `allow_reduced`
   with the exact arithmetic in the reason.
3. Engage the **emergency stop**, then try to place an order — the refusal names the
   breaker, not a generic error.
4. Confirm the open position is still open while halted: the page says so explicitly.
5. Clear the halt and note the attributed `breaker_cleared` event.

## 8. Deliberately not in Phase 9

- **The autonomous loop.** Now that the controller exists, a scheduler *could* be safe
  — but the loop is Phase 10+ work and running strategies unattended is a scope
  decision, not a checkbox. Nothing schedules orders today.
- **Portfolio-level correlation matrices and factor exposure.** The rule compares
  candidate returns against held positions pairwise; a full covariance model is not
  built.
- **Automatic liquidation.** By design, and by blueprint instruction.
- **Live broker risk checks.** No broker is connected.
