/**
 * Demo implementation of the model registry.
 *
 * It mirrors the backend's *discipline*, not its numbers: a candidate is trained
 * on a strictly forward time split, scored out-of-sample against a majority-class
 * baseline, gates itself, and every candidate needs an explicit human promotion.
 * Nothing here is auto-promoted, and nothing it produces can reach a trade.
 *
 * The demo deliberately ships a model that FAILS its gate. That is the honest
 * state of this platform on synthetic data, and showing a green "deployed" badge
 * here would teach the wrong lesson.
 */

import { candlesForSymbol } from "./marketEngine";
import type { Timeframe } from "../marketTypes";

export type ModelStatus = "draft" | "candidate" | "champion" | "retired" | "rejected";

export interface CalibrationBin {
  lower: number;
  upper: number;
  count: number;
  predicted: number;
  observed: number;
}

export interface Evaluation {
  kind: "train" | "validation" | "out_of_sample";
  is_out_of_sample: boolean;
  rows: number;
  accuracy: number | null;
  balanced_accuracy: number | null;
  roc_auc: number | null;
  brier: number | null;
  baseline_accuracy: number | null;
  edge_over_baseline: number | null;
  calibration_slope: number | null;
  max_calibration_error: number | null;
  bins: CalibrationBin[];
}

export interface WalkForwardFold {
  fold: number;
  train_rows: number;
  test_rows: number;
  accuracy: number;
  edge_over_baseline: number;
}

export interface ModelVersion {
  id: string;
  model_code: string;
  version: number;
  status: ModelStatus;
  instrument_symbol: string;
  timeframe: string;
  feature_names: string[];
  training_rows: number;
  training_window: string;
  label_horizon: number;
  checksum: string;
  promoted_at: string | null;
  promotion_note: string;
  gate_passed: boolean;
  gate_reason: string;
  evaluations: Evaluation[];
  walk_forward: WalkForwardFold[];
  feature_snapshots: { feature: string; mean: number; std: number; minimum: number; maximum: number }[];
}

export interface DriftEvent {
  id: string;
  feature: string;
  severity: "info" | "warning" | "critical";
  metric: string;
  value: number;
  threshold: number;
  message: string;
  recommendation: string;
  acknowledged: boolean;
}

export interface ModelOverview {
  models_total: number;
  versions_total: number;
  candidates: number;
  champions: number;
  rejected: number;
  drift_events_open: number;
  best_edge: number | null;
}

export const FEATURE_NAMES = [
  "return_1",
  "return_5",
  "return_20",
  "sma_gap_20",
  "sma_gap_50",
  "ema_gap_20",
  "rsi_14",
  "macd_hist",
  "atr_pct",
  "bb_position",
  "volatility_20",
  "volatility_ratio",
  "range_pct",
  "close_location",
  "volume_z",
] as const;

// ---------------------------------------------------------------- primitives

function mean(values: number[]): number {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function stdev(values: number[]): number {
  if (values.length < 2) return 0;
  const mu = mean(values);
  return Math.sqrt(values.reduce((sum, value) => sum + (value - mu) ** 2, 0) / (values.length - 1));
}

function sigmoid(value: number): number {
  return 1 / (1 + Math.exp(-value));
}

/** Deterministic pseudo-noise so a reload shows the same numbers. */
function hashNoise(seed: string, index: number): number {
  let hash = 2166136261;
  const text = `${seed}:${index}`;
  for (let position = 0; position < text.length; position += 1) {
    hash ^= text.charCodeAt(position);
    hash = Math.imul(hash, 16777619);
  }
  // Map to [-1, 1).
  return ((hash >>> 0) % 20001) / 10000 - 1;
}

// ------------------------------------------------------------------ features

export interface FeatureRow {
  values: number[];
  label: number;
}

/**
 * Build lagged features and forward-return labels from the demo candle series.
 *
 * Every feature at row i uses only closes at or before i. The label uses i + 1,
 * and the last row is dropped because its future is unknown. Keeping those two
 * rules in one place is what stops look-ahead bias creeping in.
 */
export function buildRows(symbol: string, timeframe: Timeframe = "1d", count = 420): FeatureRow[] {
  const candles = candlesForSymbol(symbol, timeframe, count);
  // A symbol the demo cannot price must not be silently substituted: return no
  // rows so the caller reports "no data" rather than training on the wrong series.
  if (!candles) return [];
  const closes = candles.map((candle) => candle.close);
  const highs = candles.map((candle) => candle.high);
  const lows = candles.map((candle) => candle.low);

  const rolling = (period: number, offset: number): number => {
    const index = offset;
    if (index < period - 1) return Number.NaN;
    const window = closes.slice(index - period + 1, index + 1);
    return mean(window);
  };

  const rows: FeatureRow[] = [];
  for (let index = 0; index < closes.length - 1; index += 1) {
    const price = closes[index];
    const sma20 = rolling(20, index);
    const sma50 = rolling(50, index);
    const returns1 = index >= 1 ? (price - closes[index - 1]) / closes[index - 1] : Number.NaN;
    const returns5 = index >= 5 ? (price - closes[index - 5]) / closes[index - 5] : Number.NaN;
    const returns20 = index >= 20 ? (price - closes[index - 20]) / closes[index - 20] : Number.NaN;
    const window10 = index >= 9 ? closes.slice(index - 9, index + 1) : [];
    const window60 = index >= 59 ? closes.slice(index - 59, index + 1) : [];
    const rets = (window: number[]) =>
      window.slice(1).map((value, position) => (value - window[position]) / window[position]);
    const vol10 = window10.length > 1 ? stdev(rets(window10)) : Number.NaN;
    const vol60 = window60.length > 1 ? stdev(rets(window60)) : Number.NaN;

    const range = highs[index] - lows[index];
    const values = [
      returns1,
      returns5,
      returns20,
      Number.isFinite(sma20) ? (price - sma20) / sma20 : Number.NaN,
      Number.isFinite(sma50) ? (price - sma50) / sma50 : Number.NaN,
      Number.isFinite(sma20) ? (price - sma20) / sma20 : Number.NaN,
      // A crude RSI proxy: the share of up-closes in the trailing window.
      index >= 14
        ? (closes.slice(index - 13, index + 1).slice(1).filter((value, position) =>
            value > closes.slice(index - 13, index + 1)[position],
          ).length /
            14) *
            100
        : Number.NaN,
      Number.isFinite(sma20) && Number.isFinite(sma50) ? (sma20 - sma50) / sma50 : Number.NaN,
      range / price,
      index >= 19
        ? (price - Math.min(...lows.slice(index - 19, index + 1))) /
          Math.max(1e-9, Math.max(...highs.slice(index - 19, index + 1)) - Math.min(...lows.slice(index - 19, index + 1)))
        : Number.NaN,
      Number.isFinite(vol10) ? vol10 * Math.sqrt(252) * 100 : Number.NaN,
      Number.isFinite(vol10) && vol60 ? vol10 / vol60 : Number.NaN,
      range / price,
      range > 0 ? (price - lows[index]) / range : 0.5,
      0,
    ];

    const forward = closes[index + 1];
    const label = forward > price ? 1 : forward < price ? 0 : -1;
    if (values.some((value) => Number.isNaN(value))) continue;
    rows.push({ values, label });
  }
  return rows;
}

// ------------------------------------------------------------------ training

export interface TrainOptions {
  symbol: string;
  timeframe?: Timeframe;
  horizon?: number;
  seed?: string;
}

export interface TrainOutput {
  weights: number[];
  bias: number;
  scale: { mean: number[]; std: number[] };
  train: Evaluation;
  validation: Evaluation;
  outOfSample: Evaluation;
  walkForward: WalkForwardFold[];
  trainingRows: number;
  checksum: string;
  featureSnapshots: ModelVersion["feature_snapshots"];
}

function evaluate(rows: FeatureRow[], probabilities: number[]): Evaluation {
  if (rows.length === 0) {
    return {
      kind: "out_of_sample",
      is_out_of_sample: true,
      rows: 0,
      accuracy: null,
      balanced_accuracy: null,
      roc_auc: null,
      brier: null,
      baseline_accuracy: null,
      edge_over_baseline: null,
      calibration_slope: null,
      max_calibration_error: null,
      bins: [],
    };
  }
  const labels = rows.map((row) => row.label);
  const predicted = probabilities.map((probability) => (probability >= 0.5 ? 1 : 0));
  const accuracy = mean(predicted.map((value, index) => (value === labels[index] ? 1 : 0)));

  const recalls = [0, 1].map((cls) => {
    const indices = labels.map((label, index) => (label === cls ? index : -1)).filter((index) => index >= 0);
    if (indices.length === 0) return Number.NaN;
    return mean(indices.map((index) => (predicted[index] === cls ? 1 : 0)));
  }).filter((value) => !Number.isNaN(value));

  const brier = mean(probabilities.map((probability, index) => (probability - labels[index]) ** 2));
  const positives = labels.filter((label) => label === 1).length;
  const baseline = Math.max(positives / labels.length, 1 - positives / labels.length);

  // Reliability bins: predicted vs observed. A slope well below 1 means the model
  // is over-confident, which is the normal state of an uncalibrated classifier.
  const bins: CalibrationBin[] = [];
  for (let step = 0; step < 10; step += 1) {
    const lower = step / 10;
    const upper = (step + 1) / 10;
    const indices = probabilities
      .map((probability, index) => (probability > lower && probability <= upper ? index : -1))
      .filter((index) => index >= 0);
    if (indices.length === 0) continue;
    bins.push({
      lower,
      upper,
      count: indices.length,
      predicted: mean(indices.map((index) => probabilities[index])),
      observed: mean(indices.map((index) => labels[index])),
    });
  }
  const maxCalibrationError = bins.length
    ? Math.max(...bins.map((bin) => Math.abs(bin.predicted - bin.observed)))
    : null;

  return {
    kind: "out_of_sample",
    is_out_of_sample: true,
    rows: rows.length,
    accuracy,
    balanced_accuracy: recalls.length ? mean(recalls) : null,
    roc_auc: null,
    brier,
    baseline_accuracy: baseline,
    edge_over_baseline: accuracy - baseline,
    calibration_slope: null,
    max_calibration_error: maxCalibrationError,
    bins,
  };
}

function fit(rows: FeatureRow[], scale: { mean: number[]; std: number[] }): { weights: number[]; bias: number } {
  const widths = rows[0]?.values.length ?? FEATURE_NAMES.length;
  const weights = new Array(widths).fill(0);
  let bias = 0;
  const learningRate = 0.12;
  const iterations = 400;
  const l2 = 0.01;

  for (let step = 0; step < iterations; step += 1) {
    const gradient = new Array(widths).fill(0);
    let biasGradient = 0;
    for (const row of rows) {
      const scaled = row.values.map((value, index) => (value - scale.mean[index]) / (scale.std[index] || 1));
      const logit = scaled.reduce((sum, value, index) => sum + value * weights[index], bias);
      const error = sigmoid(logit) - row.label;
      for (let index = 0; index < widths; index += 1) gradient[index] += scaled[index] * error;
      biasGradient += error;
    }
    for (let index = 0; index < widths; index += 1) {
      weights[index] -= learningRate * (gradient[index] / rows.length + l2 * weights[index]);
    }
    bias -= learningRate * (biasGradient / rows.length);
  }
  return { weights, bias };
}

function score(rows: FeatureRow[], model: { weights: number[]; bias: number }, scale: { mean: number[]; std: number[] }): number[] {
  return rows.map((row) => {
    const logit = row.values.reduce(
      (sum, value, index) => sum + ((value - scale.mean[index]) / (scale.std[index] || 1)) * model.weights[index],
      model.bias,
    );
    return sigmoid(logit);
  });
}

function scaleFor(rows: FeatureRow[]): { mean: number[]; std: number[] } {
  const widths = rows[0]?.values.length ?? FEATURE_NAMES.length;
  const means: number[] = [];
  const stds: number[] = [];
  for (let index = 0; index < widths; index += 1) {
    const column = rows.map((row) => row.values[index]);
    means.push(mean(column));
    stds.push(stdev(column) || 1);
  }
  return { mean: means, std: stds };
}

export function train({ symbol, timeframe = "1d" }: TrainOptions): TrainOutput {
  const rows = buildRows(symbol, timeframe).filter((row) => row.label >= 0);
  if (rows.length === 0) throw new Error(`${symbol} has no priceable history in the demo feed`);
  if (rows.length < 40) throw new Error("Not enough usable rows to train on this instrument");

  const trainEnd = Math.floor(rows.length * 0.6);
  const validationEnd = trainEnd + Math.floor(rows.length * 0.2);
  const trainRows = rows.slice(0, trainEnd);
  const validationRows = rows.slice(trainEnd, validationEnd);
  const oosRows = rows.slice(validationEnd); // strictly after — no shuffling

  const scale = scaleFor(trainRows);
  const model = fit(trainRows, scale);
  const trainEval = { ...evaluate(trainRows, score(trainRows, model, scale)), kind: "train" as const, is_out_of_sample: false };
  const validationEval = {
    ...evaluate(validationRows, score(validationRows, model, scale)),
    kind: "validation" as const,
    is_out_of_sample: false,
  };
  const oosEval = evaluate(oosRows, score(oosRows, model, scale));

  const folds: WalkForwardFold[] = [];
  const blockSize = Math.max(1, Math.floor(rows.length / 5));
  for (let fold = 0; fold < 4; fold += 1) {
    const start = blockSize * (fold + 1);
    const end = Math.min(start + blockSize, rows.length);
    if (start >= end) break;
    const foldTrain = rows.slice(0, start);
    const foldTest = rows.slice(start, end);
    if (foldTrain.length < 30 || foldTest.length < 5) continue;
    const foldScale = scaleFor(foldTrain);
    const foldModel = fit(foldTrain, foldScale);
    const foldEval = evaluate(foldTest, score(foldTest, foldModel, foldScale));
    folds.push({
      fold: fold + 1,
      train_rows: foldTrain.length,
      test_rows: foldTest.length,
      accuracy: foldEval.accuracy ?? 0,
      edge_over_baseline: foldEval.edge_over_baseline ?? 0,
    });
  }

  const featureSnapshots = FEATURE_NAMES.map((feature, index) => {
    const column = oosRows.length ? oosRows.map((row) => row.values[index]) : trainRows.map((row) => row.values[index]);
    return {
      feature,
      mean: mean(column),
      std: stdev(column),
      minimum: Math.min(...column),
      maximum: Math.max(...column),
    };
  });

  const checksum = hashNoise(`${symbol}-${timeframe}-${rows.length}`, 7)
    .toString(16)
    .replace("-", "");

  return {
    weights: model.weights,
    bias: model.bias,
    scale,
    train: trainEval,
    validation: validationEval,
    outOfSample: oosEval,
    walkForward: folds,
    trainingRows: trainRows.length,
    checksum: (checksum + "0".repeat(64)).slice(0, 64),
    featureSnapshots,
  };
}

// ------------------------------------------------------------------ the gate

/** Same gate as the backend, stated in one place so the UI cannot drift from it. */
export function gate(evaluation: Evaluation, folds: WalkForwardFold[]): { passed: boolean; reason: string } {
  if (evaluation.rows < 60) {
    return { passed: false, reason: `Only ${evaluation.rows} out-of-sample rows; need 60.` };
  }
  const edge = evaluation.edge_over_baseline ?? 0;
  if (edge < 0.02) {
    return {
      passed: false,
      reason: `Out-of-sample edge ${edge >= 0 ? "+" : ""}${edge.toFixed(3)} is below the 0.02 floor.`,
    };
  }
  const positiveFolds = folds.filter((fold) => fold.edge_over_baseline > 0).length;
  if (folds.length && positiveFolds < Math.ceil(folds.length * 0.6)) {
    return {
      passed: false,
      reason: `Only ${positiveFolds} of ${folds.length} walk-forward folds show a positive edge.`,
    };
  }
  return { passed: true, reason: "Meets the out-of-sample gate." };
}

export function verdict(evaluation: Evaluation, folds: WalkForwardFold[]): string {
  const edge = evaluation.edge_over_baseline ?? 0;
  const base = `Out-of-sample accuracy ${(evaluation.accuracy ?? 0).toFixed(3)} versus ${(
    evaluation.baseline_accuracy ?? 0
  ).toFixed(3)} majority-class baseline (edge ${edge >= 0 ? "+" : ""}${edge.toFixed(3)}).`;
  if (edge <= 0.02) {
    return `${base} The edge is within noise of the baseline; this model should not be promoted.`;
  }
  const positiveFolds = folds.filter((fold) => fold.edge_over_baseline > 0).length;
  if (folds.length && positiveFolds < folds.length) {
    return `${base} Edge is positive overall but ${folds.length - positiveFolds} of ${folds.length} walk-forward folds disagree, so stability is unproven.`;
  }
  return `${base} Positive out-of-sample edge on every walk-forward fold.`;
}

// ----------------------------------------------------------------- registry

const MODELS = [
  {
    code: "direction_logistic_v1",
    name: "Directional logistic baseline",
    family: "logistic",
    task: "direction",
    description: "Logistic regression over lagged price features. The honest baseline every future model must beat out of sample.",
  },
  {
    code: "direction_gbdt_v1",
    name: "Gradient-boosted direction",
    family: "gradient_boost",
    task: "direction",
    description: "Gradient boosting over the same features. Registered, not yet implemented — it must beat the baseline before it earns code.",
  },
];

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 86_400_000).toISOString();
}

function buildVersion(symbol: string, version: number, status: ModelStatus, trainedDaysAgo: number): ModelVersion {
  const output = train({ symbol, timeframe: "1d" });
  const decision = gate(output.outOfSample, output.walkForward);
  return {
    id: `demo-${symbol.replace("/", "")}-v${version}`,
    model_code: "direction_logistic_v1",
    version,
    status,
    instrument_symbol: symbol,
    timeframe: "1d",
    feature_names: [...FEATURE_NAMES],
    training_rows: output.trainingRows,
    training_window: `${trainedDaysAgo + 419} days ending ${isoDaysAgo(trainedDaysAgo).slice(0, 10)}`,
    label_horizon: 1,
    checksum: output.checksum,
    promoted_at: status === "champion" ? isoDaysAgo(trainedDaysAgo - 1) : null,
    promotion_note: `[gate: ${decision.reason}]`,
    gate_passed: decision.passed,
    gate_reason: decision.reason,
    evaluations: [output.train, output.validation, output.outOfSample],
    walk_forward: output.walkForward,
    feature_snapshots: output.featureSnapshots,
  };
}

const cache = new Map<string, ModelVersion[]>();

export function versionsFor(symbol: string): ModelVersion[] {
  const existing = cache.get(symbol);
  if (existing) return existing;
  // An instrument the demo cannot price has no training history, so it has no
  // versions. Returning [] keeps the caller free to report an absence; throwing
  // here would take an entire AI run down over one unpriceable symbol.
  if (buildRows(symbol).length === 0) {
    cache.set(symbol, []);
    return [];
  }
  // No champion ships in the demo: nothing here has cleared its gate, and
  // pretending otherwise would misrepresent the platform.
  const versions = [
    buildVersion(symbol, 2, "candidate", 12),
    buildVersion(symbol, 1, "rejected", 40),
  ];
  cache.set(symbol, versions);
  return versions;
}

export function models() {
  return MODELS.map((model) => ({ ...model, deterministic: model.family === "logistic" }));
}

export function overview(symbol: string): ModelOverview {
  const versions = versionsFor(symbol);
  const edges = versions
    .flatMap((version) => version.evaluations.filter((evaluation) => evaluation.is_out_of_sample))
    .map((evaluation) => evaluation.edge_over_baseline)
    .filter((value): value is number => value !== null);
  return {
    models_total: MODELS.length,
    versions_total: versions.length,
    candidates: versions.filter((version) => version.status === "candidate").length,
    champions: versions.filter((version) => version.status === "champion").length,
    rejected: versions.filter((version) => version.status === "rejected").length,
    drift_events_open: driftFor(symbol).filter((event) => !event.acknowledged).length,
    best_edge: edges.length ? Math.max(...edges) : null,
  };
}

export function driftFor(symbol: string): DriftEvent[] {
  const versions = versionsFor(symbol);
  const reference = versions[0];
  const rows = buildRows(symbol, "1d");
  if (rows.length < 60) return [];
  const recent = rows.slice(-60);
  const events: DriftEvent[] = [];

  reference.feature_snapshots.forEach((snapshot, index) => {
    if (!snapshot.std) return;
    const recentMean = mean(recent.map((row) => row.values[index]));
    const z = (recentMean - snapshot.mean) / snapshot.std;
    if (Math.abs(z) < 1.5) return;
    events.push({
      id: `${symbol}-${snapshot.feature}`,
      feature: snapshot.feature,
      severity: Math.abs(z) >= 2.5 ? "critical" : "warning",
      metric: "standardised mean shift",
      value: z,
      threshold: Math.abs(z) >= 2.5 ? 2.5 : 1.5,
      message: `${snapshot.feature} moved ${z >= 0 ? "+" : ""}${z.toFixed(2)} standard deviations from the training reference.`,
      recommendation:
        "Investigate before relying on this version. Retraining or rolling back is a human decision; nothing is changed automatically.",
      acknowledged: false,
    });
  });
  return events;
}
