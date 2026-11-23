# Phase 6 — AI model engineering & learning

**Status:** complete · **Scope owner:** quantitative engineering · **Date:** 23 November 2026

Phase 6 adds the model lifecycle: leakage-controlled datasets, candidate training, comparison with
the champion out of sample, and **controlled promotion with versioning and rollback**. The hard
constraint from the blueprint governs every decision below:

> The system may propose model changes, but must not silently rewrite production trading rules or
> automatically promote unvalidated models.

## 1. Objective

Give the platform an honest answer to "is this model any better than a coin flip, and can we prove
it?" — and make the answer impossible to skip.

## 2. Delivered scope

### 2.1 Feature engineering (`backend/app/services/ml/features.py`)

15 features per bar, each computed only from data available at that bar's close: lagged returns,
SMA/EMA gaps, RSI, MACD histogram, ATR percentage, Bollinger position, realised volatility and its
ratio, bar range, close location within the bar, and a volume z-score.

**Leakage control is tested, not asserted.** `test_features_do_not_leak_future_data` recomputes the
matrix on a truncated series and requires the overlapping rows to match exactly. Any feature that
peeked at the future would change when the series is cut.

Labels are the forward return over `horizon` bars. Rows whose features are incomplete, or whose
label window is not yet closed, are **excluded rather than imputed** — imputing a moving average you
could not yet compute invents information.

### 2.2 Training and evaluation (`training.py`)

- **Families:** `ModelFamily` enumerates logistic, gradient boost, random forest and baseline.
  Only **logistic regression** is implemented, in numpy, with batch gradient descent and L2. A
  registered family that has no code cannot be trained, and the API says so rather than silently
  substituting one.
- **Split:** 60% train / 20% validation / 20% out-of-sample, strictly forward in time. A random
  split would let adjacent rows share label windows and would flatter every model.
- **Scaling is fitted on train only** and applied to validation and out-of-sample.
- **Walk-forward:** four expanding-window folds, retrained each time, so instability shows up as
  variance across folds instead of hiding in an aggregate.
- **Every evaluation carries `is_out_of_sample`.** A train score can never be presented as
  performance.

Metrics recorded per slice: accuracy, balanced accuracy, precision, recall, ROC AUC, Brier score,
log loss, **majority-class baseline accuracy**, **edge over baseline**, calibration slope and
intercept, maximum calibration error, and reliability bins.

The baseline is the point. On a 51/48 label split, 51% accuracy is a coin flip with extra steps,
and `edge_over_baseline` makes that visible in every table.

### 2.3 The promotion gate (`training.should_promote`)

A version is promotable only if **all** of the following hold on the out-of-sample slice:

| Gate | Threshold |
| --- | --- |
| Out-of-sample rows | ≥ 60 |
| Edge over majority-class baseline | ≥ +0.02 |
| ROC AUC | ≥ 0.52 |
| Walk-forward folds with a positive edge | ≥ 60% |

Failure returns a reason string, and the reason is shown in the API, the UI and the audit trail.

### 2.4 Lifecycle and persistence (`registry.py`, `app/models/ml.py`)

Five tables:

| Table | Contents |
| --- | --- |
| `model_registry` | Model definitions, family, task, whether code exists |
| `model_versions` | Version, status, features in order, training window, hyperparameters, weights, checksum, `replaced_version_id` |
| `model_evaluations` | Per-slice metrics including `is_out_of_sample` and reliability bins |
| `feature_snapshots` | Per-feature reference distribution (mean, std, min, max, deciles) |
| `drift_events` | Severity, metric, threshold, message, advisory recommendation |

Status is a one-way-ish lifecycle: `candidate → champion → retired`, with `rejected` as a terminal
side branch. `promote()` stores `replaced_version_id`, so `rollback()` is a lookup rather than a
reconstruction.

**Weights are stored as JSON, not pickle.** A pickle is executable content: loading one from a
database is an arbitrary-code path, and a model registry should not have one. The JSON payload also
carries the standardisation mean and standard deviation, so a version is reproducible from its own
record plus the raw candles.

The feature list is stored in order and asserted against `FEATURE_NAMES` in tests. Column four can
never be silently reinterpreted as something else.

### 2.5 Drift monitoring

Recent feature means are compared with the stored training reference and expressed as a
standardised shift. ≥1.0σ is a warning, ≥2.5σ is critical. Every event carries:

> "Investigate before relying on this version. Retraining or rolling back is a human decision;
> nothing is changed automatically."

Drift is a reason for a human to look. It is not a trigger.

### 2.6 AI Desk integration

The quantitative agent (previously registered as unbuilt) now reports the champion's probability
with the numbers needed to read it honestly: the baseline it was measured against, the edge, the
ROC AUC, and the calibration slope. It takes **no stance** from the probability, and
`confidence_kind` is `uncalibrated` — a model output is not a calibrated trade signal.

When no champion exists — the normal state — it reports an `absence` with `data_available: false`.
`tradeable` remains `False` on every run.

## 3. The safety argument, in one paragraph

Training cannot promote: `train_version` writes `candidate` unconditionally, and no code path from
training reaches champion. Promotion is a separate staff-only endpoint that re-derives the gate from
stored evaluations and refuses with a reason. `force=True` exists for a human override and stamps
`FORCED OVERRIDE` into the promotion note, so an override cannot masquerade as a pass. Nothing in
this phase writes to trading rules: the deterministic rules live in code, and Phase 6 can only
change what an agent *says*, never what the system *does*.

## 4. Evidence from this build

On real BTC/USD candles from the demo provider (721 bars, 396 usable training rows):

```
out_of_sample  accuracy 0.481  baseline 0.504  edge -0.023  auc 0.536
gate: False — "Out-of-sample edge -0.023 is below the 0.02 floor."
promotion: refused
```

That is the correct result. A logistic model on lagged price features should not beat the market,
and a pipeline that reported otherwise would be leaking. The demo therefore ships with **zero
champions**, and the UI says "Nothing has passed the gate" rather than showing a fake deployment.

## 5. Bugs found and fixed during Phase 6

1. **Dead volume feature.** A constant-volume series produced `NaN` for `volume_z`, which silently
   made the whole instrument untrainable. The feature now returns 0 ("at the average") when the
   volume standard deviation is zero.
2. **Silent on a negative edge.** The quantitative agent tested `abs(edge) < 0.02`, so a model
   *worse* than baseline produced no caveat at all. It now distinguishes "within noise" from "below
   baseline" and says which.
3. **Bare probability headline.** The "no caveats" branch keyed off an empty uncertainty list, so a
   model that cleared the 0.02 edge floor but sat below a 0.52 AUC could be presented without a
   caveat. The headline now requires both an edge ≥ 0.02 and AUC ≥ 0.55.
4. **Whole-run failure on an unpriceable symbol** (frontend). The demo model engine threw on a
   symbol it could not price, taking the entire AI run down. It now returns no versions, and the
   agent reports an absence.
5. **Training depended on endpoint order.** `/models/train` failed with "Unknown model" if
   `/models` had not been called first. The registry now seeds itself on first use.

## 6. Deliberately not in Phase 6

- **No automatic retraining.** A scheduler that retrains nightly is a Phase 10 monitoring concern.
- **No gradient boosting, forests, LSTM, transformer or RL.** They are registered as families and
  explicitly unimplemented. Writing a model before there is a baseline edge to beat is how projects
  acquire complexity without performance.
- **No strategy backtesting or economics.** Costs, slippage, position sizing and Sharpe are Phase 7.
- **No promotion of models into the trading path.** There is still no trading path; nothing in this
  phase can create an order.
- **No probability→stance conversion.** The agent reports a number; it does not act on it.

## 7. Review checklist

- [x] Feature computation is prefix-invariant (leakage test passes both directions).
- [x] Train/validation/out-of-sample are strictly forward in time.
- [x] Scaling parameters are fitted on training data only.
- [x] Every stored evaluation states whether it is out of sample.
- [x] A random-walk model cannot pass the gate (asserted in `test_a_random_walk_model_cannot_pass_the_promotion_gate`).
- [x] `train_version` cannot produce a champion (asserted in `test_training_creates_a_candidate_never_a_champion`).
- [x] Promotion records actor, note, gate verdict and the replaced version.
- [x] Rollback restores the predecessor and retires the incumbent.
- [x] Model weights are JSON, never pickle.
- [x] Drift events are advisory and carry a human-decision recommendation.
- [x] `tradeable` is still always `False`.
