// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import * as readiness from "./readinessEngine";
import * as monitoring from "./monitoringEngine";
import * as execution from "./executionEngine";

beforeEach(() => {
  localStorage.clear();
  monitoring.resetDemo();
  execution.resetDemo();
});

describe("the assessment", () => {
  it("is not live ready", () => {
    const report = readiness.assessment();
    expect(report.live_ready).toBe(false);
    expect(report.blocking_for_live.length).toBeGreaterThan(0);
  });

  it("gives every blocking item a way to unblock it", () => {
    for (const entry of readiness.assessment().blocking_for_live) {
      expect(entry.unblocked_by).toBeTruthy();
    }
  });

  it("counts every item exactly once", () => {
    const report = readiness.assessment();
    const total = Object.values(report.counts).reduce((sum, value) => sum + value, 0);
    expect(total).toBe(report.items.length);
  });

  it("uses only the four honest statuses", () => {
    const allowed = new Set(["done", "partial", "blocked", "not_implemented"]);
    for (const entry of readiness.assessment().items) {
      expect(allowed.has(entry.status)).toBe(true);
    }
  });

  it("cites evidence for every item", () => {
    for (const entry of readiness.assessment().items) {
      expect(entry.evidence.length).toBeGreaterThan(10);
    }
  });

  it("marks KYC as not built", () => {
    const kyc = readiness.assessment().items.find((entry) => entry.key === "kyc.identity_verification");
    expect(kyc?.status).toBe("not_implemented");
    expect(kyc?.blocks_live).toBe(true);
  });

  it("marks the audit trail done only while the chain verifies", () => {
    monitoring.record("a", "one");
    monitoring.record("b", "two");
    expect(
      readiness.assessment().items.find((entry) => entry.key === "privacy.audit_trail")?.status,
    ).toBe("done");
    monitoring.tamperForDemo();
    expect(
      readiness.assessment().items.find((entry) => entry.key === "privacy.audit_trail")?.status,
    ).toBe("blocked");
  });
});

describe("the release gate", () => {
  it("passes for the paper scope", () => {
    expect(readiness.releaseGate().verdict).toBe("pass");
  });

  it("fails when the audit chain is broken", () => {
    monitoring.record("a", "one");
    monitoring.record("b", "two");
    monitoring.tamperForDemo();
    expect(readiness.releaseGate().verdict).toBe("fail");
  });

  it("still reports the compliance count so a pass cannot read as ready", () => {
    const gate = readiness.releaseGate();
    expect(gate.verdict).toBe("pass");
    expect(gate.compliance_blocking_count).toBeGreaterThan(0);
  });

  it("distinguishes blocking checks from informational ones", () => {
    const gate = readiness.releaseGate();
    expect(gate.blocking_checks).toEqual([
      "no_live_adapter",
      "audit_chain_verifies",
      "risk_controller_separate",
    ]);
    const informational = gate.checks.filter((check) => !check.blocking);
    expect(informational).toHaveLength(1);
    expect(informational[0].passed).toBe(false);
  });
});

describe("the stage model", () => {
  it("has three stages and cannot reach the third", () => {
    const report = readiness.stageReport();
    expect(report.stages.map((stage) => stage.key)).toEqual([
      "stage_1_private",
      "stage_2_research_saas",
      "stage_3_live",
    ]);
    expect(report.current.next_reachable).toBe(false);
    expect(report.stages[2].status).toBe("not_implemented");
  });
});

describe("broker connections", () => {
  it("accepts a sandbox connection", () => {
    expect(readiness.connectBroker({ broker: "simulated", mode: "sandbox" }).ok).toBe(true);
  });

  it("refuses a live connection with the same words the server uses", () => {
    const result = readiness.connectBroker({ broker: "simulated", mode: "live" });
    expect(result.ok).toBe(false);
    expect(result.message).toContain("Live broker execution is not implemented");
  });

  it("refuses an unknown broker", () => {
    const result = readiness.connectBroker({ broker: "interactive-brokers", mode: "sandbox" });
    expect(result.ok).toBe(false);
    expect(result.message).toContain("No adapter is registered");
  });

  it("lists no broker with live support", () => {
    expect(readiness.brokerCatalogue().brokers.every((broker) => !broker.live_supported)).toBe(true);
  });
});
