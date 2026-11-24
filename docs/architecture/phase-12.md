# Phase 12 — Readiness

**Blueprint weeks 28–32:** *Broker sandbox integrations, compliance assessment and release
preparation.* This is the final phase in the blueprint's roadmap.

Phase 12 adds almost no capability a trader can see. It adds the ability to **answer the
question "may this ship, and what would have to be true before it could go further?"** —
and to answer it in a way that can be checked rather than believed.

The organising principle, matching Phase 11's: **an assessment that cannot say "no" is
marketing.** So the deliverables are a refusal, an enumeration, and a gate.

---

## 1. Broker integration — sandbox, structurally

`app/services/broker/` contains:

* `base.py` — `BrokerAdapter` (handshake, clock probe, submit), `SimulatedSandboxAdapter`,
  `require_sandbox`, `live_trading_is_impossible`.
* `sandbox.py` — connection lifecycle: connect, probe, submit, disconnect.

### The wall, not a switch

The blueprint asks for *sandbox* integrations. It does not ask for a live broker, and §13
explains why: live execution means client money, custody, permissions and a written
perimeter assessment this project does not have.

So `LIVE_TRADING_ENABLED` is checked in exactly one place, and the live adapter class
**does not exist**:

```python
def require_sandbox(*, mode: str) -> None:
    if mode != "sandbox":
        raise LiveTradingRefused(...)   # -> HTTP 403
```

Tested three ways (`tests/test_broker.py`):

1. `mode="live"` raises;
2. `"LIVE"`, `""`, `"production"`, `"live-ish"` also raise — the check is equality, not a
   prefix;
3. with **both** live flags monkeypatched to `True`, `live_trading_is_impossible()` still
   reports `reachable: False` and `require_sandbox` still raises, because there is no
   adapter to reach.

That is the difference between a kill switch and a wall. A switch can be flipped by
mistake; a wall has to be built.

### What the sandbox actually records

| Field | Why it exists |
|---|---|
| `mode` | `sandbox` is the only value that proceeds; `live` is stored nowhere because it never gets past the gate |
| `api_key_encrypted` / `api_secret_encrypted` | Encrypted with `app.core.secrets` (Phase 11). Cleared on disconnect — a link that is off must not still hold a key |
| `withdraw_allowed` | The blueprint asks for least privilege; a key *with* withdrawal permission is a finding, so it is a column and a compliance item |
| `scopes`, `ip_allowlist` | Recorded as declared, so "scoped credentials" is auditable rather than asserted |
| `broker_sessions` rows | Every handshake, clock probe, submit and disconnect with latency and skew — "we tested connectivity" is a row, not a sentence |

A sandbox order **does not fill and does not create a paper order**. It is a connectivity
and protocol test, not an execution path, and a test asserts that
`/execution/orders` is unchanged after one.

---

## 2. Compliance assessment (`app/services/compliance.py`)

A list of 24 items across the categories in blueprint §13 — perimeter, client money,
promotions, KYC/AML, data protection, market-data licensing, cryptoassets, operations,
broker, release. Each item is:

```json
{"key", "title", "category", "status", "evidence", "blocks_live", "unblocked_by"}
```

`status` ∈ `done | partial | blocked | not_implemented`.

**Every status resolves from evidence in the running system, never from a hardcoded
boolean.** Examples:

* `privacy.audit_trail` → `done` only because `integrity.verify_chain(db).verified` is true;
  break the chain and the item becomes `blocked`.
* `perimeter.non_customer_scope` → `done` only while both live flags are false; flip one and
  it becomes `blocked`.
* `broker.least_privilege_keys` → `blocked` if any stored connection has
  `withdraw_allowed = true`.
* `broker.paper_evidence` → `partial` if orders exist, `not_implemented` if none do.

Where evidence *cannot* exist yet — no counsel opinion, no identity provider, no load test —
the item is `blocked` or `not_implemented` **and names what would unblock it**. "Not ready"
without a next step is a dead end; this is meant to be a working document.

The assessment's headline:

```json
{"live_ready": false,
 "live_ready_reason": "8 blocking item(s) remain. The Phase 12 deliverable is readiness
                       assessment, and the assessment currently answers no."}
```

That is the deliverable. If it ever returns `true`, the docs must change with it — and a
test asserts it returns `false` today.

---

## 3. Release gate (`app/services/release.py`)

A state machine over the blueprint's three business stages, with evidentiary
preconditions:

| Check | Blocking? | Passes when |
|---|---|---|
| `no_live_adapter` | yes | no registered adapter supports live |
| `audit_chain_verifies` | yes | the Phase 11 chain verifies |
| `no_withdrawal_keys` | yes | no stored broker key has withdrawal permission |
| `no_open_critical_breach` | yes | no critical monitoring finding |
| `no_blocking_compliance_items` | **no** | informational — these block *live*, not the paper release |

The distinction between `blocking: true` and `blocking: false` is data, not prose, so a
passing gate still visibly carries `compliance_blocking_count: 8`. A test asserts exactly
that: a green paper-release gate must not read as "ready for live".

`advance_to("stage_3_live")` raises a 403 naming the blocking items and **writes the
refusal to the audit trail**. Stage 3 is not reachable by any input.

---

## 4. The Readiness screen (`/readiness`)

* live-ready **NO** as the headline, not a green badge;
* the blocking list with "unblocked by" for each;
* the gate with blocking/informational labels distinguished;
* the broker catalogue showing `live: not built`;
* the three-stage model with stage 3 marked `not_implemented`;
* the full assessment grouped by category, each row carrying its evidence;
* a *What this page refuses to claim* panel: passing the gate is not a compliance sign-off;
  "not built" ≠ "not needed"; paper evidence is a sample, not a season; the demo's
  assessment is resolved from demo state while the server's is authoritative.

`src/lib/demo/readinessEngine.ts` computes statuses from demo state the same way: break
the local seal chain and `privacy.audit_trail` flips to `blocked` and the gate to `fail`.

---

## 5. Numbers

| | Phase 11 | Phase 12 |
|---|---|---|
| Backend tests | 480 | **527** (+11 broker, +36 readiness API) |
| Frontend tests | 255 / 20 files | **285 / 22 files** (+16 engine, +12 page, +2 responsive) |
| API paths | 107 | **118** |
| Tables | 54 | **56** (`broker_connections`, `broker_sessions`) |
| Migration head | `2c2b8f5f1acb` | `965e4603e834` |

---

## 6. What Phase 12 does not deliver

The blueprint's Phase 12 line is *"broker sandbox integrations, compliance assessment and
release preparation"*. All three are here. What is emphatically **not** here:

* **No live trading, and no path to it.** Documented above; tested from three directions.
* **No real broker contract.** The sandbox adapter is in-process. Connecting to a real
  sandbox (Alpaca, OANDA, Binance testnet) needs credentials, a network policy and a
  vendor-specific adapter — none of which this environment provides.
* **No regulatory opinion.** §13 is an issue map; this module is an engineering
  assessment of *code and data*, and says so on its own output.
* **No counsel, no KYC provider, no load test, no PITR.** All four are `blocked` or
  `not_implemented` in the assessment, with the unblocking condition named.

### The end of the roadmap

Phases 1–12 are the blueprint's full plan (§15). The next step the blueprint describes is
not a phase but a programme: months of paper trading across varied market conditions,
shadow comparison against independent benchmarks, and a written perimeter assessment with
specialist counsel (§15 validation sequence 25–28). No amount of further coding
substitutes for that evidence, and the platform is built to say so rather than to imply
otherwise.
