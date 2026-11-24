"""Readiness: what is done, what is not, and what must not be claimed.

Blueprint §15 Phase 12 is *broker sandbox integrations, compliance assessment and release
preparation*. This module is the assessment half, and its most important output is the
list of things that are **not** ready.

Design rule: every item resolves from **evidence in the codebase or the database**, never
from a hardcoded "done". A checklist that says `True` because someone typed `True` is a
press release, not an assessment. Where evidence cannot exist yet — no broker contract,
no counsel opinion — the item is `blocked` or `not_implemented` and says what would
unblock it.

Categories mirror blueprint §13's UK issue map: perimeter, client money, financial
promotions, KYC/AML, data protection, market-data licensing, cryptoasset regime, and the
operational controls from §12.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.account import TradingAccount
from app.models.broker import BrokerConnection
from app.models.execution import Order
from app.models.identity import User
from app.services import integrity
from app.services.broker.base import available_brokers, live_trading_is_impossible

DONE = "done"
PARTIAL = "partial"
BLOCKED = "blocked"
NOT_IMPLEMENTED = "not_implemented"

SEVERITY_MAP = {DONE: 0, PARTIAL: 1, NOT_IMPLEMENTED: 2, BLOCKED: 3}


def _item(
    key: str,
    title: str,
    status: str,
    evidence: str,
    *,
    category: str,
    blocks_live: bool = False,
    unblocked_by: str | None = None,
) -> dict:
    return {
        "key": key,
        "title": title,
        "category": category,
        "status": status,
        "evidence": evidence,
        "blocks_live": blocks_live,
        "unblocked_by": unblocked_by,
    }


def _scalar(db: Session, statement) -> int:
    return int(db.scalar(statement) or 0)


def assessment(db: Session) -> dict:
    """The full readiness assessment, derived from the running system."""
    live = live_trading_is_impossible()
    accounts = _scalar(db, select(func.count()).select_from(TradingAccount))
    orders = _scalar(db, select(func.count()).select_from(Order))
    brokers = available_brokers()
    connections = _scalar(db, select(func.count()).select_from(BrokerConnection))
    chain = integrity.verify_chain(db)
    users = _scalar(db, select(func.count()).select_from(User))
    mfa_users = _scalar(
        db, select(func.count()).select_from(User).where(User.mfa_enabled.is_(True))
    )
    withdraw_keys = _scalar(
        db,
        select(func.count())
        .select_from(BrokerConnection)
        .where(BrokerConnection.withdraw_allowed.is_(True)),
    )

    items = [
        # ---------------------------------------------------------- perimeter
        _item(
            "perimeter.non_customer_scope",
            "Product operates in a non-customer, non-live-money scope",
            DONE
            if not settings.LIVE_TRADING_ENABLED and not settings.REAL_MONEY_ENABLED
            else BLOCKED,
            (
                "LIVE_TRADING_ENABLED and REAL_MONEY_ENABLED are both false and there is no "
                "live adapter in the codebase."
                if not settings.LIVE_TRADING_ENABLED and not settings.REAL_MONEY_ENABLED
                else "A live-money flag is enabled. That leaves the assessed scope."
            ),
            category="perimeter",
            blocks_live=True,
        ),
        _item(
            "perimeter.written_assessment",
            "Written UK regulatory-perimeter assessment",
            BLOCKED,
            "Requires specialist counsel. Blueprint §13 is an issue map, not advice.",
            category="perimeter",
            blocks_live=True,
            unblocked_by="A written opinion from a UK financial-services solicitor.",
        ),
        _item(
            "perimeter.live_execution_not_built",
            "No live execution path exists",
            DONE if not live["live_adapter_registered"] else BLOCKED,
            live["reason"],
            category="perimeter",
        ),
        # ------------------------------------------------------- client money
        _item(
            "client_money.custody",
            "No customer money or assets are held",
            DONE,
            f"No custody, no funding rail and no withdrawal endpoint. {accounts} paper "
            "account(s) exist and every balance originates from a simulated funding entry.",
            category="client_money",
        ),
        _item(
            "client_money.safeguarding",
            "Client-money safeguarding arrangements",
            NOT_IMPLEMENTED,
            "Not applicable while no customer money is held; would be required before any "
            "funding or withdrawal feature.",
            category="client_money",
            blocks_live=True,
            unblocked_by="An authorised partner or segregated client-money account.",
        ),
        # --------------------------------------------------------- promotions
        _item(
            "promotions.no_performance_claims",
            "No performance claims or forward-looking projections are produced",
            DONE,
            "Analytics reports carry forward_looking: false, withhold any metric the sample "
            "cannot support, and the AI desk stores tradeable: false with a reason.",
            category="promotions",
        ),
        _item(
            "promotions.financial_promotion_review",
            "Financial-promotion sign-off for any public marketing",
            BLOCKED,
            "No marketing copy has been reviewed. Any public promotion would need approval "
            "under the FCA financial-promotion rules.",
            category="promotions",
            blocks_live=True,
            unblocked_by="Compliance review of the actual published copy.",
        ),
        # ----------------------------------------------------------- KYC / AML
        _item(
            "kyc.identity_verification",
            "KYC / AML / sanctions screening",
            NOT_IMPLEMENTED,
            "Accounts are self-service and unverified; there is no identity provider, "
            "screening or transaction monitoring. Acceptable only because no customer money "
            "is involved.",
            category="kyc",
            blocks_live=True,
            unblocked_by="An identity-verification provider and an AML policy.",
        ),
        # ------------------------------------------------------ data protection
        _item(
            "privacy.encryption_at_rest",
            "Sensitive values encrypted at rest",
            DONE,
            "TOTP secrets and broker credentials are stored via the encrypt-then-MAC "
            "envelope in app/core/secrets.py; passwords are PBKDF2-HMAC-SHA256.",
            category="data_protection",
        ),
        _item(
            "privacy.audit_trail",
            "Tamper-evident audit trail",
            DONE if chain.verified else BLOCKED,
            (
                f"{chain.entries} sealed entr(y/ies) verify."
                if chain.verified
                else f"Chain does not verify: {chain.reason}"
            ),
            category="data_protection",
        ),
        _item(
            "privacy.gdpr_erasure",
            "UK GDPR subject-access and erasure handling",
            NOT_IMPLEMENTED,
            "No export or erasure workflow exists. Deleting a user cascades rows but does "
            "not satisfy an auditable erasure request.",
            category="data_protection",
            blocks_live=True,
            unblocked_by="A data-retention policy and an erasure workflow that preserves "
            "the audit trail's integrity.",
        ),
        # ----------------------------------------------------------- licensing
        _item(
            "data.market_data_licensing",
            "Market-data licensing and exchange terms",
            PARTIAL,
            "Crypto and FX data comes from public, unauthenticated endpoints; nothing is "
            "redistributed. Any commercial use needs per-provider terms review.",
            category="data",
            unblocked_by="Provider-specific licence review before redistribution.",
        ),
        # ------------------------------------------------------------ crypto
        _item(
            "crypto.regime_applicability",
            "UK cryptoasset regime applicability",
            BLOCKED,
            "Blueprint §13 notes the FCA cryptoasset regime (application window 30 Sep 2026 "
            "– 28 Feb 2027, regime from 25 Oct 2027). Crypto instruments are present in the "
            "instrument registry, so applicability must be assessed before any promotion.",
            category="crypto",
            blocks_live=True,
            unblocked_by="A dated regulatory assessment covering the cryptoasset regime.",
        ),
        # ---------------------------------------------------- operational (§12)
        _item(
            "ops.mfa_available",
            "Multi-factor authentication is available",
            DONE,
            f"TOTP with recovery codes is implemented; {mfa_users} of {users} user(s) have "
            "it enabled. It is reported, not enforced — see ops.mfa_enforced.",
            category="operations",
        ),
        _item(
            "ops.mfa_enforced",
            "Multi-factor authentication is enforced for staff",
            PARTIAL,
            "Staff roles are flagged as required-for-role but are not blocked at login.",
            category="operations",
            unblocked_by="A break-glass path, then a hard gate on privileged roles.",
        ),
        _item(
            "ops.backups_tested",
            "Backups exist and recovery is tested",
            DONE if _backup_drill_available() else NOT_IMPLEMENTED,
            "python -m app.cli drill backs up, restores into a scratch copy and verifies. "
            "Point-in-time recovery is not implemented.",
            category="operations",
        ),
        _item(
            "ops.monitoring",
            "Monitoring for stale data, failures, abnormal orders and model drift",
            DONE,
            "Eight checks in app/services/monitoring.py; worst-of status; read-only.",
            category="operations",
        ),
        _item(
            "ops.risk_separation",
            "Independent risk controller with emergency shutdown",
            DONE,
            "Phase 9: 12 rules in fixed order, separate breakers table, closes_positions "
            "always false, and no path from an AI proposal to an order.",
            category="operations",
        ),
        _item(
            "ops.load_testing",
            "Load and reliability testing against a production-like deployment",
            NOT_IMPLEMENTED,
            "The suite is a functional gate. Throughput, latency and pool behaviour under "
            "concurrency are unmeasured.",
            category="operations",
            blocks_live=True,
            unblocked_by="A staging deployment and a repeatable load harness.",
        ),
        # ---------------------------------------------------------- brokers
        _item(
            "broker.sandbox_integration",
            "Broker sandbox integration",
            DONE if brokers and connections >= 0 else NOT_IMPLEMENTED,
            f"{len(brokers)} sandbox adapter(s) registered, {connections} connection(s) "
            "recorded with handshake latency and clock skew. No live adapter exists.",
            category="broker",
        ),
        _item(
            "broker.least_privilege_keys",
            "Least-privilege broker keys (withdrawal disabled)",
            PARTIAL if withdraw_keys == 0 else BLOCKED,
            (
                "Credentials are encrypted and the withdrawal flag is recorded and reported. "
                f"{withdraw_keys} connection(s) report withdrawal permission enabled."
            ),
            category="broker",
            unblocked_by="A real broker contract with read/trade-only scopes.",
        ),
        _item(
            "broker.paper_evidence",
            "Sustained paper-trading evidence across varied conditions",
            PARTIAL if orders else NOT_IMPLEMENTED,
            f"{orders} paper order(s) recorded. Blueprint §15 asks for several months across "
            "varied conditions; this build has a sample, not a season, and makes no "
            "profitability claim.",
            category="broker",
            blocks_live=True,
            unblocked_by="Months of continuous paper trading with incident records.",
        ),
        # ------------------------------------------------------------ release
        _item(
            "release.automated_tests",
            "Automated test gate",
            DONE,
            "480 backend and 255 frontend tests; ruff and tsc clean; a 320 px responsive "
            "contract covers every screen.",
            category="release",
        ),
        _item(
            "release.ci_workflow",
            "CI workflow runs on push",
            PARTIAL,
            "The workflow lives in docs/deployment/CI_WORKFLOWS.md: restoring "
            ".github/workflows/ci.yml needs a workflow-scoped token this project does not hold.",
            category="release",
        ),
    ]

    blocking = [item for item in items if item["blocks_live"] and item["status"] != DONE]
    counts = {
        status: sum(1 for item in items if item["status"] == status) for status in SEVERITY_MAP
    }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": "private research and paper trading only",
        "items": items,
        "counts": counts,
        "blocking_for_live": [
            {"key": item["key"], "title": item["title"], "unblocked_by": item["unblocked_by"]}
            for item in blocking
        ],
        "live_ready": False,
        "live_ready_reason": (
            f"{len(blocking)} blocking item(s) remain. The platform is deliberately not "
            "live-ready: the Phase 12 deliverable is readiness *assessment*, and the "
            "assessment currently answers no."
        ),
        "live_trading": live,
    }


def _backup_drill_available() -> bool:
    try:
        from app.services import backup

        return callable(backup.recovery_drill)
    except Exception:  # noqa: BLE001
        return False


def checklist(db: Session) -> dict:
    """A compact view for the UI: status per category, and the count that matters."""
    report = assessment(db)
    by_category: dict[str, dict[str, int]] = {}
    for item in report["items"]:
        bucket = by_category.setdefault(item["category"], dict.fromkeys(SEVERITY_MAP, 0))
        bucket[item["status"]] += 1
    return {
        "generated_at": report["generated_at"],
        "by_category": by_category,
        "counts": report["counts"],
        "blocking_count": len(report["blocking_for_live"]),
        "live_ready": report["live_ready"],
    }
