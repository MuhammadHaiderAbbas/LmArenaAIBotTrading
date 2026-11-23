"""Model registry, versions, evaluations and drift events.

The blueprint's rule for this domain is blunt:

> The system may propose model changes, but must not silently rewrite production
> trading rules or automatically promote unvalidated models.

That constraint is encoded here rather than in a comment:

- A `ModelVersion` cannot reach `promoted` through the training path. Training
  produces `candidate`; promotion is a separate, recorded act with an approver.
- `evaluations` hold out-of-sample metrics, never in-sample ones as if they were
  the same thing. `is_out_of_sample` is a column because confusing the two is the
  single most common way a research pipeline lies to itself.
- Every promotion is reversible: the version it replaced is stored, so rollback is
  a lookup rather than an archaeology exercise.
- Drift events are recorded per feature, so "the model is degrading" can be
  traced to *which input moved*.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid.uuid4())


class ModelFamily(str, enum.Enum):
    """Families named by the blueprint. Only two are implemented in Phase 6."""

    LOGISTIC = "logistic"
    GRADIENT_BOOST = "gradient_boost"
    RANDOM_FOREST = "random_forest"
    BASELINE = "baseline"


class ModelStatus(str, enum.Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    CHAMPION = "champion"
    RETIRED = "retired"
    REJECTED = "rejected"


class EvaluationKind(str, enum.Enum):
    TRAIN = "train"
    VALIDATION = "validation"
    OUT_OF_SAMPLE = "out_of_sample"
    WALK_FORWARD = "walk_forward"


class DriftSeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Model(Base, TimestampMixin):
    __tablename__ = "model_registry"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    family: Mapped[ModelFamily] = mapped_column(
        Enum(ModelFamily, native_enum=False, length=24), nullable=False
    )
    task: Mapped[str] = mapped_column(String(40), default="direction", nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    deterministic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    versions: Mapped[list[ModelVersion]] = relationship(
        back_populates="model", cascade="all, delete-orphan"
    )


class ModelVersion(Base, TimestampMixin):
    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_model_version"),
        Index("ix_version_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_id: Mapped[str] = mapped_column(
        ForeignKey("model_registry.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ModelStatus] = mapped_column(
        Enum(ModelStatus, native_enum=False, length=16), default=ModelStatus.DRAFT, nullable=False
    )

    # Provenance: what data, which features, which split, which seed. A model you
    # cannot reconstruct is an anecdote.
    instrument_symbol: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), default="1d", nullable=False)
    feature_names: Mapped[str] = mapped_column(Text, default="")
    training_rows: Mapped[int] = mapped_column(Integer, default=0)
    training_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    training_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hyperparameters: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int] = mapped_column(Integer, default=7)
    label_horizon: Mapped[int] = mapped_column(Integer, default=1)
    label_kind: Mapped[str] = mapped_column(String(40), default="direction")

    # Serialised model parameters. JSON text, not a pickle: a trained artefact is
    # not code, and storing it as a pickle invites executing arbitrary objects.
    parameters: Mapped[str] = mapped_column(Text, default="")
    checksum: Mapped[str] = mapped_column(String(64), default="")

    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    promotion_note: Mapped[str] = mapped_column(Text, default="")
    replaced_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model: Mapped[Model] = relationship(back_populates="versions")
    evaluations: Mapped[list[ModelEvaluation]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class ModelEvaluation(Base, TimestampMixin):
    """Metrics for one evaluation kind.

    `is_out_of_sample` is load-bearing: a 0.95 train score beside a 0.51 OOS score
    is a model that has learned the past and nothing else, and the two numbers
    must never be presented as if they were comparable.
    """

    __tablename__ = "model_evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[EvaluationKind] = mapped_column(
        Enum(EvaluationKind, native_enum=False, length=20), nullable=False
    )
    is_out_of_sample: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    rows: Mapped[int] = mapped_column(Integer, default=0)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    balanced_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    roc_auc: Mapped[float | None] = mapped_column(Float, nullable=True)
    brier: Mapped[float | None] = mapped_column(Float, nullable=True)
    log_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_over_baseline: Mapped[float | None] = mapped_column(Float, nullable=True)

    calibration_slope: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_intercept: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_calibration_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    bins: Mapped[str] = mapped_column(Text, default="")

    notes: Mapped[str] = mapped_column(Text, default="")

    version: Mapped[ModelVersion] = relationship(back_populates="evaluations")


class FeatureSnapshot(Base, TimestampMixin):
    """Distribution summary per feature per dataset slice.

    Drift is a property of the inputs; storing the reference distribution at
    training time is what makes "this feature has moved" checkable later.
    """

    __tablename__ = "feature_snapshots"
    __table_args__ = (UniqueConstraint("version_id", "feature", name="uq_snapshot_feature"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(
        ForeignKey("model_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0)
    mean: Mapped[float | None] = mapped_column(Float, nullable=True)
    std: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Deciles, comma separated. A mean and std hide bimodal shifts.
    deciles: Mapped[str] = mapped_column(Text, default="")


class DriftEvent(Base, TimestampMixin):
    __tablename__ = "drift_events"
    __table_args__ = (Index("ix_drift_version_time", "version_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    version_id: Mapped[str] = mapped_column(String(36), nullable=False)
    instrument_symbol: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    feature: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    severity: Mapped[DriftSeverity] = mapped_column(
        Enum(DriftSeverity, native_enum=False, length=16), nullable=False
    )
    metric: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # State the detection exactly as a recommendation, never an automatic action.
    recommendation: Mapped[str] = mapped_column(Text, default="")
