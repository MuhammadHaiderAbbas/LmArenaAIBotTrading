# Phase 2 — Data model & domain design

**Status:** complete · **Scope owner:** platform engineering · **Date:** 23 November 2026

Phase 2 turned the Phase 1 foundation into a coherent domain model. Every later phase assumes
these tables, enums and invariants, so this document records the shape of the schema and the rules
that must not drift.

> **Why this document is written after Phases 3–5.** Phase 2 shipped as code and migrations but its
> architecture note was never written, leaving a gap in `docs/architecture/`. It is backfilled here
> from the migrations and models, not reconstructed from memory.

## 1. Objective

Design the data model so that money, market data, research and AI output each live in their own
domain with explicit boundaries, and so that no domain can quietly write into another's records.

## 2. Delivered scope

### 2.1 Domain separation

| Domain | Responsibility | Must never do |
| --- | --- | --- |
| Identity | Users, sessions, roles, settings | Hold balances |
| Accounts | Trading accounts and cached balances | Post entries without the ledger |
| Ledger | Double-entry accounts, transactions, entries, reconciliation | Compute balances from anywhere else |
| Market | Venues, instruments, candles, quality events | Invent prices |
| Research | Documents, claims, verdicts, reports | State a market view |
| AI | Agents, runs, findings, evidence | Execute or size anything |

The dependency direction is one-way: AI may read market and research data, market never reads AI,
and the ledger knows nothing about either.

### 2.2 Financial primitives

- **Money is `Decimal`**, never `float`, in the ledger and account domains. Floats appear only in
  indicators and model features, where the values are statistical rather than monetary.
- **Double-entry with a trial balance.** Every transaction posts balanced entries; a reconciliation
  record compares the derived cash position with the cached balance.
- **Paper only.** `LIVE_TRADING_ENABLED` and `REAL_MONEY_ENABLED` default to false and the health
  endpoint reports both so a reviewer can see the guardrails without reading the code.

### 2.3 Enumerated vocabularies

Postgres enums were avoided in favour of `Enum(..., native_enum=False)`, which stores a `VARCHAR`
with a check constraint. The reason is operational: adding a value to a native Postgres enum needs a
migration that locks the type, and every phase so far has added values.

### 2.4 Invariants introduced in this phase

1. `Venue.asset_class` and `Instrument.venue_id` are **NOT NULL** — an instrument without a venue
   cannot be reconciled against a provider.
2. `MarketCandle` requires both `close_time` and `ingested_at`, so the difference between "when the
   bar happened" and "when we learned about it" is always available.
3. Timestamps are timezone-aware in Postgres. SQLite returns naive datetimes, so every comparison in
   the application goes through `aware()`/`age_hours()` helpers rather than trusting the driver.
4. Primary keys are UUID strings generated in Python, not by the database, so a record's identity is
   known before the transaction commits.

## 3. Deliberately not in Phase 2

- No trading, order or position tables. Those arrive with the execution engine (Phase 8); the
  `Signals`/`Orders` namespaces stay reserved and absent so no client can assume them.
- No time-series partition strategy. Candles are a plain table until volume justifies more.
- No vector storage for research retrieval; lexical retrieval shipped in Phase 4 instead.

## 4. Review checklist

- [x] Migrations run forward and back cleanly (`23e2ffb` → `a623f3e` are the Phase 2 revisions).
- [x] Ledger balances are derived from entries, and reconciliation can detect a mismatch.
- [x] No monetary quantity is stored as a float.
- [x] Every later phase adds tables through Alembic only; no `create_all` outside tests.
