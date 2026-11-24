# AUREX AI — delivery record

**Version 0.1.0 · all twelve blueprint phases · paper trading only · no real money · no broker execution**

This document is the handover. It states what was built, what was verified and how, and —
because this project is built to be honest about its own limits — what is *not* done and
who would have to do it.

Start with §1 if you want to run it. Read §5 before you believe anything about it.

---

## 1. Run it

### Docker (recommended)

```bash
cp .env.example .env          # then change SECRET_KEY
docker compose up --build
```

Open <http://localhost:5173>, sign in as `demo@aurex.ai` / `Demo!Aurex2026` (or
`admin@aurex.ai` / `ChangeMe!2026` for the staff screens).

The API container runs `backend/entrypoint.sh`, which does exactly three things in order:

1. `alembic upgrade head` — schema comes from migration history, never `create_all`
2. `python -m app.seed` — admin, demo trader, second account, registry, agents, sources
3. `uvicorn` — the API

Verified: a blank database taken through that entrypoint ends at migration head
`965e4603e834`, 56 tables, 2 users, 2160 market bars, and a passing release gate.

### Without Docker

See README §Quickstart. The three commands are the same three the entrypoint runs, so a
local run and a deployed run take an identical path.

### Verify the install

```bash
python3 tools/smoke_e2e.py http://localhost:8000
```

37 checks across all twelve phases over real HTTP. Exits non-zero on failure, so it works
as a release gate. Current result: **37 passed, 0 failed**.

---

## 2. What is in the box

| | Count |
|---|---|
| Blueprint phases delivered | 12 / 12 |
| Backend tests | **552** |
| Frontend tests | **285** in 22 files |
| API endpoints | **118** |
| Database tables | **56** |
| Alembic migrations | 12, reversible, verified up/down/up |
| Documented screens | 17, all held to a 320 px responsive contract |

### The twelve phases

| Phase | Delivered | Doc |
|---|---|---|
| 1 Foundation | Repo, Docker, PostgreSQL, auth, migrations, API structure | [phase-1](docs/architecture/phase-1.md) |
| 2 Frontend | Design system, navigation, dashboard, account screens | [phase-2](docs/architecture/phase-2.md) |
| 3 Market data | Registry, provider adapters, ingestion, quality, indicators | [phase-3](docs/architecture/phase-3.md) |
| 4 Research | Ingestion, extraction, retrieval, source-linked reports | [phase-4](docs/architecture/phase-4.md) |
| 5 AI agents | Orchestrator and specialist agents with structured output | [phase-5](docs/architecture/phase-5.md) |
| 6 Quant models | Features, training, validation, drift monitoring | [phase-6](docs/architecture/phase-6.md) |
| 7 Backtesting | Event-driven simulator with costs, slippage, out-of-sample | [phase-7](docs/architecture/phase-7.md) |
| 8 Paper execution | Order lifecycle, simulated fills, positions, accounting | [phase-8](docs/architecture/phase-8.md) |
| 9 Risk engine | 12 rules in fixed order, breakers, emergency shutdown | [phase-9](docs/architecture/phase-9.md) |
| 10 Analytics | Net return, Sharpe, Sortino, drawdown, attribution | [phase-10](docs/architecture/phase-10.md) |
| 11 Security | Hash-chained audit, MFA, secrets at rest, backups, monitoring | [phase-11](docs/architecture/phase-11.md) |
| 12 Readiness | Broker sandbox, compliance assessment, release gate | [phase-12](docs/architecture/phase-12.md) |

---

## 3. How it was verified

The test suite alone is not evidence that a deliverable works — it proves the code does
what its tests say. So the delivery checks were run separately, against a real database
and a real server:

| Check | Method | Result |
|---|---|---|
| Schema from scratch | 12 migrations on a blank database, then down/up | head `965e4603e834`, 56 tables |
| Container boot path | `entrypoint.sh` against a blank SQLite database | migrate → seed → serve, 0 errors |
| Seeding idempotency | `python -m app.seed` run twice | second run creates nothing, updates registries only |
| Live API, all phases | `tools/smoke_e2e.py` over HTTP | 37/37 |
| Documentation completeness | `tests/test_contract.py` diffs OpenAPI against `docs/api/README.md` | 118/118 endpoints documented, 0 phantom routes |
| Identity consistency | every public surface (`/`, `/system/meta`, `/operations/health`) | all agree: Phase 12 |
| Audit chain | seal → verify → tamper → verify | detects edit, deletion and re-ordering |
| Recovery drill | `python -m app.cli drill` | backup → restore into scratch → verified |
| Live-trading refusal | 3 independent routes, incl. both flags forced true | refused every time, HTTP 403 |

Three real defects were found and fixed during this pass — they are listed in §6 because
a delivery record that only lists successes is not a record.

---

## 4. Design principles the code actually enforces

These are not aspirations in a README; each one has a test that fails if it stops being
true.

1. **No metric the sample cannot support.** Analytics returns `null` with a reason, never a
   zero, and the UI renders `—`. A five-trade sample does not get a Sharpe ratio.
2. **No AI proposal is tradeable.** Every run stores `tradeable: false` with the reason,
   and there is no code path from a proposal to an order.
3. **Money moves only through the ledger.** Every fill posts a balanced double-entry
   transaction; positions are a view over postings, reconciled in a test.
4. **Risk is independent and never liquidates.** 12 rules in fixed order; the emergency
   stop halts new risk and reports `closes_positions: false`.
5. **The audit trail is tamper-evident, and says what that means.** A hash chain detects
   edits and deletions; it cannot prevent re-sealing by someone with write access, and the
   API and UI both say so.
6. **Live trading is a wall, not a switch.** No live adapter exists. `LIVE_TRADING_ENABLED`
   is checked in one place and refused unconditionally.
7. **The readiness assessment answers no.** `live_ready: false`, eight blocking items, each
   naming what would unblock it.
8. **Generated data is labelled.** The offline provider is called `synthetic`, every bar
   records it, the seed warns, and it is never selected automatically.

---

## 5. What is **not** delivered

Read this before quoting any output of this system.

### Not built, and required before customer money

- **UK regulatory perimeter assessment.** Blueprint §13 is an issue map, not legal advice.
  A written opinion from specialist counsel is a prerequisite, not a nice-to-have.
- **KYC / AML / sanctions screening.** Accounts are self-service and unverified. Acceptable
  only because no customer money is involved.
- **Client-money safeguarding and custody.** No funding rail, no withdrawal path, no
  custody, nothing to safeguard.
- **UK GDPR subject-access and erasure.** No export or erasure workflow exists.
- **Financial-promotion sign-off.** No marketing copy has been reviewed.
- **Live broker execution.** Not implemented, not stubbed, not flag-gated. No adapter
  exists that could reach a real market.
- **Point-in-time recovery.** Backups restore to the moment of the dump, not an arbitrary
  second before an incident.
- **Load and reliability testing.** The suite is a functional gate. Throughput, latency and
  connection-pool behaviour under concurrency are **unmeasured**.
- **CI running on push.** The workflow is documented in `docs/deployment/CI_WORKFLOWS.md`
  but `.github/workflows/ci.yml` cannot be restored without a `workflow`-scoped token.
- **MFA enforcement.** TOTP and recovery codes are implemented and a challenged login
  really does withhold tokens. But the *policy* is reported, not enforced: a staff account
  without MFA is told so and is not blocked.

### Not claimed

- **No performance claim anywhere.** Every analytics report carries `forward_looking:
  false`; the sample is small; historical and simulated results do not predict live
  outcomes.
- **No evidence of profitability.** The blueprint (§15, steps 25–28) asks for months of
  paper trading across varied conditions, shadow benchmarking against independent
  benchmarks, and long-duration incident testing. This build has a test suite, not a track
  record.
- **The synthetic provider's bars are generated, not market data.** Any result computed
  from them is meaningless as evidence.

---

## 6. Defects found and fixed during delivery

Recorded because they are the most useful part of this document.

1. **The API said Phase 11 after Phase 12 shipped.** The phase string was hardcoded in
   four modules and drifted; the frontend and docs said 12, the API said 11. Fixed by
   moving it to `app/core/release.py` as one setting, and adding a contract test that all
   public surfaces agree. *The existing tests passed throughout, because each asserted the
   literal it had been written against.*

2. **`docs/api/README.md` promised four endpoints that were never built** — `/markets/reseed`,
   `/markets/{symbol}/quality`, `/research/claims`, `/research/verify` — while 22 real
   endpoints went undocumented. Found by diffing the OpenAPI spec against the docs in both
   directions. `tests/test_contract.py` now fails on either kind of drift.

3. **A fresh install reported CRITICAL and failed its own release gate.** Seeding created
   18 instruments with zero market data, so `data_freshness` was critical, `no_open_critical_breach`
   failed, and the gate returned `fail` on day one. Unit tests never saw it because they
   build their own fixtures. Fixed with an explicitly-labelled opt-in synthetic provider
   (§4.8), not by weakening the check.

4. **The container never ran migrations.** `Dockerfile` seeded with `create_all`, which
   silently diverges on an existing database. Replaced with `entrypoint.sh`:
   migrate → seed → serve, with retry for a cold PostgreSQL.

---

## 7. Where to look next

| Question | File |
|---|---|
| How do I call the API? | [`docs/api/README.md`](docs/api/README.md) — all 118 endpoints |
| How is it deployed? | [`docs/deployment/HOSTING.md`](docs/deployment/HOSTING.md) |
| What are the security controls? | [`docs/security/README.md`](docs/security/README.md), [phase-11](docs/architecture/phase-11.md) |
| What are the compliance gaps? | [phase-12](docs/architecture/phase-12.md), or `GET /readiness/compliance` live |
| What does it refuse to do? | The "What this page refuses to claim" panel on `/analytics`, `/operations` and `/readiness` |

---

## 8. The honest summary

A complete twelve-phase implementation of the blueprint: a working paper-trading platform
with market data, research, AI agents, models, backtesting, execution, risk, analytics,
security and a readiness assessment — fully tested, fully documented, and deliberately
built so that the gap between what it does and what it *claims* is as small as the authors
could make it.

It is **not** a product that may take customer money. It is not close to one. The
distance is not code; it is counsel, authorisation, an identity provider, operational
evidence over months, and a load test against something that looks like production.

Everything this document says it does, it does today, verified on a clean install.
Everything it says it does not do, it does not do — and the software says so too, on
screens a reviewer can open.
