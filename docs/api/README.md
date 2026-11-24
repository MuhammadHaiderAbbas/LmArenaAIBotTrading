# API reference — v1

Base URL: `/api/v1` · OpenAPI: `/docs`, `/redoc`, `/openapi.json`

All responses are JSON. Authenticated endpoints require `Authorization: Bearer <access_token>`.
Errors return `{"detail": "<message>"}`; validation failures additionally include
`{"errors": [{"field": "...", "message": "..."}]}`.

## System

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/system/health` | none | Liveness + database probe + guardrail flags |
| GET | `/system/meta` | none | Product metadata consumed by the frontend |

## Authentication

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/auth/register` | none | Create user + funded paper account (rate limited 5/min) |
| POST | `/auth/login` | none | Exchange credentials for an access/refresh pair (rate limited 10/min) |
| POST | `/auth/refresh` | none | Rotate a refresh token; revokes the presented one |
| POST | `/auth/logout` | bearer | Revoke the supplied refresh session |
| GET | `/auth/me` | bearer | Current profile |

## Users

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/users/me` | bearer | Profile + preferences |
| PATCH | `/users/me` | bearer | Update name, currency, timezone, alert preferences |
| POST | `/users/me/password` | bearer | Change password; revokes all other sessions |

## Accounts & ledger

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/accounts` | bearer | Accounts owned by the caller |
| POST | `/accounts` | bearer | Create a paper account with virtual opening balance |
| GET | `/accounts/{id}` | bearer | Account detail with derived balances |
| GET | `/accounts/{id}/ledger` | bearer | Paginated transaction statement + trial balance |
| GET | `/accounts/{id}/ledger/accounts` | bearer | Chart of accounts |
| POST | `/accounts/{id}/deposit` | bearer | Virtual deposit (paper only) |
| POST | `/accounts/{id}/withdraw` | bearer | Virtual withdrawal (paper only, cannot exceed cash) |
| POST | `/accounts/{id}/reconcile` | bearer | Compare derived cash with the cached balance |

## Administration (role: admin)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/admin/users` | List users |
| POST | `/admin/users` | Create a user with an explicit role |
| PATCH | `/admin/users/{id}` | Change role / activation (self-demotion blocked) |
| GET | `/admin/audit-logs` | Append-only audit trail with action filter |
| GET | `/admin/integrations` | Provider status records |
| GET | `/admin/system-status` | Counters, uptime, guardrails |

## Examples

```bash
# Register
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"trader@example.com","full_name":"A Trader","password":"Str0ngPass!"}'

# Login
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"trader@example.com","password":"Str0ngPass!"}' | jq -r .access_token)

# List accounts
curl http://localhost:8000/api/v1/accounts -H "Authorization: Bearer $TOKEN"

# Virtual deposit
curl -X POST http://localhost:8000/api/v1/accounts/<id>/deposit \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"amount": 5000, "description": "Research budget"}'

# Ledger statement
curl http://localhost:8000/api/v1/accounts/<id>/ledger -H "Authorization: Bearer $TOKEN"
```

## Market data

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/markets/instruments` | bearer | Registry of venues, instruments and specs |
| GET | `/markets/{symbol}/candles` | bearer | OHLCV series for a timeframe |
| GET | `/markets/{symbol}/indicators` | bearer | SMA/EMA/RSI/MACD/ATR/Bollinger snapshot |
| GET | `/markets/{symbol}/quality` | bearer | Gaps, duplicates, outliers and staleness |
| POST | `/markets/sync` | staff | Pull candles from the provider into the database |
| POST | `/markets/reseed` | staff | Re-seed the instrument registry |

## Research lab

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/research/documents` | bearer | Ingested documents with trust tier |
| POST | `/research/documents` | bearer | Ingest text, URL or PDF |
| GET | `/research/claims` | bearer | Extracted claims and their verdicts |
| POST | `/research/verify` | bearer | Verify a claim against the corpus |
| GET | `/research/reports` | bearer | Generated evidence reports |
| POST | `/research/reports` | bearer | Compose a report from verified claims |

(12 routes in total; see `/openapi.json` for the full list.)

## AI orchestration

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/ai/agents` | bearer | Specialist registry, with `implemented` flags |
| GET | `/ai/overview` | bearer | Run counters, refusals and tradeable proposals |
| GET | `/ai/runs` | bearer | Recent runs, optionally filtered by symbol |
| POST | `/ai/run` | bearer | Run the specialists for one instrument |
| GET | `/ai/runs/{id}` | bearer | Full run with findings and evidence |

Every run returns `tradeable: false` with a `blocked_reason`.

## Model registry (Phase 6)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/models` | bearer | Registered model families |
| GET | `/models/overview` | bearer | Registry counters and champion detail |
| GET | `/models/versions` | bearer | Version history, optionally by model or symbol |
| GET | `/models/versions/{id}` | bearer | Version detail, evaluations, feature snapshots, gate verdict |
| GET | `/models/versions/{id}/summary` | bearer | One-line verdict and walk-forward folds |
| POST | `/models/train` | bearer | Train a **candidate** (never promotes) |
| POST | `/models/versions/{id}/promote` | **staff** | Promote to champion (gated, audited) |
| POST | `/models/versions/{id}/rollback` | **staff** | Restore the replaced version |
| POST | `/models/versions/{id}/reject` | **staff** | Retire a candidate permanently |
| POST | `/models/versions/{id}/drift` | bearer | Compare recent features with the training reference |
| GET | `/models/drift` | bearer | Open drift events |
| POST | `/models/drift/{id}/acknowledge` | **staff** | Mark a drift event as reviewed |

Training returns `201` with `status: candidate`. Promotion returns `422` with a reason when the
out-of-sample gate fails; `force: true` (staff) overrides it and stamps `FORCED OVERRIDE` on the
version.

## Strategies & backtesting (Phase 7)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/strategies` | bearer | Catalogue with each hypothesis stated |
| GET | `/strategies/overview` | bearer | Counts, passing backtests, best edge |
| GET | `/strategies/backtests` | bearer | Recent runs, filterable by strategy and symbol |
| GET | `/strategies/backtests/{id}` | bearer | Full result: trades, equity curve, metrics |
| POST | `/strategies/backtests` | bearer | Run a backtest with an explicit cost model |
| GET | `/strategies/{id}/latest` | bearer | Most recent completed run |
| POST | `/strategies/{id}/validate` | **staff** | Mark validated — refused unless a passing backtest exists |
| POST | `/strategies/{id}/retire` | **staff** | Retire a strategy |

A backtest returns `201` with `status: completed` (metrics, trades and equity) or
`status: refused` with a `refusal_reason` when there is not enough history. Every
completed result carries `baseline_return` (buy-and-hold, same window, same costs)
and `edge_over_baseline`. Running a backtest never sets `status: validated` and never
sets `live_eligible`.

## Paper execution (Phase 8)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| POST | `/execution/orders` | bearer | Submit a proposal; validated before it can reach the venue |
| GET | `/execution/orders` | bearer | Recent orders, filterable by status |
| GET | `/execution/orders/{id}` | bearer | One order with its fills and lifecycle events |
| POST | `/execution/orders/{id}/cancel` | bearer | Cancel a resting order |
| GET | `/execution/positions` | bearer | Open or closed positions, marked to the latest close |
| GET | `/execution/positions/{id}` | bearer | One position with unrealised percentage |
| GET | `/execution/overview` | bearer | Equity, cash, costs, rejection rate, reconciliation report |
| POST | `/execution/strategy-orders` | bearer | Submit a **validated** strategy's proposal (refused otherwise) |

Submission returns `201` whether the order is `filled` or `rejected`: a refusal is a
result, and the reason is in `reject_reason` together with the check trace in the
order's events. Order submission is rate-limited to 20/minute (10/minute for strategy
orders). Every route is scoped to the caller's own account; another user's order id
returns `404`, never its contents.

Note the path: execution lives under `/execution`, not `/trading` or `/orders`.

## Risk engine (Phase 9)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/risk/status` | bearer | Breaker state, limits, applied rules, live exposure and drawdown |
| GET | `/risk/policy` | bearer | Active risk policy |
| PUT | `/risk/policy` | **staff** | Replace the policy — creates a new version, records the reason |
| GET | `/risk/checks` | bearer | Decision history with every rule's observed value and limit |
| GET | `/risk/events` | bearer | Risk event log |
| POST | `/risk/evaluate` | bearer | Dry run: no order created, nothing persisted |
| POST | `/risk/emergency-stop` | **staff** | Engage the shutdown |
| POST | `/risk/resume` | **staff** | Clear the halt |
| GET | `/risk/breakers` | bearer | Breaker history |
| GET | `/risk/overview` | bearer | Counts, breach frequency, `rules_not_implemented` |

A rejected order returns `201` from `/execution/orders` with `status: rejected` and the
risk reason in `reject_reason`; the corresponding `/risk/checks` row carries the full
trace. `RiskStatus.closes_positions` is always `false`: the shutdown halts new risk and
never liquidates, and the contract says so rather than leaving it to be inferred.

## Analytics (Phase 10)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/analytics/performance` | bearer | Full report: metrics, curves, benchmark, attribution, costs, trades, notes |
| GET | `/analytics/trades` | bearer | Closed round trips, FIFO matched, net of commission |
| GET | `/analytics/equity-curve` | bearer | Marked equity per day, rebuilt from fills |

Every metric is `{key, label, value, unit, definition, note}`. `value` is `null` when
the sample cannot support the metric and `note` says what is missing — a client must
render a dash, not a zero. Every report carries `forward_looking: false`, and the
`notes` array states the report's own caveats.

## Operations (Phase 11)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/operations/health` | **public** | Liveness only: `status`, `phase`, `version`, `checked_at`. No account state, no internals |
| GET | `/operations/monitoring` | bearer | Eight-check health report for the caller's account |
| GET | `/operations/monitoring/system` | staff | Same report, system-wide, `scope: "system"` |
| GET | `/operations/audit` | staff | Sealed audit entries, newest first (`limit`, `offset`, `action`); each row carries `sequence`, `sealed`, `entry_hash` |
| GET | `/operations/audit/verify` | staff | Walk the hash chain. Reports `verified`, `first_broken_at`, `reason`, `digest`, `limitations`. **Never repairs** |
| POST | `/operations/audit/seal` | staff | Adopt unsealed rows into the chain. Idempotent; audited |
| GET | `/operations/audit/digest` | staff | Publishable head digest — the mitigation for tail re-sealing |
| GET | `/operations/backups` | staff | Enumerate on-disk backups |
| POST | `/operations/backups` | staff | Take a backup now; audited |
| POST | `/operations/backups/verify` | staff | `{path}` → checksum, integrity check, row counts, unsealed audit entries |
| POST | `/operations/backups/drill` | staff | Backup → restore into scratch → verify; `passed` is the answer |

`/operations/health` is deliberately the only unauthenticated route added in this phase:
a liveness probe must answer before anyone has a token, and it must not leak balances or
symbols. Anything that reveals account state or system internals requires a bearer token,
and the audit/backup endpoints are staff-only (403 for a standard user, tested).

A monitoring report is `{status, findings, counts, checks_run}` where each finding is
`{check, label, status, message, detail}` and `status` is the worst of the findings.
Monitoring is read-only: it reports, it does not act.

## MFA (Phase 11)

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| GET | `/auth/mfa` | bearer | What is *actually* enforced: enabled, enrolled-at, required-for-role, recovery codes remaining |
| POST | `/auth/mfa/enrol` | bearer | Start enrolment: returns the secret and an `otpauth://` URL |
| POST | `/auth/mfa/confirm` | bearer | `{code}` → enables the factor, returns eight one-time recovery codes |
| POST | `/auth/mfa/verify` | none | `{challenge_token, code}` → token pair |
| POST | `/auth/mfa/disable` | bearer | `{password, code}` → both required |

When MFA is on, `POST /auth/login` with a correct password returns
`401 {"detail": ..., "mfa_required": true, "challenge_token": "..."}` and **no tokens**.
The challenge token is only accepted by `/auth/mfa/verify`.

## Reserved namespaces (implemented in later phases)

`/signals`, `/trading`, `/orders`, `/portfolio`, `/wallet`,
`/transactions`, `/notifications`, `/scheduler`. These routes are
intentionally absent so no client can assume they exist. Note that `/positions` and
`/orders` remain reserved at the **top level** even though Phase 8 serves positions and
orders beneath `/execution`.
