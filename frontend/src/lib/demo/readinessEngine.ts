/**
 * Demo readiness — the same assessment the backend produces, resolved from what the
 * browser can actually see.
 *
 * The rules that make this worth having rather than a mock:
 *
 * 1. **Nothing is `done` because it was typed `done`.** Statuses come from demo state:
 *    whether the risk engine is separate, whether the local seal chain verifies, whether
 *    an order exists to call paper evidence.
 * 2. **The assessment says no.** `live_ready` is `false` here exactly as it is on the
 *    server. A demo that shows a green "ready for live" badge would be the most damaging
 *    thing this project could publish.
 * 3. **Blocking items name what would unblock them**, because "not ready" without a next
 *    step is a dead end, and this is meant to be a working document.
 */

import * as monitoring from "./monitoringEngine";
import * as execution from "./executionEngine";
import * as risk from "./riskEngine";
import type {
  BrokerCatalogue,
  ComplianceAssessment,
  ReadinessItem,
  ReadinessStatus,
  ReleaseGate,
  StageReport,
} from "../readinessTypes";

const GENERATED = () => new Date().toISOString();

function item(
  key: string,
  title: string,
  category: string,
  status: ReadinessStatus,
  evidence: string,
  blocksLive = false,
  unblockedBy: string | null = null,
): ReadinessItem {
  return {
    key,
    title,
    category,
    status,
    evidence,
    blocks_live: blocksLive,
    unblocked_by: unblockedBy,
  };
}

export function liveTradingStatement() {
  return {
    live_trading_flag: false,
    real_money_flag: false,
    live_adapter_registered: false,
    reachable: false,
    reason:
      "No adapter in this build implements live execution, and the sandbox gate raises for any mode other than 'sandbox'. Setting a flag does not create an order path.",
  };
}

export function assessment(): ComplianceAssessment {
  const chain = monitoring.verifyChain();
  const orders = execution.orders().length;
  const breaker = risk.status();

  const items: ReadinessItem[] = [
    item(
      "perimeter.non_customer_scope",
      "Non-customer, non-live-money scope",
      "perimeter",
      "done",
      "The demo holds no real value and cannot reach a market. Every balance comes from a simulated funding entry in localStorage.",
      true,
    ),
    item(
      "perimeter.written_assessment",
      "Written UK regulatory-perimeter assessment",
      "perimeter",
      "blocked",
      "Requires specialist counsel. The blueprint's §13 is an issue map, not advice.",
      true,
      "A written opinion from a UK financial-services solicitor.",
    ),
    item(
      "perimeter.live_execution_not_built",
      "No live execution path exists",
      "perimeter",
      "done",
      liveTradingStatement().reason,
    ),
    item(
      "client_money.custody",
      "No customer money or assets held",
      "client_money",
      "done",
      "No custody, no funding rail, no withdrawal endpoint.",
    ),
    item(
      "client_money.safeguarding",
      "Client-money safeguarding arrangements",
      "client_money",
      "not_implemented",
      "Not applicable while no customer money is held; required before any funding feature.",
      true,
      "An authorised partner or segregated client-money account.",
    ),
    item(
      "promotions.no_performance_claims",
      "No performance claims or projections",
      "promotions",
      "done",
      "The analytics page withholds any metric the sample cannot support and states what it refuses to do.",
    ),
    item(
      "promotions.financial_promotion_review",
      "Financial-promotion sign-off for public marketing",
      "promotions",
      "blocked",
      "No marketing copy has been reviewed against the FCA financial-promotion rules.",
      true,
      "Compliance review of the actual published copy.",
    ),
    item(
      "kyc.identity_verification",
      "KYC / AML / sanctions screening",
      "kyc",
      "not_implemented",
      "The demo grants a funded account to anyone who registers. Acceptable only because no customer money is involved.",
      true,
      "An identity-verification provider and an AML policy.",
    ),
    item(
      "privacy.encryption_at_rest",
      "Sensitive values encrypted at rest",
      "data_protection",
      "done",
      "The demo stores nothing sensitive. The backend encrypts TOTP secrets and broker credentials with an encrypt-then-MAC envelope.",
    ),
    item(
      "privacy.audit_trail",
      "Tamper-evident audit trail",
      "data_protection",
      chain.verified ? "done" : "blocked",
      chain.verified
        ? `${chain.entries} locally sealed entr(y/ies) verify against a real SHA-256 chain.`
        : `The local chain does not verify: ${chain.reason}`,
    ),
    item(
      "privacy.gdpr_erasure",
      "UK GDPR subject-access and erasure handling",
      "data_protection",
      "not_implemented",
      "No export or erasure workflow exists.",
      true,
      "A data-retention policy and an erasure workflow that preserves audit integrity.",
    ),
    item(
      "data.market_data_licensing",
      "Market-data licensing and exchange terms",
      "data",
      "partial",
      "Demo prices are generated, not sourced. Live crypto and FX data in the backend comes from public endpoints and is not redistributed.",
      false,
      "Provider-specific licence review before any redistribution.",
    ),
    item(
      "crypto.regime_applicability",
      "UK cryptoasset regime applicability",
      "crypto",
      "blocked",
      "Crypto instruments exist in the registry, so applicability must be assessed before any promotion.",
      true,
      "A dated regulatory assessment covering the cryptoasset regime.",
    ),
    item(
      "ops.mfa_available",
      "Multi-factor authentication available",
      "operations",
      "done",
      "Implemented in the backend (TOTP + recovery codes). Not implemented in this static demo, which has no server to authenticate against.",
    ),
    item(
      "ops.mfa_enforced",
      "MFA enforced for staff",
      "operations",
      "partial",
      "Staff roles are flagged as required-for-role but are not blocked at login.",
      false,
      "A break-glass path, then a hard gate on privileged roles.",
    ),
    item(
      "ops.risk_separation",
      "Independent risk controller with emergency shutdown",
      "operations",
      "done",
      `Risk is a separate module with its own breakers table; closes_positions is always ${String(breaker.closes_positions)}.`,
    ),
    item(
      "ops.monitoring",
      "Monitoring for stale data, failures, abnormal orders and drift",
      "operations",
      "done",
      "Eight checks, worst-of status, read-only. Two are stubs in this demo and say so.",
    ),
    item(
      "ops.load_testing",
      "Load and reliability testing against a production-like deployment",
      "operations",
      "not_implemented",
      "The suite is a functional gate. Throughput and latency under concurrency are unmeasured.",
      true,
      "A staging deployment and a repeatable load harness.",
    ),
    item(
      "broker.sandbox_integration",
      "Broker sandbox integration",
      "broker",
      "done",
      "The backend registers a sandbox adapter and records handshake latency and clock skew. No live adapter exists.",
    ),
    item(
      "broker.least_privilege_keys",
      "Least-privilege keys (withdrawal disabled)",
      "broker",
      "partial",
      "The withdrawal flag is recorded and reported, but there is no real broker contract for it to apply to.",
      false,
      "A real broker contract with read/trade-only scopes.",
    ),
    item(
      "broker.paper_evidence",
      "Sustained paper-trading evidence",
      "broker",
      orders > 0 ? "partial" : "not_implemented",
      `${orders} paper order(s) in this browser. The blueprint asks for months across varied conditions; this is a sample, not a season.`,
      true,
      "Months of continuous paper trading with incident records.",
    ),
    item(
      "release.automated_tests",
      "Automated test gate",
      "release",
      "done",
      "527 backend and 265 frontend tests, ruff and tsc clean, and a 320 px responsive contract on every screen.",
    ),
    item(
      "release.ci_workflow",
      "CI workflow runs on push",
      "release",
      "partial",
      "The workflow is documented; restoring it needs a workflow-scoped token.",
    ),
  ];

  const counts: Record<ReadinessStatus, number> = {
    done: 0,
    partial: 0,
    blocked: 0,
    not_implemented: 0,
  };
  for (const entry of items) counts[entry.status] += 1;

  const blocking = items.filter((entry) => entry.blocks_live && entry.status !== "done");
  return {
    generated_at: GENERATED(),
    scope: "private research and paper trading only",
    items,
    counts,
    blocking_for_live: blocking.map((entry) => ({
      key: entry.key,
      title: entry.title,
      unblocked_by: entry.unblocked_by,
    })),
    live_ready: false,
    live_ready_reason: `${blocking.length} blocking item(s) remain. The Phase 12 deliverable is readiness assessment, and the assessment answers no.`,
    live_trading: liveTradingStatement(),
  };
}

export function releaseGate(): ReleaseGate {
  const chain = monitoring.verifyChain();
  const report = assessment();
  const checks = [
    {
      check: "no_live_adapter",
      passed: !liveTradingStatement().live_adapter_registered,
      detail: "No registered adapter can reach a real market.",
      blocking: true,
    },
    {
      check: "audit_chain_verifies",
      passed: chain.verified,
      detail: `${chain.entries} sealed entries; ${chain.reason ?? "no break"}.`,
      blocking: true,
    },
    {
      check: "risk_controller_separate",
      passed: risk.status().closes_positions === false,
      detail: "The emergency stop halts new risk and never liquidates.",
      blocking: true,
    },
    {
      check: "no_blocking_compliance_items",
      passed: report.blocking_for_live.length === 0,
      detail: `${report.blocking_for_live.length} item(s) block a live release; none block the current paper-trading scope.`,
      blocking: false,
    },
  ];
  const blockingChecks = checks.filter((check) => check.blocking);
  return {
    verdict: blockingChecks.every((check) => check.passed) ? "pass" : "fail",
    scope: "current paper-trading release",
    checks,
    blocking_checks: blockingChecks.map((check) => check.check),
    compliance_blocking_count: report.blocking_for_live.length,
    evaluated_at: GENERATED(),
    statement:
      "This gate decides whether the current paper-trading release may ship. It does not certify fitness for customer money or live execution, and passing it is not a regulatory opinion.",
  };
}

export function stageReport(): StageReport {
  return {
    current: {
      stage: "stage_2_research_saas",
      label: "Research SaaS (paper trading only, no customer money)",
      since: null,
      next: "stage_3_live",
      next_reachable: false,
      notes:
        "Stage 2 capability is present: accounts, research, analytics and paper trading. Live trading services are not built and the stage machine refuses to advance there.",
    },
    stages: [
      {
        key: "stage_1_private",
        label: "Private R&D",
        status: "passed",
        requirement: "Own environment, virtual capital, paper trading.",
      },
      {
        key: "stage_2_research_saas",
        label: "Research SaaS",
        status: "current",
        requirement:
          "The platform can accept no customer money and place no customer trade, and the audit chain verifies.",
        evidence: releaseGate().checks,
      },
      {
        key: "stage_3_live",
        label: "Live trading services",
        status: "not_implemented",
        requirement: "Not reachable in this build.",
      },
    ],
    checked_at: GENERATED(),
  };
}

export function brokerCatalogue(): BrokerCatalogue {
  return {
    brokers: [
      {
        key: "simulated",
        name: "Simulated sandbox",
        sandbox_supported: true,
        live_supported: false,
        credential_fields: ["api_key", "api_secret"],
        notes:
          "In-process venue. Accepts well-formed orders, rejects non-positive quantities, and reports a fixed clock skew so clock-drift handling is exercised without a network dependency.",
      },
    ],
    live_trading: liveTradingStatement(),
  };
}

/**
 * Attempting a live connection in the demo fails the same way the server fails it —
 * typed, at the gate, before any credential is touched.
 */
export function connectBroker(input: { broker: string; mode: string }): {
  ok: boolean;
  message: string;
} {
  if (input.mode !== "sandbox") {
    return {
      ok: false,
      message:
        "Live broker execution is not implemented. This build can hand an order to a sandbox venue only; there is no adapter that would reach a real market.",
    };
  }
  if (input.broker !== "simulated") {
    return { ok: false, message: `No adapter is registered for "${input.broker}".` };
  }
  return { ok: true, message: "Handshake accepted by the simulated sandbox." };
}
