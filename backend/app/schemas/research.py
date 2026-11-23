"""Pydantic contracts for the research API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.research import (
    ClaimVerdict,
    DocumentStatus,
    EntityKind,
    FindingKind,
    SourceKind,
    TrustTier,
)


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    name: str
    publisher: str
    kind: SourceKind
    trust_tier: TrustTier
    homepage: str
    jurisdiction: str
    licence: str
    terms_url: str
    redistribution: str
    storage_permitted: bool
    is_enabled: bool
    last_success_at: datetime | None
    consecutive_failures: int
    last_error: str
    document_count: int = 0


class DocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_code: str = ""
    source_name: str = ""
    source_tier: str = ""
    title: str
    url: str
    summary: str
    published_at: datetime | None
    retrieved_at: datetime
    status: DocumentStatus
    content_stored: bool
    word_count: int
    prompt_injection_flags: int
    injection_detail: str
    untrusted: bool
    chunk_count: int = 0


class ChunkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ordinal: int
    text: str
    page: int | None
    section: str
    char_start: int
    char_end: int
    embedding_model: str


class EntityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: EntityKind
    value: str
    confidence: float


class SearchHit(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float
    source_code: str
    source_name: str
    source_tier: str
    title: str
    url: str
    published_at: datetime | None
    locator: str
    page: int | None
    section: str
    injection_flags: int


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]
    retrieval: str = "hybrid:lexical+hash-256"


class CitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_code: str
    source_name: str
    url: str
    published_at: datetime | None
    quote: str
    locator: str
    relevance: float


class ClaimRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    ordinal: int
    text: str
    verdict: ClaimVerdict
    confidence: float
    evidence_count: int
    note: str
    citations: list[CitationRead] = []


class StatementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: FindingKind
    text: str
    confidence: float


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    topic: str
    instrument_symbol: str | None
    query: str
    generated_by: str
    status: str
    documents_used: int
    claims_total: int
    claims_supported: int
    claims_unverified: int
    contradictions: int
    coverage_score: float
    body_markdown: str
    limitations: str
    created_at: datetime


class ReportDetail(ReportRead):
    claims: list[ClaimRead] = []
    statements: list[StatementRead] = []


class IngestRequest(BaseModel):
    sources: list[str] = Field(default_factory=list, max_length=20)
    limit_per_source: int = Field(default=25, ge=1, le=100)


class IngestSourceResult(BaseModel):
    source: str
    outcome: str
    seen: int
    new: int
    duplicates: int
    rejected: int
    error: str


class IngestResponse(BaseModel):
    sources: int
    documents_new: int
    results: list[IngestSourceResult]


class ReportRequest(BaseModel):
    query: str = Field(min_length=3, max_length=400)
    topic: str = Field(default="", max_length=200)
    instrument_symbol: str | None = Field(default=None, max_length=40)
    days: int = Field(default=90, ge=1, le=730)
    sources: list[str] = Field(default_factory=list, max_length=20)


class ResearchOverview(BaseModel):
    sources_total: int
    sources_enabled: int
    sources_failing: int
    documents_total: int
    documents_untrusted: int
    documents_flagged: int
    chunks_total: int
    reports_total: int
    latest_documents: list[DocumentRead]
    trust_breakdown: dict[str, int]
    top_entities: list[EntityRead]
