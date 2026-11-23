"""Model registry API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ml import DriftSeverity, EvaluationKind, ModelFamily, ModelStatus


class ModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    family: ModelFamily
    task: str
    description: str
    deterministic: bool


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: EvaluationKind
    is_out_of_sample: bool
    rows: int
    accuracy: float | None
    balanced_accuracy: float | None
    precision: float | None
    recall: float | None
    roc_auc: float | None
    brier: float | None
    log_loss: float | None
    baseline_accuracy: float | None
    edge_over_baseline: float | None
    calibration_slope: float | None
    calibration_intercept: float | None
    max_calibration_error: float | None
    bins: str


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    model_id: str
    version: int
    status: ModelStatus
    instrument_symbol: str
    timeframe: str
    feature_names: str
    training_rows: int
    training_start: datetime | None
    training_end: datetime | None
    label_horizon: int
    label_kind: str
    checksum: str
    promoted_at: datetime | None
    promotion_note: str
    replaced_version_id: str | None
    created_at: datetime
    evaluations: list[EvaluationRead] = Field(default_factory=list)


class FeatureSnapshotRead(BaseModel):
    feature: str
    count: int
    mean: float | None
    std: float | None
    minimum: float | None
    maximum: float | None


class ModelVersionDetail(ModelVersionRead):
    feature_snapshots: list[FeatureSnapshotRead] = Field(default_factory=list)
    promotion_gate_passed: bool = False
    promotion_gate_reason: str = ""


class TrainRequest(BaseModel):
    model_code: str = Field(default="direction_logistic_v1", max_length=64)
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(default="1d", pattern="^(1h|4h|1d|1w)$")
    limit: int = Field(default=1200, ge=120, le=5000)
    horizon: int = Field(default=1, ge=1, le=20)


class PromotionRequest(BaseModel):
    note: str = Field(default="", max_length=1000)
    force: bool = False


class DriftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version_id: str
    instrument_symbol: str
    feature: str
    severity: DriftSeverity
    metric: str
    value: float | None
    threshold: float | None
    message: str
    recommendation: str
    detected_at: datetime
    acknowledged: bool


class FeatureDriftRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(default="1d", pattern="^(1h|4h|1d|1w)$")


class ModelOverview(BaseModel):
    models_total: int
    versions_total: int
    candidates: int
    champions: int
    rejected: int
    drift_events_open: int
    champions_detail: list[dict] = Field(default_factory=list)
