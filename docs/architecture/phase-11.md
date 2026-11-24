# Phase 11 — Security & reliability

**Blueprint weeks 25–27:** *Security testing, recovery, monitoring and load testing.*
**Acceptance list:** blueprint §12, "Security and operational controls" (p.9), twelve bullets.

Phase 11 is the phase where the platform turns round and looks at itself. Earlier phases
built things a trader uses; this one builds the things that make those things safe to
run, and — just as importantly — makes the platform say what it *cannot* guarantee.

The single organising principle: **a control that cannot demonstrate failure is not a
control.** Every mechanism below is tested by breaking it.

---

## 1. What §12 asked for, and where it lives

| §12 requirement | Implementation | Proven by |
|---|---|---|
| MFA, secure sessions, RBAC | TOTP enrolment/confirm/verify, single-use recovery codes, MFA-required login challenge; sessions already rotate and revoke | `tests/test_mfa.py` (21) |
| Secrets manager, never hardcode | `app/core/secrets.py` — encrypt-then-MAC envelope for values at rest; config via environment only | `test_a_secret_round_trips`, `test_the_stored_totp_secret_is_not_readable` |
| Separate dev / staging / prod | `ENVIRONMENT` setting drives HSTS, docs exposure and the production guards from Phase 1 | `tests/test_system.py` |
| Least-privilege keys, IP restrictions | No broker is connected; the paper venue uses no credentials. Stated, not faked | — (see §7) |
| TLS in transit, encryption at rest | HSTS outside DEBUG; secret envelope for persisted secrets | `test_tampering_is_detected` |
| Rate limiting, validation, dependency scanning, secure headers | `app/core/rate_limit.py`, Pydantic validation + error envelope, pinned `requirements.txt`, header middleware | `tests/test_auth.py`, `tests/test_system.py` |
| Idempotent orders, duplicate prevention | `client_order_id` uniqueness (Phase 8) | `test_duplicate_client_order_id_returns_the_same_order` |
| **Tamper-evident audit logs**, controlled admin access | `audit_seals` hash chain + `/operations/audit/verify`; audit endpoints staff-only | `tests/test_integrity.py` (15), `tests/test_operations_api.py` (21) |
| Automated backups, tested recovery, DR | `app/services/backup.py` — backup, checksum manifest, restore-and-verify, recovery drill; `python -m app.cli` | `tests/test_backup.py` (14) |
| Monitoring: stale data, API failures, abnormal orders, model degradation | `app/services/monitoring.py` — eight checks, worst-of status | `tests/test_monitoring.py` (17) |
| Independent risk service, emergency shutdown | Phase 9 (`app/services/risk.py`), untouched here | `tests/test_risk.py` |

---

## 2. The audit chain (`app/services/integrity.py`)

```
entry_hash = sha256( prev_hash | canonical_json(sequence, action, actor, detail, created_at) )
```

* `sequence` is contiguous from 1. A gap means rows were removed.
* Seals live in a **separate table** (`audit_seals`), one row per audit entry, `ON DELETE
  CASCADE`. Deleting an audit row therefore leaves an orphaned seal — which is how a
  deletion becomes visible rather than silent.
* `record_audit` seals on write, in the caller's transaction. Phase 11 changed it from a
  fire-and-forget insert to a chained one.
* `verify_chain` **never repairs**. It reports `first_broken_at` and a reason:
  * *"Content changed at sequence N: the entry no longer hashes to its seal."*
  * *"Sequence gap"* / *"Sealed entry missing at sequence N."*
  * unsealed rows written around the application.
* `seal_pending` adopts legacy rows at startup, so an old trail is not reported as
  tampered merely for being older than the mechanism. Best-effort: a cold database must
  not stop the process from booting.

**The honest limitation, stated in the API response and on the page:** a hash chain
detects edits and deletions; it cannot prevent someone with write access from re-sealing
the entire tail. `GET /operations/audit/digest` exists so an operator can publish the
head digest somewhere the database owner does not control — after which re-sealing is
detectable by comparison.

---

## 3. MFA (`app/services/mfa.py`)

RFC 6238 TOTP, 30-second step, ±1 step of clock skew, `pyotp` (a single well-audited
dependency rather than a hand-rolled HOTP — this is the one place where writing it
ourselves would be the wrong instinct).

Flow:

1. `POST /auth/mfa/enrol` → secret + `otpauth://` URL. Secret stored **encrypted**.
2. `POST /auth/mfa/confirm` with a code → flag on, eight one-time recovery codes returned
   once and stored as sha256 digests.
3. Login with a correct password on an MFA account returns `401 {mfa_required: true,
   challenge_token}` — **not** tokens. The challenge token opens exactly one endpoint and
   is rejected as a bearer token everywhere else (tested).
4. `POST /auth/mfa/verify` → session.
5. `POST /auth/mfa/disable` requires the password **and** a live code.

`GET /auth/mfa` returns what is actually enforced, including whether the factor is
mandatory for the caller's role. Staff roles are marked required; nothing silently
pretends an unenrolled admin is protected.

**Known gap, stated plainly:** the policy is *reported*, not *enforced*. A staff account
without MFA is told so and is not blocked. Making it a hard gate would need a rollout
path (break-glass, recovery) that this phase does not build. The demo UI has no MFA
screen at all — the endpoints are the deliverable.

---

## 4. Secrets at rest (`app/core/secrets.py`)

`v1.<nonce>.<ciphertext>.<tag>`, base64url. HMAC-SHA256 as a keystream with a per-value
16-byte nonce, encrypt-then-MAC with an independent key — both keys derived from
`SECRET_KEY` with domain separation.

Why not AES-GCM: the standard library has no AES and the project's dependency policy is
deliberately thin. This construction is the same family as `secretbox` and is only ever
applied to values that must sit next to a row (TOTP secrets today). **It is not a
secrets manager**; production is expected to inject provider keys from the environment
or a mounted secret, never from the database. The docstring says so.

---

## 5. Backups and tested recovery (`app/services/backup.py`, `app/cli.py`)

* `backup_database()` — SQLite: the online backup API (consistent while the app writes);
  PostgreSQL: `pg_dump`. Writes a `manifest.jsonl` line with size and sha256.
* `verify_backup()` — re-hashes the file, runs `PRAGMA integrity_check`, counts tables and
  critical rows, and reports **unsealed audit entries**. Rot on disk is caught here.
* `restore_into()` — restores into a **scratch** location. The question "does this backup
  work?" must never require touching production.
* `recovery_drill()` — backup → restore → verify → pass/fail. `python -m app.cli drill`
  exits non-zero on failure, which is what makes it usable in cron or CI.

```
$ python -m app.cli drill
{ "passed": true, "restore": { "restored": true, "verified_after_restore": true }, ... }
```

**Not implemented, and not claimed:** point-in-time recovery. A pg_dump restores to the
moment of the dump, not an arbitrary second before an incident. The drill's `rpo_note`
says this in its own output.

---

## 6. Monitoring (`app/services/monitoring.py`)

Eight checks, each returning `{check, label, status, message, detail}`:

| Check | Critical when |
|---|---|
| `data_freshness` | any active instrument's newest bar is older than 36 h, or has none |
| `data_quality` | a `CRITICAL` quality event is open |
| `order_activity` | > 40 orders in an hour, or > 50 % of them rejected |
| `circuit_breakers` | any breaker is not `closed` |
| `model_degradation` | a `CRITICAL` `DriftEvent` is open |
| `audit_chain` | the hash chain does not verify |
| `system_events` | an error-level event in the last 24 h |
| `reconciliation` | ledger investment ≠ position value, or realised P&L disagrees |

`health_report()` returns `status` (worst-of), `findings`, `counts`, `checks_run`.

**Monitoring is read-only.** It reports; it does not act. Acting on a breach is the risk
engine's job and that engine stays separate — a monitor with the power to close positions
is a second, unaudited risk engine.

`GET /operations/health` is deliberately unauthenticated and **shallow**: status, version,
timestamp, nothing else. A liveness probe must answer before anyone has a token, and it
must not leak balances or symbols. Everything else is authenticated; the audit and backup
endpoints are staff-only.

---

## 7. What Phase 11 does not do

Stated here so it cannot be mistaken for done:

* **No real broker credentials exist**, so "least privilege" and "disable withdrawal
  permission on trading keys" have nothing to apply to yet.
* **No IP allowlisting** — the app is single-tenant and unproxied; the CORS origin regex
  and rate limits are the controls that exist.
* **No automated dependency CVE scanning in CI.** `requirements.txt` is fully pinned; the
  GitHub Actions workflow cannot be restored without a `workflow`-scoped token.
* **No load test against a production-like deployment.** The suite is a functional gate;
  latency, throughput and connection-pool behaviour under concurrency are unmeasured.
  Claiming "load tested" on the strength of a local pytest run would be exactly the kind
  of overstatement this project refuses.
* **MFA is not enforced for staff**, only reported.
* **No PITR**, as above.

---

## 8. The Operations screen

`/operations` — a demo surface, since the static build has no server:

* overall status, finding counts, chain status, head digest;
* every finding with its **message**, not just a coloured dot;
* the sealed trail, newest first, with truncated hashes;
* a *What this page refuses to claim* panel: the chain cannot stop re-sealing; two of the
  eight checks are demo stubs, and say so in their own text; monitoring does not act;
  the chain proves ordering, not truth.

`src/lib/demo/monitoringEngine.ts` carries a real SHA-256 (verified against the published
`""` and `"abc"` digests) and builds a genuine chain over the browser's action log, so the
failure modes are demonstrable: `tamperForDemo()` edits an entry and the page goes
**BROKEN** with the first broken sequence number.

---

## 9. Responsive

`/operations` is in the 320 px contract (`responsive.test.tsx`, 31 tests across 15
pages). Both tables sit in `Panel scrollBody`; the stat grid is `grid-cols-1` until `sm`;
hashes truncate rather than widen the column; timestamps only become `nowrap` at `sm`.

---

## 10. Numbers

| | |
|---|---|
| Backend tests | **480** (was 392) — +15 integrity, +17 monitoring, +21 operations API, +21 MFA, +14 backup |
| Frontend tests | **255** in 20 files (was 226/18) — +17 monitoring engine, +10 Operations page, +2 responsive |
| API paths | **107** (was 92) |
| Tables | **54** (was 52) — `audit_seals`, `mfa_recovery_codes`; `users` +2 columns |
| Migration head | `2c2b8f5f1acb` → `729b12e78f87` (chain roots) |
| New dependency | `pyotp==2.9.0` |
