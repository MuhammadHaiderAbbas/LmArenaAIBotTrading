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

## Reserved namespaces (implemented in later phases)

`/strategies`, `/signals`, `/trading`, `/orders`, `/positions`, `/portfolio`, `/risk`, `/wallet`,
`/transactions`, `/analytics`, `/notifications`. These routes are intentionally absent so no client
can assume they exist.
