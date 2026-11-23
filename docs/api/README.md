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

## Reserved namespaces (implemented in later phases)

`/markets`, `/instruments`, `/research`, `/ai`, `/models`, `/strategies`, `/signals`, `/trading`,
`/orders`, `/positions`, `/portfolio`, `/risk`, `/wallet`, `/transactions`, `/analytics`,
`/notifications`. The routes are intentionally absent in Phase 1 so no client can assume they exist.
