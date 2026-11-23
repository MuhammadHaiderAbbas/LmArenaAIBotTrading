# Phase 1 — Foundation

**Status:** complete · **Scope owner:** platform engineering · **Date:** 23 November 2026

This document records exactly what Phase 1 implements, what it deliberately does not, and the
decisions a reviewer should check before Phase 2 work is merged.

## 1. Objective

Create a reliable foundation — repository, database, authentication, API structure, financial
primitives and the product shell — before any AI or market-data complexity is added
(project principle 29: *build a reliable system before adding complex AI*).

## 2. Delivered scope

### 2.1 Repository & infrastructure
- Monorepo layout matching the blueprint: `frontend/`, `backend/`, `ml/`, `infrastructure/`, `docs/`, `scripts/`
- `docker-compose.yml` with PostgreSQL 16, Redis 7, API and web service, health-gated dependencies
- Multi-stage backend Dockerfile, non-root user, `HEALTHCHECK`
- GitHub Actions: backend lint + tests, frontend typecheck + build, secret guard, Pages deployment
- `.env.example` with every setting documented; `.gitignore` blocks secrets and local databases

### 2.2 Database
13 tables across five domains, generated through Alembic (`phase 1 foundation schema`):

| Domain | Tables |
| --- | --- |
| Identity | `users`, `user_sessions`, `user_settings` |
| Accounts | `trading_accounts`, `account_balances` |
| Ledger | `ledger_accounts`, `ledger_transactions`, `ledger_entries`, `reconciliation_records` |
| Operations | `audit_logs`, `system_events`, `notifications`, `integration_health` |

Every table has explicit indexes on lookup keys and a unique constraint where a duplicate would
corrupt meaning (e.g. `uq_balance_account_currency`, `uq_ledger_account_kind`).

### 2.3 Financial core — double-entry ledger
`backend/app/services/ledger_service.py` is the most safety-critical module in this phase.

- Chart of accounts provisioned automatically per trading account (6 kinds)
- Transactions rejected unless lines sum to exactly zero and there are at least two lines
- Idempotency keys prevent duplicate posting (unique index at the database level)
- Cached balances are always re-derived from entries, never authored
- `reconcile()` compares derived cash against the cache and records the result
- Amounts quantised to 8 decimals using `Decimal`, not floats

### 2.4 Identity & access
- Registration with a real password policy (10 chars, upper, lower, digit) enforced server-side
- JWT access tokens (8h) carrying subject/role; refresh tokens (14d) stored **hashed** and rotated on use
- Refresh rotation revokes the presented token; reuse after rotation returns 401
- Password change revokes all other sessions
- Rate limiting on `register`, `login`, `refresh`
- RBAC: `admin` can administer users, read the audit trail, integrations and system status
- Resource isolation: another user's account returns **404**, not 403 (no existence leak)

### 2.5 Audit trail
Append-only `audit_logs` records: `user.register`, `auth.login` (success/failure/denied),
`auth.logout`, `user.update_profile`, `user.change_password`, `account.create`,
`wallet.virtual_deposit`, `wallet.virtual_withdrawal`, `admin.user_create`, `admin.user_update`.
Records capture actor, entity, outcome, IP, user agent and detail.

### 2.6 Frontend terminal
Thirteen screens: Overview, Markets, AI Trading, AI Research Lab, Strategies, Portfolio, Risk Centre,
Wallet, Trade History, Analytics, Settings, Administration, plus Login/Register.

- Institutional dark palette, tabular-numeric figures, accessible contrast and focus rings
- Paper environment is labelled in the header, the shell banner and every balance ("not withdrawable")
- Phase-gated screens explain exactly what is planned rather than faking data
- No performance figure is displayed anywhere that could be mistaken for a result

## 3. Out of scope (and where it lands)

| Capability | Phase |
| --- | --- |
| Market data adapters, candles, streaming | 03 |
| News ingestion, document RAG, research reports | 04 |
| Agent orchestration & structured findings | 05 |
| Models, calibration, drift detection | 06 |
| Backtesting with realistic costs | 07 |
| Paper order lifecycle, fills, positions | 08 |
| Independent risk service, circuit breakers | 09 |
| Performance analytics & decision replay | 10 |
| MFA, email verification, hardened ops | 11 |
| Broker sandbox, payments, regulatory perimeter | 12 |

## 4. Invariants a reviewer must verify

1. `LIVE_TRADING_ENABLED` and `REAL_MONEY_ENABLED` are `false` and no code path overrides them.
2. `AccountCreate.environment` is `Literal["paper"]` — a client cannot request a live account.
3. No endpoint accepts a trading instruction; there is no order router.
4. Every ledger transaction sums to zero (`test_every_transaction_sums_to_zero`).
5. Cached balances are only written by `refresh_balance_cache()`.
6. Cross-tenant reads return 404.
7. Admin-only routes are guarded by a dependency, not by client-side checks alone.
8. No secret is committed; `.env.example` holds placeholders only.

## 5. Test coverage

35 backend tests:

- `test_system.py` — health, metadata, security headers, OpenAPI
- `test_auth.py` — registration, duplicate email, password policy, login, wrong password, refresh
  rotation & reuse, protected routes, RBAC blocks, audit content, failed-login audit, rate limit,
  password change session invalidation
- `test_accounts.py` — auto-provisioned funded account, cross-user isolation, second account, live
  environment rejection, deposit/withdraw balance effects, over-withdrawal, ledger statement balance,
  reconciliation, audit entries
- `test_ledger.py` — expected entries, unbalanced rejection, single-line rejection, idempotency,
  no negative cash, trial balance, cache mismatch detection, password hashing round-trip

Frontend: `npm run typecheck` (strict, no unused locals) and a production build in CI.

## 6. Known limitations

- Rate limiting is in-process; multi-instance deployments need Redis (already provisioned).
- Email verification and MFA are modelled in the schema but not enforced.
- SQLite is supported for development and tests; production must use PostgreSQL.
- The static demo build reimplements the API client-side for GitHub Pages; it is a review aid, not
  a substitute for the FastAPI service.

## 7. Next phase gate

Phase 3 (market data) may begin when: CI is green on `main`, the Alembic migration applies cleanly to
an empty PostgreSQL database, and a reviewer has signed off the eight invariants above.
