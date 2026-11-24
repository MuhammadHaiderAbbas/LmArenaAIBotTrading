"""Stage transitions and the release gate.

Blueprint §13 describes three business stages: private R&D, research SaaS, live trading
services. This module models the first two as a **state machine with evidentiary
preconditions**, so "we moved to Stage 2" is a claim the system can refuse.

The third stage is not modelled as reachable. `advance_to("stage_3_live")` raises, names
the blocking items, and says so in the audit trail — because a stage machine that will
happily move to live trading once a boolean is set is exactly the control that fails
quietly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import record_audit
from app.models.broker import BrokerConnection
from app.models.identity import User
from app.services import compliance, integrity, monitoring
from app.services.broker.base import available_brokers
from app.services.errors import DomainError

STAGES = ("stage_1_private", "stage_2_research_saas", "stage_3_live")

REQUIREMENTS = {
    "stage_2_research_saas": (
        "The platform can accept no customer money and place no customer trade, and the "
        "audit chain verifies."
    ),
    "stage_3_live": "Not reachable in this build — see compliance.assessment().",
}


def _stage_2_evidence(db: Session) -> list[dict]:
    chain = integrity.verify_chain(db)
    connections = list(db.scalars(select(BrokerConnection)))
    withdrawal_keys = [c.broker for c in connections if c.withdraw_allowed]
    checks = [
        {
            "check": "no_live_adapter",
            "passed": not any(b["live_supported"] for b in available_brokers()),
            "detail": "No registered adapter can reach a real market.",
        },
        {
            "check": "audit_chain_verifies",
            "passed": chain.verified,
            "detail": f"{chain.entries} sealed entries; {chain.reason or 'no break'}.",
        },
        {
            "check": "no_withdrawal_keys",
            "passed": not withdrawal_keys,
            "detail": (
                "No stored broker key carries withdrawal permission."
                if not withdrawal_keys
                else f"Withdrawal permission enabled on: {sorted(set(withdrawal_keys))}."
            ),
        },
        {
            "check": "no_open_critical_breach",
            "passed": not any(
                finding["status"] == "critical"
                for finding in monitoring.health_report(db, account=None)["findings"]
            ),
            "detail": "No critical monitoring finding.",
        },
    ]
    return checks


def current_stage() -> dict:
    return {
        "stage": "stage_2_research_saas",
        "label": "Research SaaS (paper trading only, no customer money)",
        "since": None,
        "next": "stage_3_live",
        "next_reachable": False,
        "notes": (
            "Stage 2 capability is present: accounts, research, analytics and paper "
            "trading. Live trading services are not built and the stage machine refuses "
            "to advance there."
        ),
    }


def stage_report(db: Session) -> dict:
    return {
        "current": current_stage(),
        "stages": [
            {
                "key": "stage_1_private",
                "label": "Private R&D",
                "status": "passed",
                "requirement": "Own environment, virtual capital, paper trading.",
            },
            {
                "key": "stage_2_research_saas",
                "label": "Research SaaS",
                "status": "current",
                "requirement": REQUIREMENTS["stage_2_research_saas"],
                "evidence": _stage_2_evidence(db),
            },
            {
                "key": "stage_3_live",
                "label": "Live trading services",
                "status": "not_implemented",
                "requirement": REQUIREMENTS["stage_3_live"],
            },
        ],
        "checked_at": datetime.now(UTC).isoformat(),
    }


def evaluate(db: Session) -> dict:
    """Run the gate. Returns pass/fail per check plus the overall verdict."""
    checks = _stage_2_evidence(db)
    report = compliance.assessment(db)
    gate_checks = [
        *checks,
        {
            "check": "no_blocking_compliance_items",
            # Informational for this release: these items block a *live* release, and are
            # listed here so the gate cannot be read as "nothing left to do".
            "blocking": False,
            "passed": not report["blocking_for_live"],
            "detail": (
                f"{len(report['blocking_for_live'])} item(s) block a live release; none "
                "block the current paper-trading scope."
            ),
        },
    ]
    passed = all(check["passed"] for check in gate_checks if check.get("blocking", True))
    return {
        "verdict": "pass" if passed else "fail",
        "scope": "current paper-trading release",
        "checks": gate_checks,
        "compliance_blocking_count": len(report["blocking_for_live"]),
        "blocking_checks": [c["check"] for c in gate_checks if c.get("blocking", True)],
        "evaluated_at": datetime.now(UTC).isoformat(),
        "statement": (
            "This gate decides whether the *current* paper-trading release may ship. It "
            "does not certify fitness for customer money or live execution, and passing it "
            "is not a regulatory opinion."
        ),
    }


def advance_to(db: Session, *, user: User, target: str) -> dict:
    """Attempt a stage transition. Stage 3 is unreachable, deliberately."""
    if target not in STAGES:
        raise DomainError(f"Unknown stage {target!r}.", status_code=422)

    if target == "stage_3_live":
        blocking = compliance.assessment(db)["blocking_for_live"]
        record_audit(
            db,
            action="release.advance",
            actor_id=user.id,
            actor_email=user.email,
            outcome="denied",
            detail=(
                f"Refused advance to {target}: {len(blocking)} blocking item(s). Live "
                "trading is not implemented in this build."
            ),
        )
        db.commit()
        raise DomainError(
            "Advancing to live trading services is refused. This build contains no live "
            "execution path, and the compliance assessment lists "
            f"{len(blocking)} blocking item(s) that require counsel, an authorised broker "
            "and operational evidence.",
            status_code=403,
            extra={"blocking": [item["key"] for item in blocking], "stage": target},
        )

    result = evaluate(db)
    if result["verdict"] != "pass":
        raise DomainError(
            "The release gate did not pass; the stage cannot advance.",
            status_code=409,
            extra={"failed": [c["check"] for c in result["checks"] if not c["passed"]]},
        )
    record_audit(
        db,
        action="release.advance",
        actor_id=user.id,
        actor_email=user.email,
        detail=f"Stage transition to {target} evaluated and permitted.",
    )
    db.commit()
    return {**result, "target": target, "current": current_stage()}
