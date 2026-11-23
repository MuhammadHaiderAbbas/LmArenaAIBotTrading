# Phase 5 — AI orchestrator and specialist agents

Phase 4 built the evidence layer. Phase 5 puts specialists on top of it and an
orchestrator above them. The design goal was not "add agents". It was:

> A specialist must be able to say **"I have no data"**, and the desk must be able
> to say **"we decline to propose a stance"**. A trading assistant that always
> finds an opinion is lying about its information.

Everything below follows from that.

---

## 1. The agents

| Agent | Input | Output | Stance? |
| --- | --- | --- | --- |
| `technical` | OHLCV candles, SMA/EMA/RSI/MACD/ATR/Bollinger | Trend/momentum read with per-indicator evidence | **yes** |
| `regime` | Realised volatility history | High/normal/low regime + drift | no |
| `news` | Phase 4 claims, verdicts, source tiers | Verification coverage and source quality | no |
| `portfolio` | Reconciled ledger balances | Exposure and concentration | no |
| `quality` | Phase 3 data-quality events | Quality state and the events behind it | no |

Registered but **not built** — and returned by the API as `implemented: false`:
`fundamental`, `sentiment`, `quantitative`. The UI renders them as *planned*. A
platform that shows an eight-agent roster while running five is misrepresenting
itself.

Why only one agent votes on direction: direction is the part of the pipeline that
needs validation against outcomes. Adding more opinionated agents before Phase 6
would multiply confidence without adding evidence. The other four supply context
and constraints, which is what they can honestly do today.

---

## 2. Structured findings

Every finding is a row in `ai_findings` with these fields kept separate:

- `facts` — measured values, one per line ("SMA(20) 1.30742")
- `interpretation` — the judgement drawn from them
- `uncertainties` — what would weaken it
- `limitations` — what the agent structurally cannot see
- `confidence` + `confidence_kind`
- `data_available`

Splitting these is the whole point. Merging them produces prose that reads like
analysis while hiding which sentences are measurements and which are opinions.

### Evidence, including absence

Each assertion points at an `ai_evidence` row: `research_claim`, `market_candle`,
`indicator`, `quality_event`, `computation` — or **`absence`**.

`absence` is a first-class kind, not an empty list. When the technical agent has
no candles it writes:

> No candles are stored for this instrument and timeframe, so no technical read is
> possible. **This is a data gap, not a neutral signal.**

An empty evidence list would be indistinguishable from "considered and found
nothing", which is a different and much stronger claim.

### Confidence is labelled, not implied

Every Phase 5 confidence is `rule_score`: a documented scoring rule in [0, 1].
Storing `confidence_kind` beside the number means a UI or an API consumer can see
that it is *not* a calibrated probability. Calibration is Phase 6 work and needs
outcome data this system does not have yet.

---

## 3. Orchestration

`orchestrator.run_agents()` executes the specialists, persists every finding, then
synthesises. Synthesis rules:

1. **Only `technical` votes on direction.**
2. **The news agent can only ever damp confidence, never set a stance.** Text →
   direction needs a validated model.
3. **Disagreement is recorded, not averaged.** A trend signal under a
   high-volatility regime sets `proposal_conflict = true` and multiplies
   confidence by 0.6. The UI shows the conflict explicitly.
4. **Below the floor, refuse.** Combined confidence under 0.35 yields
   `stance = unknown`.
5. **No usable evidence → `status = refused`** with the reason stored on the run.

The `quality` agent is excluded from the "is there any evidence" test. It always
has something to say about data, so counting it made an otherwise empty run report
`partial` instead of `refused` — a bug the tests caught.

### Nothing executes

Every run stores `tradeable = false` and a `blocked_reason` naming Phase 9's
independent risk controller. This is enforced in three places: the orchestrator
always sets it, a test asserts it across symbols, and `/ai/overview` counts
`tradeable_proposals` so a future regression is visible on a dashboard rather than
buried in code review.

A proposal here is a **record**, not an instruction. There is no order path in
Phase 5 — not a disabled one, an absent one.

---

## 4. Run lifecycle

```
POST /ai/run
  → build context (candles, indicators, quality, research, portfolio)
  → run each agent, persisting finding + evidence as we go
  → an agent that raises is recorded as a failed finding, not a failed run
  → synthesise
  → status: completed | partial | refused
```

`partial` means some specialists had no usable data. It is the honest middle
state and the UI shows it.

---

## 5. API

Seven paths under `/api/v1/ai`:

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/agents` | includes unimplemented agents, explicitly marked |
| POST | `/agents/seed` | staff |
| GET | `/overview` | run counts, refusals, `tradeable_proposals` |
| GET | `/runs` | filter by symbol |
| GET | `/runs/{id}` | full findings and evidence |
| POST | `/run` | rate-limited; verifies account ownership |
| GET | `/proposals` | directional runs, all `tradeable: false` |

---

## 6. Schema

Alembic `c3060c9ff8bf` adds four tables (34 total):

- `ai_agents` — registry, with `implemented` and `deterministic` flags
- `ai_runs` — one orchestrated pass, including the proposal record
- `ai_findings` — structured finding per agent per run
- `ai_evidence` — pointers to supporting material, including `absence`

Findings are immutable once written. A re-run creates a new run; editing a finding
would destroy the audit trail that makes a reviewable system possible.

---

## 7. Frontend

The **AI Desk** (`/ai`) shows:

- instrument + question, then **Run specialists**
- a proposal header with status, stance, confidence **and its kind**, and the
  blocked reason
- a conflict banner when agents disagree about condition
- expandable finding cards: measured facts, interpretation, uncertainty,
  evidence rows (with source links), limitations
- a specialist roster split into **live** and **planned**
- desk activity: runs, refusals, partials, and `tradeable` (always 0)

---

## 8. What this phase does not do

- **No LLM writes the findings.** Every agent is a deterministic rule set. The
  output is honest about being shallow rather than dressing shallow judgement in
  fluent prose.
- **No calibrated confidence.** `rule_score` is not a probability.
- **No execution, no risk controller.** Phase 9.
- **No fundamental/sentiment/quantitative agents.** They are registered and
  visibly unbuilt.
- **No portfolios.** Positions arrive in Phase 8; `gross_exposure` is therefore 0
  by construction and the portfolio agent says so rather than implying a flat book.
- **No scheduling.** Runs are manual; `RunTrigger` has the enum values ready.

---

## 9. Tests

48 Phase 5 backend tests + 25 frontend tests (200 backend, 82 frontend total):

- `tests/test_ai_agents.py` — each agent's directional read, absence handling for
  every agent, confidence monoticity in data quality and history length, the news
  agent's refusal to map text to direction, evidence coverage, orchestration
  refusal, conflict detection, agent-failure isolation, the never-tradeable
  invariant, and registry idempotency.
- `tests/test_ai_api.py` — auth, staff gating, unimplemented agents marked,
  run lifecycle, 404s, ownership of account context, rate limiting, overview
  counters, proposal filtering.
- `src/lib/demo/aiEngine.test.ts` — deterministic runs, absence vs neutral,
  never tradeable, question recorded but not obeyed.
- `src/pages/AIDesk.test.tsx` — the screen renders, runs, expands findings, shows
  evidence and limitations, and does not offer instruments the engine cannot price.

### Bugs these caught

1. A 30-bar history scored **higher** confidence than a 120-bar one, because the
   unavailable 50-period average silently removed a corroborating check.
   Confidence is now *capped* when history is short, not merely reduced.
2. An empty run reported `partial` rather than `refused`, because the quality
   agent always produces a finding.
3. The demo screen offered `SPX500` and `XAUUSD`, which the demo engine cannot
   price — producing a refusal that would look like a data problem rather than a
   missing instrument.
