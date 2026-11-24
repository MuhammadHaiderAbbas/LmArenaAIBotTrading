"""Model registry, training, evaluation, promotion and drift endpoints.

Promotion is staff-only and audited. Training is open to any authenticated user
because a candidate is harmless: it cannot be reached by any trading path, and
`status: candidate` is not a state the AI desk will consume.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core.rate_limit import enforce_rate_limit
from app.models.ml import (
    DriftEvent,
    EvaluationKind,
    FeatureSnapshot,
    Model,
    ModelEvaluation,
    ModelStatus,
    ModelVersion,
)
from app.schemas.ml import (
    DriftRead,
    EvaluationRead,
    FeatureDriftRequest,
    ModelOverview,
    ModelRead,
    ModelVersionDetail,
    ModelVersionRead,
    PromotionRequest,
    TrainRequest,
)
from app.services.ml import registry as ml_registry
from app.services.ml import training as training_lib

router = APIRouter(prefix="/models", tags=["models"])


def _version_read(
    version: ModelVersion, evaluations: list[ModelEvaluation] | None = None
) -> ModelVersionRead:
    payload = ModelVersionRead.model_validate(version)
    if evaluations is not None:
        payload.evaluations = [EvaluationRead.model_validate(item) for item in evaluations]
    return payload


def _evaluations(db: Session, version_id: str) -> list[ModelEvaluation]:
    return list(
        db.scalars(
            select(ModelEvaluation)
            .where(ModelEvaluation.version_id == version_id)
            .order_by(ModelEvaluation.created_at)
        )
    )


@router.get("", response_model=list[ModelRead])
def list_models(db: DbSession, user: CurrentUser) -> list[ModelRead]:
    models = list(db.scalars(select(Model).order_by(Model.code)))
    if not models:
        ml_registry.seed_models(db)
        db.commit()
        models = list(db.scalars(select(Model).order_by(Model.code)))
    return [ModelRead.model_validate(model) for model in models]


@router.get("/overview", response_model=ModelOverview)
def overview(db: DbSession, user: CurrentUser) -> ModelOverview:
    if db.scalar(select(Model)) is None:
        ml_registry.seed_models(db)
        db.commit()
    versions = list(db.scalars(select(ModelVersion)))
    champions = [version for version in versions if version.status == ModelStatus.CHAMPION]
    champions_evaluated: list[dict] = []
    for version in champions:
        oos = db.scalar(
            select(ModelEvaluation).where(
                ModelEvaluation.version_id == version.id,
                ModelEvaluation.kind == EvaluationKind.OUT_OF_SAMPLE,
            )
        )
        champions_evaluated.append(
            {
                "model_id": version.model_id,
                "version": version.version,
                "symbol": version.instrument_symbol,
                "out_of_sample_accuracy": oos.accuracy if oos else None,
                "edge_over_baseline": oos.edge_over_baseline if oos else None,
                "promoted_at": version.promoted_at,
            }
        )
    return ModelOverview(
        models_total=len(list(db.scalars(select(Model)))),
        versions_total=len(versions),
        candidates=sum(1 for version in versions if version.status == ModelStatus.CANDIDATE),
        champions=len(champions),
        rejected=sum(1 for version in versions if version.status == ModelStatus.REJECTED),
        drift_events_open=len(
            list(db.scalars(select(DriftEvent).where(DriftEvent.acknowledged.is_(False))))
        ),
        champions_detail=champions_evaluated,
    )


@router.get("/versions", response_model=list[ModelVersionRead])
def list_versions(
    db: DbSession,
    user: CurrentUser,
    model: str | None = None,
    symbol: str | None = None,
    limit: int = Query(default=40, ge=1, le=200),
) -> list[ModelVersionRead]:
    statement = select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(limit)
    if model:
        parent = db.scalar(select(Model).where(Model.code == model))
        if parent is None:
            raise HTTPException(status_code=404, detail="Model not found")
        statement = statement.where(ModelVersion.model_id == parent.id)
    if symbol:
        statement = statement.where(ModelVersion.instrument_symbol == symbol)
    versions = list(db.scalars(statement))
    return [_version_read(version, _evaluations(db, version.id)) for version in versions]


@router.get("/versions/{version_id}", response_model=ModelVersionDetail)
def get_version(version_id: str, db: DbSession, user: CurrentUser) -> ModelVersionDetail:
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version not found")
    detail = ModelVersionDetail.model_validate(version)
    detail.evaluations = [
        EvaluationRead.model_validate(item) for item in _evaluations(db, version_id)
    ]
    detail.feature_snapshots = [
        {
            "feature": snapshot.feature,
            "count": snapshot.count,
            "mean": snapshot.mean,
            "std": snapshot.std,
            "minimum": snapshot.minimum,
            "maximum": snapshot.maximum,
        }
        for snapshot in db.scalars(
            select(FeatureSnapshot).where(FeatureSnapshot.version_id == version.id)
        )
    ]
    ok, reason = ml_registry.promotion_decision(db, version)
    detail.promotion_gate_passed = ok
    detail.promotion_gate_reason = reason
    return detail


@router.post("/train", response_model=ModelVersionDetail, status_code=status.HTTP_201_CREATED)
def train(
    payload: TrainRequest, request: Request, db: DbSession, user: CurrentUser
) -> ModelVersionDetail:
    """Train a candidate. This endpoint cannot promote anything."""
    enforce_rate_limit(request, "ml_train", limit=6, window_seconds=60)
    try:
        version = ml_registry.train_version(
            db,
            model_code=payload.model_code,
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            limit=payload.limit,
            horizon=payload.horizon,
            created_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return get_version(version.id, db, user)


@router.post("/versions/{version_id}/promote", response_model=ModelVersionRead)
def promote(
    version_id: str, payload: PromotionRequest, db: DbSession, user: StaffUser
) -> ModelVersionRead:
    """Promote a version to champion. Staff only, audited, reversible."""
    try:
        version = ml_registry.promote(
            db, version_id=version_id, actor_id=user.id, note=payload.note, force=payload.force
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return _version_read(version, _evaluations(db, version.id))


@router.post("/versions/{version_id}/rollback", response_model=ModelVersionRead)
def rollback(version_id: str, db: DbSession, user: StaffUser) -> ModelVersionRead:
    try:
        version = ml_registry.rollback(db, version_id=version_id, actor_id=user.id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return _version_read(version, _evaluations(db, version.id))


@router.post("/versions/{version_id}/reject", response_model=ModelVersionRead)
def reject(version_id: str, db: DbSession, user: StaffUser, note: str = "") -> ModelVersionRead:
    try:
        version = ml_registry.reject(db, version_id=version_id, note=note)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return _version_read(version, _evaluations(db, version.id))


@router.post("/versions/{version_id}/drift", response_model=list[DriftRead])
def check_drift(
    version_id: str, payload: FeatureDriftRequest, db: DbSession, user: CurrentUser
) -> list[DriftRead]:
    try:
        events = ml_registry.detect_drift(
            db, version_id=version_id, symbol=payload.symbol, timeframe=payload.timeframe
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    db.commit()
    return [DriftRead.model_validate(event) for event in events]


@router.get("/drift", response_model=list[DriftRead])
def list_drift(
    db: DbSession,
    user: CurrentUser,
    open_only: bool = True,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[DriftRead]:
    statement = select(DriftEvent).order_by(DriftEvent.created_at.desc()).limit(limit)
    if open_only:
        statement = statement.where(DriftEvent.acknowledged.is_(False))
    return [DriftRead.model_validate(event) for event in db.scalars(statement)]


@router.post("/drift/{event_id}/acknowledge", response_model=DriftRead)
def acknowledge_drift(event_id: str, db: DbSession, user: StaffUser) -> DriftRead:
    event = db.get(DriftEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Drift event not found")
    event.acknowledged = True
    db.commit()
    return DriftRead.model_validate(event)


@router.get("/versions/{version_id}/summary", response_model=dict)
def version_summary(version_id: str, db: DbSession, user: CurrentUser) -> dict:
    """The one-line verdict a reviewer reads before trusting a version."""
    version = db.get(ModelVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    evaluations = _evaluations(db, version_id)
    oos = next((item for item in evaluations if item.kind == EvaluationKind.OUT_OF_SAMPLE), None)
    hyper = version.hyperparameters or "{}"
    import json

    folds = json.loads(hyper).get("walk_forward", [])
    result = training_lib.TrainingResult(
        parameters={},
        feature_names=(version.feature_names or "").split(","),
        train=training_lib.Metrics(rows=0),
        validation=training_lib.Metrics(rows=0),
        out_of_sample=training_lib.Metrics(
            rows=oos.rows if oos else 0,
            accuracy=oos.accuracy if oos else None,
            edge_over_baseline=oos.edge_over_baseline if oos else None,
            baseline_accuracy=oos.baseline_accuracy if oos else None,
            roc_auc=oos.roc_auc if oos else None,
            calibration_slope=oos.calibration_slope if oos else None,
        ),
        walk_forward=folds,
        training_rows=version.training_rows,
        checksum=version.checksum,
        config=training_lib.TrainingConfig(),
    )
    return {
        "version": version.version,
        "status": version.status.value,
        "verdict": training_lib.summarise(result),
        "walk_forward": folds,
    }
