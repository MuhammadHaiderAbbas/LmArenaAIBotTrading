import { describe, expect, it } from "vitest";
import {
  FEATURE_NAMES,
  buildRows,
  driftFor,
  gate,
  models,
  overview,
  train,
  verdict,
  versionsFor,
} from "./modelEngine";

describe("feature engineering", () => {
  it("produces one row per labelled bar", () => {
    const rows = buildRows("GBP/USD");
    expect(rows.length).toBeGreaterThan(100);
    for (const row of rows) {
      expect(row.values).toHaveLength(FEATURE_NAMES.length);
      expect([0, 1]).toContain(row.label);
    }
  });

  it("never emits NaN in a usable row", () => {
    // A NaN here would silently poison every downstream coefficient.
    for (const row of buildRows("EUR/USD")) {
      expect(row.values.every((value) => Number.isFinite(value))).toBe(true);
    }
  });

  it("does not use future data in a feature", () => {
    // Truncating the series must not change earlier rows. The demo engine is
    // seeded from the symbol, so the overlap is directly comparable.
    const full = buildRows("SPY");
    const truncated = buildRows("SPY", "1d", 200);
    const offset = full.length - truncated.length;
    expect(offset).toBeGreaterThan(0);
    const compared = Math.min(truncated.length, 50);
    for (let index = 0; index < compared; index += 1) {
      expect(truncated[truncated.length - 1 - index].values[0]).toBeCloseTo(
        full[full.length - 1 - index].values[0],
        10,
      );
    }
  });

  it("refuses a symbol the demo cannot price", () => {
    expect(buildRows("NOT/REAL")).toEqual([]);
    expect(() => train({ symbol: "NOT/REAL" })).toThrow(/no priceable history/);
  });
});

describe("training", () => {
  it("splits forward in time and reports out-of-sample rows last", () => {
    const output = train({ symbol: "GBP/USD" });
    expect(output.train.rows).toBeGreaterThan(output.outOfSample.rows);
    expect(output.outOfSample.is_out_of_sample).toBe(true);
    expect(output.train.is_out_of_sample).toBe(false);
    expect(output.validation.is_out_of_sample).toBe(false);
  });

  it("is deterministic for the same instrument", () => {
    const first = train({ symbol: "BTC/USD" });
    const second = train({ symbol: "BTC/USD" });
    expect(first.checksum).toBe(second.checksum);
    expect(first.outOfSample.accuracy).toBe(second.outOfSample.accuracy);
  });

  it("scores against a majority-class baseline", () => {
    const output = train({ symbol: "GBP/USD" });
    expect(output.outOfSample.baseline_accuracy).not.toBeNull();
    expect(output.outOfSample.edge_over_baseline).not.toBeNull();
    expect(output.outOfSample.baseline_accuracy!).toBeGreaterThanOrEqual(0.5);
  });

  it("records walk-forward folds", () => {
    const output = train({ symbol: "AAPL" });
    expect(output.walkForward.length).toBeGreaterThanOrEqual(2);
    for (const fold of output.walkForward) {
      expect(fold.test_rows).toBeGreaterThan(0);
      expect(fold.train_rows).toBeLessThan(fold.train_rows + fold.test_rows);
    }
  });

  it("keeps the checksum shaped like a hash", () => {
    expect(train({ symbol: "GBP/USD" }).checksum).toHaveLength(64);
  });
});

describe("the promotion gate", () => {
  it("refuses a model without a real edge", () => {
    const result = gate(
      {
        kind: "out_of_sample",
        is_out_of_sample: true,
        rows: 120,
        accuracy: 0.5,
        balanced_accuracy: 0.5,
        roc_auc: null,
        brier: 0.25,
        baseline_accuracy: 0.51,
        edge_over_baseline: -0.01,
        calibration_slope: null,
        max_calibration_error: 0.1,
        bins: [],
      },
      [],
    );
    expect(result.passed).toBe(false);
    expect(result.reason).toContain("below the 0.02 floor");
  });

  it("refuses a thin out-of-sample sample however good it looks", () => {
    const result = gate(
      {
        kind: "out_of_sample",
        is_out_of_sample: true,
        rows: 20,
        accuracy: 0.9,
        balanced_accuracy: 0.9,
        roc_auc: null,
        brier: 0.1,
        baseline_accuracy: 0.5,
        edge_over_baseline: 0.4,
        calibration_slope: null,
        max_calibration_error: 0.02,
        bins: [],
      },
      [],
    );
    expect(result.passed).toBe(false);
    expect(result.reason).toContain("need 60");
  });

  it("refuses an unstable model even with a positive overall edge", () => {
    const result = gate(
      {
        kind: "out_of_sample",
        is_out_of_sample: true,
        rows: 200,
        accuracy: 0.56,
        balanced_accuracy: 0.55,
        roc_auc: null,
        brier: 0.24,
        baseline_accuracy: 0.51,
        edge_over_baseline: 0.05,
        calibration_slope: null,
        max_calibration_error: 0.1,
        bins: [],
      },
      [
        { fold: 1, train_rows: 100, test_rows: 50, accuracy: 0.6, edge_over_baseline: 0.08 },
        { fold: 2, train_rows: 150, test_rows: 50, accuracy: 0.46, edge_over_baseline: -0.06 },
        { fold: 3, train_rows: 200, test_rows: 50, accuracy: 0.45, edge_over_baseline: -0.07 },
      ],
    );
    expect(result.passed).toBe(false);
    expect(result.reason).toContain("walk-forward folds");
  });

  it("passes a model that clears every bar", () => {
    const result = gate(
      {
        kind: "out_of_sample",
        is_out_of_sample: true,
        rows: 200,
        accuracy: 0.58,
        balanced_accuracy: 0.57,
        roc_auc: null,
        brier: 0.22,
        baseline_accuracy: 0.51,
        edge_over_baseline: 0.07,
        calibration_slope: null,
        max_calibration_error: 0.05,
        bins: [],
      },
      [
        { fold: 1, train_rows: 100, test_rows: 50, accuracy: 0.6, edge_over_baseline: 0.08 },
        { fold: 2, train_rows: 150, test_rows: 50, accuracy: 0.57, edge_over_baseline: 0.05 },
      ],
    );
    expect(result.passed).toBe(true);
  });

  it("writes a verdict that names the baseline", () => {
    const output = train({ symbol: "GBP/USD" });
    expect(verdict(output.outOfSample, output.walkForward)).toContain("majority-class baseline");
  });
});

describe("registry", () => {
  it("ships no champion, because nothing has passed its gate", () => {
    const versions = versionsFor("GBP/USD");
    expect(versions.some((version) => version.status === "champion")).toBe(false);
    expect(overview("GBP/USD").champions).toBe(0);
  });

  it("keeps rejected versions in the history", () => {
    expect(versionsFor("GBP/USD").some((version) => version.status === "rejected")).toBe(true);
  });

  it("is stable across calls", () => {
    expect(versionsFor("SPY").map((version) => version.id)).toEqual(
      versionsFor("SPY").map((version) => version.id),
    );
  });

  it("lists registered families with an implementation flag", () => {
    const registered = models();
    expect(registered.length).toBeGreaterThanOrEqual(2);
    expect(registered.find((model) => model.code === "direction_logistic_v1")?.deterministic).toBe(true);
    // Registering a family is not the same as implementing it.
    expect(registered.find((model) => model.code === "direction_gbdt_v1")?.deterministic).toBe(false);
  });

  it("reports drift as advisory, never as an action", () => {
    for (const event of driftFor("BTC/USD")) {
      expect(event.recommendation).toContain("human decision");
      expect(event.recommendation).toContain("nothing is changed automatically");
    }
  });
});
