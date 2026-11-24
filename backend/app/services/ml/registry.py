"""Model lifecycle: register, train, evaluate, promote, roll back.

The promotion workflow is the safety-critical part of Phase 6. Two rules:

- **Training cannot promote.** `train_version` always writes a `candidate`.
  Promotion is a separate call that records who did it and why.
- **Every promotion is reversible.** The replaced version id is stored, so
  rollback is a lookup rather than a reconstruction.

Nothing in this module is called automatically by a scheduler. A model change is
an event a human can point at in an audit log.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.market import MarketCandle, Timeframe
from app.models.ml import (
    DriftEvent,
    DriftSeverity,
    EvaluationKind,
    FeatureSnapshot,
    Model,
    ModelEvaluation,
    ModelFamily,
    ModelStatus,
    ModelVersion,
)
from app.services.ml import features as feature_lib
from app.services.ml import training as training_lib

logger = logging.getLogger(__name__)

DEFAULT_MODELS = (
    {
        "code": "direction_logistic_v1",
        "name": "Directional logistic baseline",
        "family": ModelFamily.LOGISTIC,
        "task": "direction",
        "deterministic": True,
        "description": (
            "Logistic regression over lagged price features. Serves as the honest "
            "baseline every future model must beat out-of-sample."
        ),
    },
)


def seed_models(db: Session) -> dict[str, int]:
    created = 0
    updated = 0
    for spec in DEFAULT_MODELS:
        existing = db.scalar(select(Model).where(Model.code == spec["code"]))
        if existing is None:
            db.add(Model(**spec))
            created += 1
        else:
            existing.name = spec["name"]
            existing.description = spec["description"]
            existing.deterministic = spec["deterministic"]
            updated += 1
    db.flush()
    return {"created": created, "updated": updated, "total": created + updated}


def _candles(db: Session, symbol: str, timeframe: Timeframe, limit: int) -> list[MarketCandle]:
    from app.models.market import Instrument

    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if instrument is None:
        raise ValueError(f"Unknown instrument '{symbol}'")
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(MarketCandle.instrument_id == instrument.id, MarketCandle.timeframe == timeframe)
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
        )
    )
    return list(reversed(rows))


def build_matrix_for(
    db: Session, symbol: str, timeframe: str = "1d", *, limit: int = 1200, horizon: int = 1
) -> feature_lib.FeatureMatrix:
    timeframe_enum = Timeframe(timeframe)
    rows = _candles(db, symbol, timeframe_enum, limit)
    return feature_lib.build_matrix(
        opens=[float(row.open) for row in rows],
        highs=[float(row.high) for row in rows],
        lows=[float(row.low) for row in rows],
        closes=[float(row.close) for row in rows],
        volumes=[float(row.volume or 0) for row in rows],
        timestamps=[row.open_time.isoformat() for row in rows],
        horizon=horizon,
    )


def _store_evaluation(
    db: Session,
    version: ModelVersion,
    kind: EvaluationKind,
    metrics: training_lib.Metrics,
    *,
    out_of_sample: bool,
) -> ModelEvaluation:
    payload = metrics.as_dict()
    evaluation = ModelEvaluation(
        version_id=version.id,
        kind=kind,
        is_out_of_sample=out_of_sample,
        rows=metrics.rows,
        accuracy=metrics.accuracy,
        balanced_accuracy=metrics.balanced_accuracy,
        precision=metrics.precision,
        recall=metrics.recall,
        roc_auc=metrics.roc_auc,
        brier=metrics.brier,
        log_loss=metrics.log_loss,
        baseline_accuracy=metrics.baseline_accuracy,
        edge_over_baseline=metrics.edge_over_baseline,
        calibration_slope=metrics.calibration_slope,
        calibration_intercept=metrics.calibration_intercept,
        max_calibration_error=metrics.max_calibration_error,
        bins=payload["bins"],
    )
    db.add(evaluation)
    return evaluation


def train_version(
    db: Session,
    *,
    model_code: str,
    symbol: str,
    timeframe: str = "1d",
    limit: int = 1200,
    horizon: int = 1,
    created_by: str | None = None,
) -> ModelVersion:
    """Train a candidate. Never promotes, whatever the metrics say."""
    model = db.scalar(select(Model).where(Model.code == model_code))
    if model is None:
        # The shipped model definitions are code, not user data. If the registry
        # has not been seeded yet, seed it once and look again, so training does
        # not depend on the caller having visited the listing endpoint first.
        seed_models(db)
        db.flush()
        model = db.scalar(select(Model).where(Model.code == model_code))
    if model is None:
        raise ValueError(f"Unknown model '{model_code}'")
    if model.family != ModelFamily.LOGISTIC:
        raise ValueError(
            f"Model family '{model.family.value}' is registered but not implemented in Phase 6"
        )

    matrix = build_matrix_for(db, symbol, timeframe, limit=limit, horizon=horizon)
    if not matrix.usable.any():
        raise ValueError(
            "No usable training rows: the instrument has too little history for these "
            "features, or every row falls inside the label horizon"
        )

    result = training_lib.train(matrix, config=training_lib.TrainingConfig(horizon=horizon))

    latest = db.scalar(
        select(ModelVersion)
        .where(ModelVersion.model_id == model.id)
        .order_by(ModelVersion.version.desc())
    )
    next_version = (latest.version + 1) if latest else 1

    rows = _candles(db, symbol, Timeframe(timeframe), limit)
    version = ModelVersion(
        model_id=model.id,
        version=next_version,
        status=ModelStatus.CANDIDATE,
        instrument_symbol=symbol,
        timeframe=timeframe,
        feature_names=",".join(result.feature_names),
        training_rows=result.training_rows,
        training_start=rows[0].open_time if rows else None,
        training_end=rows[-1].open_time if rows else None,
        hyperparameters=json.dumps(result.config.__dict__),
        seed=result.config.seed,
        label_horizon=horizon,
        label_kind="direction",
        parameters=json.dumps(result.parameters),
        checksum=result.checksum,
        promotion_note=training_lib.summarise(result),
    )
    db.add(version)
    db.flush()

    _store_evaluation(db, version, EvaluationKind.TRAIN, result.train, out_of_sample=False)
    _store_evaluation(
        db, version, EvaluationKind.VALIDATION, result.validation, out_of_sample=False
    )
    _store_evaluation(
        db, version, EvaluationKind.OUT_OF_SAMPLE, result.out_of_sample, out_of_sample=True
    )

    for summary in feature_lib.describe(matrix.values[matrix.usable], matrix.names):
        db.add(
            FeatureSnapshot(
                version_id=version.id,
                feature=summary["feature"],
                count=int(summary["count"]),  # type: ignore[arg-type]
                mean=summary["mean"],  # type: ignore[arg-type]
                std=summary["std"],  # type: ignore[arg-type]
                minimum=summary["minimum"],  # type: ignore[arg-type]
                maximum=summary["maximum"],  # type: ignore[arg-type]
                deciles=",".join(str(value) for value in summary["deciles"]),  # type: ignore[union-attr]
            )
        )

    # Walk-forward folds are stored as JSON on the version note: they are a list,
    # and inventing a table for one list is not worth the join.
    version.hyperparameters = json.dumps(
        {**result.config.__dict__, "walk_forward": result.walk_forward}
    )
    db.flush()
    return version


def promotion_decision(db: Session, version: ModelVersion) -> tuple[bool, str]:
    oos = db.scalar(
        select(ModelEvaluation).where(
            ModelEvaluation.version_id == version.id,
            ModelEvaluation.kind == EvaluationKind.OUT_OF_SAMPLE,
        )
    )
    if oos is None:
        return False, "No out-of-sample evaluation recorded."

    hyper = json.loads(version.hyperparameters or "{}")
    folds = hyper.get("walk_forward", [])
    result = training_lib.TrainingResult(
        parameters={},
        feature_names=[],
        train=training_lib.Metrics(rows=0),
        validation=training_lib.Metrics(rows=0),
        out_of_sample=training_lib.Metrics(
            rows=oos.rows,
            accuracy=oos.accuracy,
            edge_over_baseline=oos.edge_over_baseline,
            roc_auc=oos.roc_auc,
        ),
        walk_forward=folds,
        training_rows=version.training_rows,
        checksum=version.checksum,
        config=training_lib.TrainingConfig(),
    )
    return training_lib.should_promote(result)


def promote(
    db: Session, *, version_id: str, actor_id: str, note: str, force: bool = False
) -> ModelVersion:
    """Make a version the champion, recording what it replaced.

    `force` exists for a human override and is recorded as such. It cannot be
    reached through the training path.
    """
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise ValueError("Version not found")
    if version.status == ModelStatus.CHAMPION:
        return version

    ok, reason = promotion_decision(db, version)
    if not ok and not force:
        raise ValueError(f"Promotion refused: {reason}")

    previous = db.scalar(
        select(ModelVersion).where(
            ModelVersion.model_id == version.model_id,
            ModelVersion.status == ModelStatus.CHAMPION,
        )
    )
    if previous is not None and previous.id != version.id:
        previous.status = ModelStatus.RETIRED
        previous.retired_at = datetime.now(UTC)
        version.replaced_version_id = previous.id

    version.status = ModelStatus.CHAMPION
    version.promoted_at = datetime.now(UTC)
    version.promoted_by = actor_id
    version.promotion_note = (
        f"{note} [{'FORCED OVERRIDE' if force and not ok else 'gate passed'}: {reason}]"
    )
    db.flush()
    logger.info("promoted model version %s (%s)", version.version, note)
    return version


def rollback(db: Session, *, version_id: str, actor_id: str, note: str = "") -> ModelVersion:
    """Restore the version this one replaced."""
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise ValueError("Version not found")
    if not version.replaced_version_id:
        raise ValueError("This version has no predecessor to roll back to")

    previous = db.get(ModelVersion, version.replaced_version_id)
    if previous is None:
        raise ValueError("Predecessor version no longer exists")

    version.status = ModelStatus.RETIRED
    version.retired_at = datetime.now(UTC)
    previous.status = ModelStatus.CHAMPION
    previous.promoted_at = datetime.now(UTC)
    previous.promoted_by = actor_id
    previous.promotion_note = f"Rolled back from v{version.version}. {note}".strip()
    db.flush()
    return previous


def reject(db: Session, *, version_id: str, note: str) -> ModelVersion:
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise ValueError("Version not found")
    if version.status == ModelStatus.CHAMPION:
        raise ValueError("A champion must be rolled back, not rejected")
    version.status = ModelStatus.REJECTED
    version.promotion_note = note
    db.flush()
    return version


def predict_probability(
    db: Session, *, version_id: str, matrix: feature_lib.FeatureMatrix
) -> float | None:
    """Score the most recent usable row with a stored version.

    Returns None rather than a guess when the row's features are incomplete.
    """
    import numpy as np

    version = db.get(ModelVersion, version_id)
    if version is None or not version.parameters:
        return None
    payload = json.loads(version.parameters)
    weights = np.asarray(payload["weights"], dtype=float)
    bias = float(payload["bias"])
    mean = np.asarray(payload["mean"], dtype=float)
    std = np.asarray(payload["std"], dtype=float)

    complete = ~np.isnan(matrix.values).any(axis=1)
    if not complete.any():
        return None
    row = matrix.values[complete][-1]
    scaled = (row - mean) / np.where(std == 0, 1.0, std)
    return float(training_lib.predict(scaled.reshape(1, -1), weights, bias)[0])


# ----------------------------------------------------------------------- drift


def detect_drift(
    db: Session,
    *,
    version_id: str,
    symbol: str,
    timeframe: str = "1d",
    limit: int = 250,
    warn_z: float = 1.0,
    critical_z: float = 2.5,
) -> list[DriftEvent]:
    """Compare recent feature distributions with the training reference.

    Drift is reported, never acted on. A drifted model is a reason for a human to
    look, not a trigger for the system to change its own rules.
    """
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise ValueError("Version not found")

    reference = {
        snapshot.feature: snapshot
        for snapshot in db.scalars(
            select(FeatureSnapshot).where(FeatureSnapshot.version_id == version.id)
        )
    }
    if not reference:
        raise ValueError("No reference distribution stored for this version")

    matrix = build_matrix_for(db, symbol, timeframe, limit=limit, horizon=version.label_horizon)
    complete = ~__import__("numpy").isnan(matrix.values).any(axis=1)
    if not complete.any():
        raise ValueError("No complete feature rows in the recent window")

    recent = matrix.values[complete]
    events: list[DriftEvent] = []
    for column, name in enumerate(matrix.names):
        snapshot = reference.get(name)
        if snapshot is None or snapshot.std in (None, 0):
            continue
        recent_mean = float(recent[:, column].mean())
        z = (recent_mean - float(snapshot.mean)) / float(snapshot.std)
        if abs(z) < warn_z:
            continue
        severity = DriftSeverity.CRITICAL if abs(z) >= critical_z else DriftSeverity.WARNING
        events.append(
            DriftEvent(
                version_id=version.id,
                instrument_symbol=symbol,
                feature=name,
                severity=severity,
                metric="standardised mean shift",
                value=round(z, 4),
                threshold=critical_z if severity == DriftSeverity.CRITICAL else warn_z,
                message=(
                    f"{name} mean moved {z:+.2f} standard deviations from the training reference "
                    f"({float(snapshot.mean):.4f} -> {recent_mean:.4f})."
                ),
                detected_at=datetime.now(UTC),
                recommendation=(
                    "Investigate before relying on this version. Retraining or rolling back is a "
                    "human decision; nothing is changed automatically."
                ),
            )
        )

    # A model whose predictions are drifting is a separate signal from its inputs
    # drifting, so both are recorded.
    outcome_z = None
    if len(recent) >= 30:
        outcome_z = None  # label monitoring arrives with the strategy evaluator in Phase 7
    if outcome_z is not None:  # pragma: no cover - reserved
        events.append(
            DriftEvent(
                version_id=version.id,
                instrument_symbol=symbol,
                feature="__prediction__",
                severity=DriftSeverity.WARNING,
                metric="prediction drift",
                value=outcome_z,
                detected_at=datetime.now(UTC),
                message="Prediction distribution has shifted.",
            )
        )

    for event in events:
        db.add(event)
    db.flush()
    return events
