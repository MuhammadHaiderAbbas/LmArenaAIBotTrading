"""Feature engineering, training, evaluation, promotion gate and drift.

The tests that matter most here are the ones about *honesty*: that features do not
leak, that a random-walk model cannot pass the promotion gate, and that training
never promotes anything by itself.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.models.market import Instrument, MarketCandle, Timeframe, Venue
from app.models.ml import (
    DriftSeverity,
    EvaluationKind,
    Model,
    ModelFamily,
    ModelStatus,
    ModelVersion,
)
from app.services.ml import features as feature_lib
from app.services.ml import registry as ml_registry
from app.services.ml import training as training_lib


def noisy_series(rows: int, *, seed: int = 11, drift: float = 0.0, sigma: float = 0.01):
    rng = random.Random(seed)  # noqa: S311 - synthetic fixtures, not security
    closes = [100.0]
    for _ in range(rows - 1):
        closes.append(closes[-1] * (1 + rng.gauss(drift, sigma)))
    return closes


def matrix_for(closes: list[float], *, horizon: int = 1):
    rows = len(closes)
    return feature_lib.build_matrix(
        opens=[value * 0.999 for value in closes],
        highs=[value * 1.004 for value in closes],
        lows=[value * 0.996 for value in closes],
        closes=closes,
        volumes=[1000.0 + (index % 7) for index in range(rows)],
        timestamps=[f"2026-01-{index:04d}" for index in range(rows)],
        horizon=horizon,
    )


# ------------------------------------------------------------------- features


def test_features_and_labels_are_aligned() -> None:
    matrix = matrix_for(noisy_series(200))
    assert matrix.values.shape == (200, len(feature_lib.FEATURE_NAMES))
    assert matrix.labels.shape == (200,)
    # The final row cannot be labelled: its future is unknown.
    assert matrix.labels[-1] == -1
    assert matrix.usable[-1] == False  # noqa: E712


def test_features_do_not_leak_future_data() -> None:
    """Computing on a truncated series must not change earlier rows.

    This is the test that catches look-ahead bias. If any feature used a value from
    after row n, truncating at n would change row n-1.
    """
    closes = noisy_series(400, seed=3)
    full = matrix_for(closes)
    truncated = matrix_for(closes[:250])

    full_slice = full.values[:250]
    complete = ~np.isnan(full_slice).any(axis=1)
    assert complete.sum() > 100
    assert np.allclose(full_slice[complete], truncated.values[complete], equal_nan=True)


def test_labels_use_the_forward_return() -> None:
    closes = [100.0, 101.0, 100.5, 102.0, 101.0]
    matrix = feature_lib.build_matrix(
        opens=closes,
        highs=[value + 1 for value in closes],
        lows=[value - 1 for value in closes],
        closes=closes,
        volumes=[1.0] * 5,
        timestamps=[str(index) for index in range(5)],
        horizon=1,
    )
    # 100 -> 101 is up (1), 101 -> 100.5 is down (0), 100.5 -> 102 is up (1).
    assert matrix.labels[0] == 1
    assert matrix.labels[1] == 0
    assert matrix.labels[2] == 1
    # Final row has no label.
    assert matrix.labels[4] == -1


def test_constant_volume_does_not_kill_the_feature_row() -> None:
    """A flat volume column must not make an instrument untrainable."""
    closes = noisy_series(120)
    matrix = feature_lib.build_matrix(
        opens=closes,
        highs=[value * 1.01 for value in closes],
        lows=[value * 0.99 for value in closes],
        closes=closes,
        volumes=[0.0] * 120,
        timestamps=[str(index) for index in range(120)],
    )
    assert matrix.usable.sum() > 50


def test_split_is_strictly_forward_in_time() -> None:
    matrix = matrix_for(noisy_series(400))
    index = np.where(matrix.usable)[0]
    train, validation, oos = feature_lib.chronological_split(index)
    assert train.max() < validation.min() < oos.min()
    assert len(train) > len(validation)


def test_split_refuses_when_history_is_too_short() -> None:
    with pytest.raises(ValueError, match="not enough usable rows"):
        feature_lib.chronological_split(np.arange(10))


def test_mismatched_series_lengths_are_rejected() -> None:
    with pytest.raises(ValueError, match="same length"):
        feature_lib.build_matrix(
            opens=[1.0, 2.0],
            highs=[1.0],
            lows=[1.0, 2.0],
            closes=[1.0, 2.0],
            volumes=[1.0, 1.0],
            timestamps=["a", "b"],
        )


def test_feature_descriptions_capture_the_distribution() -> None:
    matrix = matrix_for(noisy_series(300))
    summaries = feature_lib.describe(matrix.values[matrix.usable], matrix.names)
    assert len(summaries) == len(matrix.names)
    for summary in summaries:
        assert summary["count"] > 0
        assert len(summary["deciles"]) == 9  # type: ignore[arg-type]


# ------------------------------------------------------------------- training


def test_training_produces_separate_out_of_sample_metrics() -> None:
    result = training_lib.train(matrix_for(noisy_series(500)))
    assert result.out_of_sample.rows > 0
    assert result.train.rows > result.out_of_sample.rows
    # Train accuracy is always >= OOS for this model; if it were not, the split
    # would be suspicious rather than impressive.
    assert result.train.accuracy >= result.out_of_sample.accuracy - 0.15


def test_a_random_walk_model_cannot_pass_the_promotion_gate() -> None:
    """The core honesty test.

    On a random walk there is no edge to find. A pipeline that promotes something
    here is broken, and this asserts it does not.
    """
    result = training_lib.train(matrix_for(noisy_series(600, seed=99, drift=0.0)))
    ok, reason = training_lib.should_promote(result)
    assert ok is False
    assert "below" in reason or "chance" in reason or "folds" in reason


def test_training_is_deterministic() -> None:
    closes = noisy_series(400, seed=7)
    first = training_lib.train(matrix_for(closes))
    second = training_lib.train(matrix_for(closes))
    assert first.checksum == second.checksum
    assert first.out_of_sample.accuracy == second.out_of_sample.accuracy


def test_walk_forward_produces_multiple_folds() -> None:
    result = training_lib.train(matrix_for(noisy_series(700)))
    assert len(result.walk_forward) >= 2
    for fold in result.walk_forward:
        assert fold["train_rows"] < fold["train_rows"] + fold["test_rows"]


def test_summary_refuses_to_call_noise_an_edge() -> None:
    result = training_lib.train(matrix_for(noisy_series(600, seed=42)))
    verdict = training_lib.summarise(result)
    assert "Out-of-sample accuracy" in verdict
    if (result.out_of_sample.edge_over_baseline or 0) <= 0.02:
        assert "should not be promoted" in verdict


def test_metrics_include_baseline_and_calibration() -> None:
    result = training_lib.train(matrix_for(noisy_series(500)))
    oos = result.out_of_sample
    assert oos.baseline_accuracy is not None
    assert oos.edge_over_baseline is not None
    assert oos.brier is not None and 0 <= oos.brier <= 1
    assert oos.calibration_slope is not None
    assert oos.bins


# ------------------------------------------------------------------- registry


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed_instrument(db, symbol: str = "TST/ML", rows: int = 700, drift: float = 0.0) -> Instrument:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="TEST", name="Test venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="TST",
            quote_currency="ML",
            is_tradable=False,
            status="active",
        )
        db.add(instrument)
        db.flush()

    closes = noisy_series(rows, seed=17, drift=drift)
    base = datetime.now(UTC) - timedelta(days=rows)
    for index, close in enumerate(closes):
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=base + timedelta(days=index),
                close_time=base + timedelta(days=index, hours=23),
                open=close * 0.999,
                high=close * 1.005,
                low=close * 0.995,
                close=close,
                volume=1000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.flush()
    return instrument


def test_seed_models_is_idempotent(db_session) -> None:
    db = db_session
    first = ml_registry.seed_models(db)
    db.commit()
    second = ml_registry.seed_models(db)
    db.commit()
    assert first["created"] == 1
    assert second["created"] == 0


def test_training_creates_a_candidate_never_a_champion(db_session) -> None:
    """The safety rule from the blueprint, asserted directly."""
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db)
    db.commit()

    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/ML")
    db.commit()

    assert version.status == ModelStatus.CANDIDATE
    assert version.promoted_at is None
    kinds = {evaluation.kind for evaluation in version.evaluations}
    assert kinds == {EvaluationKind.TRAIN, EvaluationKind.VALIDATION, EvaluationKind.OUT_OF_SAMPLE}
    out_of_sample = next(
        item for item in version.evaluations if item.kind == EvaluationKind.OUT_OF_SAMPLE
    )
    assert out_of_sample.is_out_of_sample is True


def test_version_stores_reconstructable_provenance(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db)
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/ML")
    db.commit()

    assert version.feature_names
    assert version.checksum and len(version.checksum) == 64
    parameters = json.loads(version.parameters)
    assert {"weights", "bias", "mean", "std"} <= parameters.keys()
    # Feature order is part of the contract.
    assert version.feature_names.split(",") == list(feature_lib.FEATURE_NAMES)


def test_promotion_is_refused_without_an_edge(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/NOEDGE", rows=500)
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/NOEDGE")
    db.commit()

    ok, reason = ml_registry.promotion_decision(db, version)
    assert ok is False
    with pytest.raises(ValueError, match="Promotion refused"):
        ml_registry.promote(db, version_id=version.id, actor_id="admin", note="try")


def test_forced_promotion_is_recorded_as_an_override(db_session) -> None:
    """A human may override, but the audit trail must say so."""
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/FORCE")
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/FORCE")
    db.commit()

    promoted = ml_registry.promote(
        db, version_id=version.id, actor_id="admin", note="manual experiment", force=True
    )
    db.commit()
    assert promoted.status == ModelStatus.CHAMPION
    assert promoted.promoted_by == "admin"
    assert "FORCED OVERRIDE" in promoted.promotion_note


def test_promotion_retires_the_previous_champion_and_rollback_restores_it(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/ROLL")
    db.commit()

    first = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/ROLL")
    ml_registry.promote(db, version_id=first.id, actor_id="admin", note="v1", force=True)
    db.commit()

    second = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/ROLL")
    ml_registry.promote(db, version_id=second.id, actor_id="admin", note="v2", force=True)
    db.commit()

    assert second.replaced_version_id == first.id
    assert db.get(ModelVersion, first.id).status == ModelStatus.RETIRED

    restored = ml_registry.rollback(db, version_id=second.id, actor_id="admin")
    db.commit()
    assert restored.id == first.id
    assert restored.status == ModelStatus.CHAMPION
    assert db.get(ModelVersion, second.id).status == ModelStatus.RETIRED


def test_rollback_without_a_predecessor_is_refused(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/NOROLL")
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/NOROLL")
    db.commit()
    with pytest.raises(ValueError, match="no predecessor"):
        ml_registry.rollback(db, version_id=version.id, actor_id="admin")


def test_a_champion_cannot_be_rejected(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/REJ")
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/REJ")
    ml_registry.promote(db, version_id=version.id, actor_id="admin", note="x", force=True)
    db.commit()
    with pytest.raises(ValueError, match="rolled back"):
        ml_registry.reject(db, version_id=version.id, note="nope")


def test_unknown_model_and_unimplemented_family_are_rejected(db_session) -> None:
    db = db_session
    with pytest.raises(ValueError, match="Unknown model"):
        ml_registry.train_version(db, model_code="nope", symbol="TST/ML")

    db.add(
        Model(
            code="lgbm_v1",
            name="Gradient boost",
            family=ModelFamily.GRADIENT_BOOST,
            task="direction",
        )
    )
    db.commit()
    with pytest.raises(ValueError, match="not implemented"):
        ml_registry.train_version(db, model_code="lgbm_v1", symbol="TST/ML")


def test_training_without_history_fails_loudly(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    db.commit()
    with pytest.raises(ValueError, match="No usable training rows|Unknown instrument"):
        ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="MISSING")


def test_prediction_returns_a_probability(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/PRED")
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/PRED")
    db.commit()

    matrix = ml_registry.build_matrix_for(db, "TST/PRED", "1d")
    probability = ml_registry.predict_probability(db, version_id=version.id, matrix=matrix)
    assert probability is not None
    assert 0.0 <= probability <= 1.0


# ---------------------------------------------------------------------- drift


def test_drift_is_detected_when_the_distribution_moves(db_session) -> None:
    """Train on calm data, then feed a violent regime and expect a flag."""
    db = db_session
    ml_registry.seed_models(db)
    instrument = _seed_instrument(db, symbol="TST/DRIFT", rows=400, drift=0.0)
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/DRIFT")
    db.commit()

    # Append a much more volatile stretch.
    last = (
        db.query(MarketCandle)
        .filter(MarketCandle.instrument_id == instrument.id)
        .order_by(MarketCandle.open_time.desc())
        .first()
    )
    rng = random.Random(5)  # noqa: S311 - synthetic fixtures
    price = float(last.close)
    when = last.open_time
    for _ in range(120):
        price = price * (1 + rng.gauss(0, 0.06))
        when = when + timedelta(days=1)
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=when,
                close_time=when + timedelta(hours=23),
                open=price * 0.98,
                high=price * 1.06,
                low=price * 0.94,
                close=price,
                volume=1000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.commit()

    events = ml_registry.detect_drift(db, version_id=version.id, symbol="TST/DRIFT")
    db.commit()
    assert events, "a volatility shift of this size should be detected"
    assert any(
        event.severity in {DriftSeverity.WARNING, DriftSeverity.CRITICAL} for event in events
    )
    for event in events:
        assert event.threshold is not None
        # Drift is reported as a recommendation, never an automatic action.
        assert "human decision" in event.recommendation
        assert "nothing is changed automatically" in event.recommendation


def test_stable_data_produces_no_drift_events(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/STABLE", rows=500, drift=0.0)
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/STABLE")
    db.commit()
    events = ml_registry.detect_drift(db, version_id=version.id, symbol="TST/STABLE")
    db.commit()
    # Same generator as training, so nothing should have moved materially.
    assert len(events) == 0


def test_drift_requires_a_reference_distribution(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/NOREF")
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/NOREF")
    db.commit()
    # Remove the reference snapshots.
    from app.models.ml import FeatureSnapshot

    db.query(FeatureSnapshot).filter(FeatureSnapshot.version_id == version.id).delete()
    db.commit()
    with pytest.raises(ValueError, match="No reference distribution"):
        ml_registry.detect_drift(db, version_id=version.id, symbol="TST/NOREF")


def test_feature_snapshots_are_stored_per_version(db_session) -> None:
    db = db_session
    ml_registry.seed_models(db)
    _seed_instrument(db, symbol="TST/SNAP")
    db.commit()
    version = ml_registry.train_version(db, model_code="direction_logistic_v1", symbol="TST/SNAP")
    db.commit()

    from app.models.ml import FeatureSnapshot

    snapshots = db.query(FeatureSnapshot).filter(FeatureSnapshot.version_id == version.id).all()
    assert len(snapshots) == len(feature_lib.FEATURE_NAMES)
    for snapshot in snapshots:
        assert snapshot.mean is not None
        assert snapshot.deciles.count(",") == 8
