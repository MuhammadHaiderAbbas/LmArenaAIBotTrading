# Security notes

## Controls implemented in Phase 1

| Control | Implementation |
| --- | --- |
| Password storage | PBKDF2-HMAC-SHA256, 260,000 iterations, 16-byte random salt, constant-time compare |
| Password policy | ≥10 chars, upper + lower + digit, validated server-side and mirrored in the UI |
| Access tokens | JWT HS256, 8-hour lifetime, `sub`/`role`/`type`/`jti`/issuer claims, `exp` + `nbf` required |
| Refresh tokens | 14-day lifetime, **SHA-256 hash stored** in `user_sessions`, rotated on every use |
| Session revocation | Logout, refresh rotation and password change all revoke sessions |
| Rate limiting | Fixed window per IP: register 5/min, login 10/min, refresh 30/min → HTTP 429 + `Retry-After` |
| RBAC | FastAPI dependency guards (`require_roles`), never client-side only |
| Tenant isolation | Non-owned resources return 404 to avoid existence disclosure |
| Input validation | Pydantic v2 models on every write endpoint; structured 422 payloads |
| SQL injection | SQLAlchemy Core/ORM parameterisation only; no string-built SQL |
| Audit | Append-only `audit_logs` capturing actor, action, entity, outcome, IP and user agent |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, HSTS when not debug |
| CORS | Explicit allow-list from configuration, restricted regex for preview hosts |
| Secrets | Environment-only; `.env` ignored; CI secret guard blocks keys and local databases |
| Containers | Backend runs as UID 10001, no build toolchain in the runtime layer, healthcheck on `/system/health` |

## Deliberate exclusions

- **No broker credentials exist in this repository or deployment.** Live trading is compiled out:
  `LIVE_TRADING_ENABLED=false` and no order-submission code path exists.
- **No payment rail.** There is no gateway integration and no card data handling.
- **No customer funds.** Every balance is virtual and labelled as such throughout the UI.

## Outstanding items (Phase 11)

1. TOTP/passkey MFA enrolment and enforcement for admin roles.
2. Email verification and password-reset flow (schema fields exist: `is_verified`, `mfa_enabled`).
3. Redis-backed rate limiting and login lockout for multi-instance deployments.
4. Dependency and container image scanning in CI; SBOM generation.
5. Tamper-evidence for the audit trail (hash chaining or write-once storage).
6. Secrets manager integration; automated key rotation.
7. Backup, restore and disaster-recovery rehearsal.
8. Penetration test and abuse-case review before any public exposure.

## Reporting

Report a suspected vulnerability privately to the repository owner rather than opening a public
issue. Do not test against deployments you do not own.

## Threat model summary

| Threat | Mitigation today |
| --- | --- |
| Credential stuffing | Rate limiting, generic error messages, audit of failures |
| Token theft | Short access lifetime, rotating refresh tokens, hashed at rest, revocation |
| Privilege escalation | Server-side role guards; self-demotion blocked; audited role changes |
| Cross-tenant access | Ownership checks on every account-scoped query |
| Ledger tampering | Append-only journal, balanced-transaction invariant, reconciliation records |
| Duplicate financial postings | Unique idempotency keys; all money writes flush then re-derive balance |
| Silent AI overreach | No AI execution path exists; risk controller is a separate, deterministic layer (Phase 9) |
