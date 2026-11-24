"""AI agent models: registry, runs, findings and evidence.

The design constraint from the blueprint is that agents *propose* and never
execute. These tables therefore have no path to an order: a finding can carry a
stance, a confidence and a list of evidence rows, and nothing else. Phase 8 owns
execution, and the risk controller (Phase 9) sits between them.

Two properties are deliberate:

- **Confidence is not a probability.** It is a score in [0, 1] produced by a
  documented rule. Storing `confidence_kind` beside it stops a number from being
  read as a calibrated probability it has never been validated to be.
- **Findings are immutable once written.** A re-run creates a new run. Editing a
  finding would destroy the audit trail that makes the platform reviewable.
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


class AgentRole(str, enum.Enum):
    ORCHESTRATOR = "orchestrator"
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    NEWS = "news"
    SENTIMENT = "sentiment"
    QUANTITATIVE = "quantitative"
    REGIME = "regime"
    PORTFOLIO = "portfolio"
    RISK = "risk"


class AgentStatus(str, enum.Enum):
    ACTIVE = "active"
    SHADOW = "shadow"
    DISABLED = "disabled"


class RunTrigger(str, enum.Enum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    EVENT = "event"


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    REFUSED = "refused"


class Stance(str, enum.Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class ConfidenceKind(str, enum.Enum):
    """What the confidence number actually is.

    Phase 5 numbers are rule-derived scores. Nothing in this table may be read as
    a calibrated probability, and the calibration work lives in Phase 6.
    """

    RULE_SCORE = "rule_score"
    CALIBRATED = "calibrated"
    UNCALIBRATED = "uncalibrated"


class EvidenceKind(str, enum.Enum):
    RESEARCH_CLAIM = "research_claim"
    MARKET_CANDLE = "market_candle"
    INDICATOR = "indicator"
    QUALITY_EVENT = "quality_event"
    COMPUTATION = "computation"
    ABSENCE = "absence"


class Agent(Base, TimestampMixin):
    """A registered specialist.

    `enabled_phases` records which pipeline phases have implemented the agent, so
    the UI can show a real "not yet built" state instead of a fake one.
    """

    __tablename__ = "ai_agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[AgentRole] = mapped_column(
        Enum(AgentRole, native_enum=False, length=24), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[AgentStatus] = mapped_column(
        Enum(AgentStatus, native_enum=False, length=16), default=AgentStatus.ACTIVE, nullable=False
    )
    implemented: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    deterministic: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0", nullable=False)
    inputs: Mapped[str] = mapped_column(Text, default="")
    outputs: Mapped[str] = mapped_column(Text, default="")


class AgentRun(Base, TimestampMixin):
    """One orchestrated pass over an instrument."""

    __tablename__ = "ai_runs"
    __table_args__ = (Index("ix_ai_run_symbol_time", "instrument_symbol", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("trading_accounts.id", ondelete="SET NULL"), nullable=True
    )
    requested_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    instrument_symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(8), default="1d", nullable=False)
    trigger: Mapped[RunTrigger] = mapped_column(
        Enum(RunTrigger, native_enum=False, length=16), default=RunTrigger.MANUAL, nullable=False
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=16), default=RunStatus.PENDING, nullable=False
    )
    question: Mapped[str] = mapped_column(Text, default="")

    # Synthesis output. A proposal is not an order and carries no broker meaning.
    proposal_stance: Mapped[Stance] = mapped_column(
        Enum(Stance, native_enum=False, length=12), default=Stance.UNKNOWN, nullable=False
    )
    proposal_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    proposal_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    proposal_reason: Mapped[str] = mapped_column(Text, default="")
    tradeable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blocked_reason: Mapped[str] = mapped_column(Text, default="")

    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")

    findings: Mapped[list[AgentFinding]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="AgentFinding.ordinal"
    )


class AgentFinding(Base, TimestampMixin):
    """A structured, evidence-backed finding from one specialist."""

    __tablename__ = "ai_findings"
    __table_args__ = (UniqueConstraint("run_id", "agent_code", name="uq_finding_run_agent"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("ai_runs.id", ondelete="CASCADE"), nullable=False
    )
    agent_code: Mapped[str] = mapped_column(String(40), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    headline: Mapped[str] = mapped_column(Text, default="")
    stance: Mapped[Stance] = mapped_column(
        Enum(Stance, native_enum=False, length=12), default=Stance.UNKNOWN, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_kind: Mapped[ConfidenceKind] = mapped_column(
        Enum(ConfidenceKind, native_enum=False, length=20),
        default=ConfidenceKind.RULE_SCORE,
        nullable=False,
    )
    # Free-text blocks that the UI renders separately, never merged.
    facts: Mapped[str] = mapped_column(Text, default="")
    interpretation: Mapped[str] = mapped_column(Text, default="")
    uncertainties: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[str] = mapped_column(Text, default="")
    data_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[AgentRun] = relationship(back_populates="findings")
    evidence: Mapped[list[FindingEvidence]] = relationship(
        back_populates="finding", cascade="all, delete-orphan", order_by="FindingEvidence.ordinal"
    )


class FindingEvidence(Base, TimestampMixin):
    """One pointer from a finding to the thing that supports it.

    `kind` says what is being pointed at. When a finding asserts something about
    missing data, the evidence row is kind=absence with the reason — an explicit
    record that the agent declined, rather than an empty list that could be read
    as "no concerns".
    """

    __tablename__ = "ai_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    finding_id: Mapped[str] = mapped_column(
        ForeignKey("ai_findings.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[EvidenceKind] = mapped_column(
        Enum(EvidenceKind, native_enum=False, length=24), nullable=False
    )
    label: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(20), default="")
    reference_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    finding: Mapped[AgentFinding] = relationship(back_populates="evidence")
