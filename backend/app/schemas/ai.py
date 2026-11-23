"""AI orchestration API contracts."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.ai import AgentStatus, ConfidenceKind, EvidenceKind, RunStatus, RunTrigger, Stance


class AgentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    role: str
    description: str
    status: AgentStatus
    implemented: bool
    deterministic: bool
    version: str
    inputs: str
    outputs: str


class EvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: EvidenceKind
    label: str
    detail: str
    value: float | None
    unit: str
    reference_id: str | None
    url: str
    observed_at: datetime | None


class FindingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    agent_code: str
    ordinal: int
    headline: str
    stance: Stance
    confidence: float
    confidence_kind: ConfidenceKind
    facts: str
    interpretation: str
    uncertainties: str
    limitations: str
    data_available: bool
    duration_ms: int
    evidence: list[EvidenceRead] = Field(default_factory=list)


class RunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    instrument_symbol: str
    timeframe: str
    trigger: RunTrigger
    status: RunStatus
    question: str
    proposal_stance: Stance
    proposal_confidence: float | None
    proposal_conflict: bool
    proposal_reason: str
    tradeable: bool
    blocked_reason: str
    duration_ms: int
    started_at: datetime | None
    finished_at: datetime | None
    error: str
    created_at: datetime


class RunDetail(RunRead):
    findings: list[FindingRead] = Field(default_factory=list)


class RunRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40)
    timeframe: str = Field(default="1d", pattern="^(1m|5m|15m|1h|4h|1d|1w)$")
    account_id: str | None = None
    question: str = Field(default="", max_length=1000)


class AIOverview(BaseModel):
    runs_total: int
    runs_completed: int
    runs_refused: int
    runs_partial: int
    agents_implemented: int
    agents_total: int
    tradeable_proposals: int
    latest_runs: list[RunRead] = Field(default_factory=list)
    stance_breakdown: dict[str, int] = Field(default_factory=dict)
