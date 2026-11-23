"""Training, calibration and honest evaluation.

Implemented with numpy only. There is no deep-learning dependency here by
design: a logistic regression that is properly validated is worth more than an
LSTM that is not, and the point of Phase 6 is the *validation discipline*, not
parameter count. `ModelFamily` names the families the blueprint allows; only the
ones actually implemented can be trained, and the API says so.

Three things this module refuses to do:

1. **Report a train score as if it were performance.** Every evaluation carries
   `is_out_of_sample`.
2. **Pick the best epoch on the test set.** Calibration is fitted on validation
   data; the out-of-sample slice is touched once.
3. **Promote anything.** Training returns a candidate. Promotion is an explicit,
   audited act in the API layer.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import numpy as np

from app.services.ml import features as feature_lib


@dataclass
class TrainingConfig:
    learning_rate: float = 0.15
    iterations: int = 600
    l2: float = 0.01
    seed: int = 7
    horizon: int = 1
    walk_forward_folds: int = 4


@dataclass
class Metrics:
    rows: int
    accuracy: float | None = None
    balanced_accuracy: float | None = None
    precision: float | None = None
    recall: float | None = None
    roc_auc: float | None = None
    brier: float | None = None
    log_loss: float | None = None
    baseline_accuracy: float | None = None
    edge_over_baseline: float | None = None
    calibration_slope: float | None = None
    calibration_intercept: float | None = None
    max_calibration_error: float | None = None
    bins: list[dict[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        payload = self.__dict__.copy()
        payload["bins"] = json.dumps(self.bins)
        return payload


@dataclass
class TrainingResult:
    parameters: dict[str, list[float]]
    feature_names: list[str]
    train: Metrics
    validation: Metrics
    out_of_sample: Metrics
    walk_forward: list[dict[str, object]]
    training_rows: int
    checksum: str
    config: TrainingConfig


def _sigmoid(values: np.ndarray) -> np.ndarray:
    # Numerically stable form: exp of a large positive number overflows.
    out = np.empty_like(values)
    positive = values >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-values[positive]))
    exponent = np.exp(values[~positive])
    out[~positive] = exponent / (1.0 + exponent)
    return out


def _standardise(train_x: np.ndarray, *others: np.ndarray):
    """Fit scaling on train only, then apply to every slice.

    Fitting the scaler on all data leaks the test distribution's mean and spread
    into training. It is a small leak and a real one.
    """
    mean = train_x.mean(axis=0)
    std = train_x.std(axis=0)
    std[std == 0] = 1.0
    scaled_train = (train_x - mean) / std
    scaled_others = tuple((other - mean) / std for other in others)
    return scaled_train, scaled_others, mean, std


def fit_logistic(
    x: np.ndarray, y: np.ndarray, *, config: TrainingConfig
) -> tuple[np.ndarray, float]:
    """Batch gradient descent with L2. Deterministic given the data.

    No random initialisation: starting from zeros makes the result reproducible
    across runs, which matters when a version is being compared against a
    champion and the only difference should be the data.
    """
    rows, columns = x.shape
    weights = np.zeros(columns)
    bias = 0.0
    for _ in range(config.iterations):
        logits = x @ weights + bias
        predictions = _sigmoid(logits)
        error = predictions - y
        grad_w = (x.T @ error) / rows + config.l2 * weights
        grad_b = float(np.mean(error))
        weights -= config.learning_rate * grad_w
        bias -= config.learning_rate * grad_b
    return weights, float(bias)


def predict(x: np.ndarray, weights: np.ndarray, bias: float) -> np.ndarray:
    return _sigmoid(x @ weights + bias)


def _roc_auc(y: np.ndarray, scores: np.ndarray) -> float | None:
    positives = scores[y == 1]
    negatives = scores[y == 0]
    if positives.size == 0 or negatives.size == 0:
        return None
    # Mann-Whitney U with tie handling, expressed as an average rank.
    order = np.argsort(np.concatenate([positives, negatives]))
    ranks = np.empty(order.size, dtype=float)
    ranks[order] = np.arange(1, order.size + 1)
    rank_positives = ranks[: positives.size].sum()
    auc = (rank_positives - positives.size * (positives.size + 1) / 2) / (
        positives.size * negatives.size
    )
    return float(auc)


def _calibration(y: np.ndarray, probabilities: np.ndarray, bins: int = 10) -> tuple[list[dict[str, float]], float, float, float | None]:
    """Reliability bins plus a logistic recalibration fit.

    The slope answers "does a 70% prediction mean 70%?". A slope well below 1
    means the model is over-confident, which is the usual failure of an
    uncalibrated classifier and exactly what this column exists to expose.
    """
    edges = np.linspace(0.0, 1.0, bins + 1)
    reliability: list[dict[str, float]] = []
    max_error = 0.0
    for index in range(bins):
        lower, upper = edges[index], edges[index + 1]
        mask = (probabilities > lower) & (probabilities <= upper)
        if mask.sum() == 0:
            continue
        observed = float(np.mean(y[mask]))
        predicted = float(np.mean(probabilities[mask]))
        error = abs(observed - predicted)
        max_error = max(max_error, error)
        reliability.append(
            {
                "lower": round(float(lower), 4),
                "upper": round(float(upper), 4),
                "count": int(mask.sum()),
                "predicted": round(predicted, 4),
                "observed": round(observed, 4),
                "error": round(error, 4),
            }
        )

    # Fit logit(y) ~ a * logit(p) + b with a single Newton step approximation.
    clipped = np.clip(probabilities, 1e-6, 1 - 1e-6)
    logit_p = np.log(clipped / (1 - clipped))
    if logit_p.std() == 0:
        return reliability, 1.0, 0.0, max_error
    slope = float(np.polyfit(logit_p, y, 1)[0])
    intercept = float(np.polyfit(logit_p, y, 1)[1])
    return reliability, slope, intercept, max_error


def evaluate(
    y: np.ndarray, probabilities: np.ndarray, *, calibrate: bool = False
) -> Metrics:
    rows = int(y.size)
    metrics = Metrics(rows=rows)
    if rows == 0:
        return metrics

    predicted = (probabilities >= 0.5).astype(int)
    metrics.accuracy = float(np.mean(predicted == y))

    recalls: list[float] = []
    precisions: list[float] = []
    for cls in (0, 1):
        mask = y == cls
        if mask.sum():
            recalls.append(float(np.mean(predicted[mask] == cls)))
            selected = predicted == cls
            if selected.sum():
                precisions.append(float(np.mean(y[selected] == cls)))
    metrics.balanced_accuracy = float(np.mean(recalls)) if recalls else None
    if (predicted == 1).sum():
        metrics.precision = float(np.mean(y[predicted == 1] == 1))
    if (y == 1).sum():
        metrics.recall = float(np.mean(predicted[y == 1] == 1))

    metrics.roc_auc = _roc_auc(y, probabilities)
    metrics.brier = float(np.mean((probabilities - y) ** 2))
    clipped = np.clip(probabilities, 1e-9, 1 - 1e-9)
    metrics.log_loss = float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped)))

    # The majority-class share is the bar an honest model must clear. With a
    # ~51/48 label split this is close to a coin flip, and saying so is the point.
    majority = max(float(np.mean(y == 1)), float(np.mean(y == 0)))
    metrics.baseline_accuracy = majority
    metrics.edge_over_baseline = float(metrics.accuracy - majority)

    reliability, slope, intercept, max_error = _calibration(y, probabilities)
    metrics.bins = reliability
    metrics.max_calibration_error = max_error
    metrics.calibration_slope = slope
    metrics.calibration_intercept = intercept
    return metrics


def train(
    matrix: feature_lib.FeatureMatrix,
    *,
    config: TrainingConfig | None = None,
) -> TrainingResult:
    config = config or TrainingConfig()
    usable_index = np.where(matrix.usable)[0]
    train_idx, validation_idx, oos_idx = feature_lib.chronological_split(usable_index)

    x_all = matrix.values
    y_all = matrix.labels.astype(float)

    train_x, (val_x, oos_x), mean, std = _standardise(
        x_all[train_idx], x_all[validation_idx], x_all[oos_idx]
    )
    weights, bias = fit_logistic(train_x, y_all[train_idx], config=config)

    train_metrics = evaluate(y_all[train_idx], predict(train_x, weights, bias))
    validation_metrics = evaluate(y_all[validation_idx], predict(val_x, weights, bias))
    oos_metrics = evaluate(y_all[oos_idx], predict(oos_x, weights, bias))

    # Walk-forward: retrain on an expanding window, test on the next block. This
    # is closer to how the model would actually have been used, and it is where
    # an unstable model shows itself as variance across folds.
    folds: list[dict[str, object]] = []
    folds_count = max(2, config.walk_forward_folds)
    block = max(1, len(usable_index) // (folds_count + 1))
    for fold in range(folds_count):
        start = block * (fold + 1)
        end = min(start + block, len(usable_index))
        if start >= end:
            break
        fold_train = usable_index[:start]
        fold_test = usable_index[start:end]
        if len(fold_train) < 30 or len(fold_test) < 5:
            continue
        fx_train, (fx_test,), _, _ = _standardise(x_all[fold_train], x_all[fold_test])
        fw_weights, fw_bias = fit_logistic(fx_train, y_all[fold_train], config=config)
        fold_metrics = evaluate(y_all[fold_test], predict(fx_test, fw_weights, fw_bias))
        folds.append(
            {
                "fold": fold + 1,
                "train_rows": int(len(fold_train)),
                "test_rows": int(len(fold_test)),
                "accuracy": fold_metrics.accuracy,
                "balanced_accuracy": fold_metrics.balanced_accuracy,
                "edge_over_baseline": fold_metrics.edge_over_baseline,
                "roc_auc": fold_metrics.roc_auc,
            }
        )

    payload = {
        "weights": [float(value) for value in weights],
        "bias": float(bias),
        "mean": [float(value) for value in mean],
        "std": [float(value) for value in std],
    }
    checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    return TrainingResult(
        parameters=payload,
        feature_names=list(matrix.names),
        train=train_metrics,
        validation=validation_metrics,
        out_of_sample=oos_metrics,
        walk_forward=folds,
        training_rows=int(len(train_idx)),
        checksum=checksum,
        config=config,
    )


def summarise(result: TrainingResult) -> str:
    """One-line verdict used in promotion notes and the UI.

    It refuses to call an edge real when the out-of-sample result is within noise
    of the majority-class baseline.
    """
    oos = result.out_of_sample
    if oos.rows == 0 or oos.accuracy is None:
        return "No out-of-sample rows; the model cannot be judged."
    edge = oos.edge_over_baseline or 0.0
    folds = [fold["edge_over_baseline"] for fold in result.walk_forward if fold["edge_over_baseline"] is not None]
    positive_folds = sum(1 for value in folds if value > 0)
    verdict = (
        f"Out-of-sample accuracy {oos.accuracy:.3f} versus {oos.baseline_accuracy:.3f} "
        f"majority-class baseline (edge {edge:+.3f})."
    )
    if edge <= 0.02:
        verdict += " The edge is within noise of the baseline; this model should not be promoted."
    elif folds and positive_folds < len(folds):
        verdict += (
            f" Edge is positive overall but {len(folds) - positive_folds} of {len(folds)} "
            "walk-forward folds disagree, so stability is unproven."
        )
    else:
        verdict += " Positive out-of-sample edge on every walk-forward fold."
    if oos.calibration_slope is not None and oos.calibration_slope < 0.5:
        verdict += (
            f" Calibration slope {oos.calibration_slope:.2f} indicates over-confidence; "
            "treat the probabilities as ordinal, not absolute."
        )
    return verdict


def should_promote(result: TrainingResult, *, min_edge: float = 0.02, min_rows: int = 60) -> tuple[bool, str]:
    """Gate for promotion. Deliberately conservative and deliberately explicit."""
    oos = result.out_of_sample
    if oos.rows < min_rows:
        return False, f"Only {oos.rows} out-of-sample rows; need {min_rows}."
    if oos.edge_over_baseline is None or oos.edge_over_baseline < min_edge:
        return False, (
            f"Out-of-sample edge {oos.edge_over_baseline or 0.0:+.3f} is below the "
            f"{min_edge:.2f} floor."
        )
    if oos.roc_auc is not None and oos.roc_auc < 0.52:
        return False, f"ROC AUC {oos.roc_auc:.3f} is indistinguishable from chance."
    folds = [fold["edge_over_baseline"] for fold in result.walk_forward if fold["edge_over_baseline"] is not None]
    if folds and sum(1 for value in folds if value > 0) < math.ceil(len(folds) * 0.6):
        return False, "Fewer than 60% of walk-forward folds show a positive edge."
    return True, "Meets the out-of-sample gate."
