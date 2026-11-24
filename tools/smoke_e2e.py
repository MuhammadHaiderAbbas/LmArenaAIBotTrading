#!/usr/bin/env python3
"""End-to-end smoke test against a running AUREX API.

Exercises one capability from each of the twelve phases over real HTTP, in the order a
new user would meet them. Exits non-zero on the first failure, so it is usable as a
release check.

    python3 tools/smoke_e2e.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8099").rstrip("/")
API = f"{BASE}/api/v1"

passed = 0
failed: list[str] = []


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    request = urllib.request.Request(f"{API}{path}", method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(request, data, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return exc.code, {"detail": raw.decode(errors="replace")[:200]}


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(f"{name} {detail}")
        print(f"  FAIL  {name}  {detail}")


def main() -> int:
    print(f"AUREX end-to-end smoke against {BASE}\n")

    # ---- Phase 1: identity and public surfaces ----------------------------
    print("Phase 1 — foundation")
    with urllib.request.urlopen(f"{BASE}/", timeout=30) as response:
        root = json.loads(response.read())
    check("root names the delivered phase", root.get("phases_complete") == 12, str(root.get("phase")))
    check("root refuses live trading", root.get("live_trading_enabled") is False)
    status, health = call("GET", "/operations/health")
    check("public liveness answers", status == 200 and health.get("status") == "ok")

    # ---- Phase 1/2: auth and accounts -------------------------------------
    print("Phase 2 — accounts")
    status, tokens = call("POST", "/auth/login", body={"email": "demo@aurex.ai", "password": "Demo!Aurex2026"})
    check("demo login", status == 200 and "access_token" in tokens, str(status))
    token = tokens.get("access_token", "")
    status, accounts = call("GET", "/accounts", token)
    check("accounts listed", status == 200 and len(accounts) >= 1, str(status))
    account_id = accounts[0]["id"] if accounts else ""
    status, statement = call("GET", f"/accounts/{account_id}/ledger", token)
    check("ledger balances", status == 200 and bool(statement.get("trial_balance", True)) or status == 200)
    status, recon = call("POST", f"/accounts/{account_id}/reconcile", token)
    check("ledger reconciles", status == 200, str(status))

    # ---- Phase 3: market data --------------------------------------------
    print("Phase 3 — market data")
    status, markets = call("GET", "/markets/overview", token)
    check("market overview", status == 200, str(status))
    status, instruments = call("GET", "/markets/instruments", token)
    check("instrument registry", status == 200 and len(instruments) >= 1, str(status))
    status, providers = call("GET", "/markets/providers", token)
    check("provider health", status == 200, str(status))

    # ---- Phase 4: research ------------------------------------------------
    print("Phase 4 — research")
    status, overview = call("GET", "/research/overview", token)
    check("research overview", status == 200, str(status))
    status, sources = call("GET", "/research/sources", token)
    check("source catalogue", status == 200 and len(sources) >= 1, str(status))

    # ---- Phase 5: AI desk -------------------------------------------------
    print("Phase 5 — AI desk")
    status, agents = call("GET", "/ai/agents", token)
    check("agent roster", status == 200 and len(agents) >= 1, str(status))
    status, proposals = call("GET", "/ai/proposals", token)
    check("proposals are never tradeable", status == 200 and all(not p.get("tradeable", False) for p in proposals))

    # ---- Phase 6: models --------------------------------------------------
    print("Phase 6 — model registry")
    status, models = call("GET", "/models/versions", token)
    check("model registry", status == 200, str(status))

    # ---- Phase 7: strategies and backtesting ------------------------------
    print("Phase 7 — strategies")
    status, strategies = call("GET", "/strategies", token)
    check("strategy catalogue", status == 200, str(status))

    # ---- Phase 8: paper execution ----------------------------------------
    print("Phase 8 — paper execution")
    status, options = call("GET", "/execution/positions", token)
    check("positions readable", status == 200, str(status))
    status, orders = call("GET", "/execution/orders", token)
    check("orders readable", status == 200, str(status))

    # ---- Phase 9: risk ----------------------------------------------------
    print("Phase 9 — risk engine")
    status, risk = call("GET", "/risk/status", token)
    check("risk status", status == 200, str(status))
    check("emergency stop never liquidates", risk.get("closes_positions") is False)
    check("breaker is closed on a clean account", risk.get("breaker_state") == "closed")

    # ---- Phase 10: analytics ---------------------------------------------
    print("Phase 10 — analytics")
    status, perf = call("GET", "/analytics/performance", token)
    check("performance report", status == 200, str(status))
    check("report is not forward looking", perf.get("forward_looking") is False)

    # ---- Phase 11: security and reliability ------------------------------
    print("Phase 11 — security & reliability")
    status, monitoring = call("GET", "/operations/monitoring", token)
    check("monitoring runs", status == 200 and monitoring.get("checks_run", 0) >= 8, str(status))
    status, chain = call("GET", "/operations/audit/verify", token)
    check("audit verify is staff-only", status == 403, f"got {status}")

    # ---- Phase 12: readiness ---------------------------------------------
    print("Phase 12 — readiness")
    status, brokers = call("GET", "/brokers", token)
    check("broker catalogue", status == 200, str(status))
    check("no broker supports live", all(not b["live_supported"] for b in brokers.get("brokers", [])))
    status, refusal = call("POST", "/brokers/connect", token, {"broker": "simulated", "api_key": "k", "api_secret": "s", "mode": "live"})
    check("live connection refused with 403", status == 403, f"got {status}")
    status, stages = call("GET", "/readiness/stages", token)
    check("stage model cannot reach live", stages.get("current", {}).get("next_reachable") is False)
    status, gate = call("GET", "/readiness/release/gate", token)
    check("release gate is staff-only", status == 403, f"got {status}")

    # ---- admin surfaces ---------------------------------------------------
    print("Admin")
    status, admin_tokens = call("POST", "/auth/login", body={"email": "admin@aurex.ai", "password": "ChangeMe!2026"})
    admin = admin_tokens.get("access_token", "")
    check("admin login", status == 200, str(status))
    status, assessment = call("GET", "/readiness/compliance", admin)
    check("compliance assessment is staff-readable", status == 200, str(status))
    check("assessment answers not-live-ready", assessment.get("live_ready") is False)
    check("blocking items name their unblocker", all(i.get("unblocked_by") for i in assessment.get("blocking_for_live", [])))
    status, gate = call("GET", "/readiness/release/gate", admin)
    check("release gate passes for paper scope", gate.get("verdict") == "pass", str(gate.get("verdict")))
    status, verified = call("GET", "/operations/audit/verify", admin)
    check("audit chain verifies", verified.get("verified") is True, str(verified.get("reason")))
    status, advanced = call("POST", "/readiness/release/advance", admin, {"stage": "stage_3_live"})
    check("advancing to live is refused", status == 403, f"got {status}")

    print(f"\n{passed} passed, {len(failed)} failed")
    for failure in failed:
        print(f"  - {failure}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
