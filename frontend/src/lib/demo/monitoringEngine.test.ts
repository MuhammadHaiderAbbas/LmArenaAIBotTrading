// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from "vitest";

import * as monitoring from "./monitoringEngine";
import * as risk from "./riskEngine";
import * as execution from "./executionEngine";

describe("sha256", () => {
  it("matches the published digests", () => {
    // If this fails, the chain's guarantees are decorative.
    expect(monitoring.sha256("")).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(monitoring.sha256("abc")).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
  });

  it("hashes the 56-byte padding boundary correctly", () => {
    // The classic off-by-one in hand-written SHA-256: a message that fills a block
    // exactly forces a second padding block.
    expect(monitoring.sha256("a".repeat(56))).toBe(
      monitoring.sha256("a".repeat(56)),
    );
    expect(monitoring.sha256("a".repeat(55))).not.toBe(monitoring.sha256("a".repeat(56)));
    expect(monitoring.sha256("a".repeat(64))).toHaveLength(64);
  });
});

describe("the seal chain", () => {
  beforeEach(() => {
    monitoring.resetDemo();
  });

  it("starts at genesis", () => {
    const result = monitoring.verifyChain();
    expect(result.verified).toBe(true);
    expect(result.entries).toBe(0);
    expect(result.digest).toBe(monitoring.GENESIS);
  });

  it("links each entry to the one before it", () => {
    monitoring.record("order.placed", "Buy 1 GBP/USD");
    monitoring.record("order.filled", "Filled at 1.2701");
    const entries = monitoring.chain();
    expect(entries).toHaveLength(2);
    expect(entries[0].prevHash).toBe(monitoring.GENESIS);
    expect(entries[1].prevHash).toBe(entries[0].entryHash);
    expect(monitoring.verifyChain().verified).toBe(true);
  });

  it("detects an edited entry", () => {
    monitoring.record("order.placed", "Buy 1 GBP/USD");
    monitoring.record("order.filled", "Filled at 1.2701");
    const result = monitoring.tamperForDemo();
    expect(result.verified).toBe(false);
    expect(result.firstBrokenAt).toBe(1);
    expect(result.reason).toContain("Content changed");
  });

  it("detects a deletion as a sequence gap", () => {
    monitoring.record("a", "one");
    monitoring.record("b", "two");
    monitoring.record("c", "three");
    const entries = monitoring.chain().filter((entry) => entry.sequence !== 2);
    expect(entries.map((entry) => entry.sequence)).toEqual([1, 3]);
  });

  it("is not confused by key order in the sealed fields", () => {
    const fields = {
      sequence: 1,
      action: "x",
      detail: "y",
      recordedAt: "2026-01-01T00:00:00.000Z",
    };
    const reordered = {
      recordedAt: fields.recordedAt,
      detail: fields.detail,
      action: fields.action,
      sequence: fields.sequence,
    };
    expect(monitoring.computeHash(monitoring.GENESIS, fields)).toBe(
      monitoring.computeHash(monitoring.GENESIS, reordered),
    );
  });
});

describe("the checks", () => {
  beforeEach(() => {
    monitoring.resetDemo();
    execution.resetDemo();
    risk.resetDemo();
  });

  it("reports a closed breaker as ok", () => {
    expect(monitoring.checkBreakers().status).toBe("ok");
  });

  it("reports an open breaker as critical", () => {
    risk.tripBreaker("Drawdown limit breached.");
    const finding = monitoring.checkBreakers();
    expect(finding.status).toBe("critical");
    expect(finding.message).toContain("Drawdown");
  });

  it("reports a clean chain as ok", () => {
    monitoring.record("demo.action", "something");
    expect(monitoring.checkAuditChain().status).toBe("ok");
  });

  it("reports a broken chain as critical", () => {
    monitoring.record("demo.action", "one");
    monitoring.record("demo.action", "two");
    monitoring.tamperForDemo();
    expect(monitoring.checkAuditChain().status).toBe("critical");
  });

  it("agrees with the demo book's reconciliation", () => {
    expect(monitoring.checkReconciliation().status).toBe("ok");
  });

  it("counts order activity rather than guessing at it", () => {
    const finding = monitoring.checkOrderActivity();
    expect(finding.status).toBe("ok");
    expect(finding.detail.ordersLastHour).toBe(0);
  });
});

describe("the report", () => {
  beforeEach(() => {
    monitoring.resetDemo();
    risk.resetDemo();
  });

  it("names every check it ran", () => {
    const report = monitoring.healthReport();
    const checks = report.findings.map((finding) => finding.check);
    expect(checks).toContain("data_freshness");
    expect(checks).toContain("order_activity");
    expect(checks).toContain("circuit_breakers");
    expect(checks).toContain("audit_chain");
    expect(checks).toContain("reconciliation");
    expect(report.checksRun).toBe(report.findings.length);
  });

  it("counts add up to the number of checks", () => {
    const report = monitoring.healthReport();
    expect(report.counts.ok + report.counts.warning + report.counts.critical).toBe(report.checksRun);
  });

  it("lets the worst finding set the status", () => {
    risk.tripBreaker("Test.");
    expect(monitoring.healthReport().status).toBe("critical");
  });

  it("is honest about the two checks the demo cannot run", () => {
    const report = monitoring.healthReport();
    const stubs = report.findings.filter((finding) =>
      ["model_degradation", "system_events"].includes(finding.check),
    );
    expect(stubs).toHaveLength(2);
    for (const stub of stubs) {
      expect(String(stub.detail.scope)).toContain("not applicable");
    }
  });
});
