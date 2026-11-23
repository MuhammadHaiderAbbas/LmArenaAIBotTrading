"""Research domain: sources, documents, chunks, citations and generated reports.

Design notes
------------
* Retrieved content is **untrusted data, never instructions**. Nothing in a
  document can cause an action; the only thing a document can do is supply text
  that a human reads through a citation. `prompt_injection_flags` records
  attempts so a reviewer can see that they were detected rather than assumed
  absent.
* Every chunk keeps a pointer back to the page/section it came from, because a
  sentence without a location cannot be verified.
* `SourceRecord` stores licence and terms metadata alongside the content. The
  platform must be able to show, per source, what it is allowed to do with it.
* Facts and interpretations are separate tables. `ReportClaim` is a checkable
  statement tied to evidence; `ReportStatement` is model interpretation and is
  labelled as such everywhere it appears.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin
from app.models.identity import _uuid

if TYPE_CHECKING:
    pass


class SourceKind(str, enum.Enum):
    CENTRAL_BANK = "central_bank"
    REGULATOR = "regulator"
    FILING = "filing"
    NEWS = "news"
    STATISTICS = "statistics"
    USER_UPLOAD = "user_upload"


class TrustTier(str, enum.Enum):
    """How much weight a source carries before any analysis happens.

    Tier 1 is a primary source publishing its own decision or disclosure — an
    ECB press release, an SEC filing. Tier 3 is commentary about those facts.
    A report that cites only tier 3 for a factual claim is flagged.
    """

    PRIMARY = "primary"
    OFFICIAL_SECONDARY = "official_secondary"
    COMMENTARY = "commentary"


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    FETCHED = "fetched"
    EXTRACTED = "extracted"
    INDEXED = "indexed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class IngestOutcome(str, enum.Enum):
    INGESTED = "ingested"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    FAILED = "failed"


class ExtractionMethod(str, enum.Enum):
    HTML = "html"
    PDF_TEXT = "pdf_text"
    OCR = "ocr"
    FEED_SUMMARY = "feed_summary"
    PLAIN = "plain"


class EntityKind(str, enum.Enum):
    INSTRUMENT = "instrument"
    CURRENCY = "currency"
    INSTITUTION = "institution"
    PERSON = "person"
    DATE = "date"
    PERIOD = "period"
    METRIC = "metric"


class FindingKind(str, enum.Enum):
    FACT = "fact"
    INTERPRETATION = "interpretation"
    UNCERTAINTY = "uncertainty"
    CONTRADICTION = "contradiction"


class ClaimVerdict(str, enum.Enum):
    SUPPORTED = "supported"
    PARTIAL = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"
    UNVERIFIED = "unverified"


class SourceRecord(Base, TimestampMixin):
    """A feed or publication the platform is allowed to read.

    Licence fields exist because "publicly accessible" is not the same as
    "licensed for redistribution". Storing the terms next to the content means a
    reviewer can audit the claim without leaving the database.
    """

    __tablename__ = "research_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    publisher: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind), nullable=False)
    trust_tier: Mapped[TrustTier] = mapped_column(Enum(TrustTier), nullable=False)

    feed_url: Mapped[str] = mapped_column(String(500), default="")
    homepage: Mapped[str] = mapped_column(String(500), default="")
    jurisdiction: Mapped[str] = mapped_column(String(80), default="")
    language: Mapped[str] = mapped_column(String(10), default="en")

    licence: Mapped[str] = mapped_column(String(200), default="unknown")
    terms_url: Mapped[str] = mapped_column(String(500), default="")
    redistribution: Mapped[str] = mapped_column(
        String(40), default="link_only"
    )  # link_only | excerpt | full
    storage_permitted: Mapped[bool] = mapped_column(Boolean, default=False)

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")

    documents: Mapped[list[ResearchDocument]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class ResearchDocument(Base, TimestampMixin):
    __tablename__ = "research_documents"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_document_source_external"),
        Index("ix_document_published", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"), nullable=False
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str] = mapped_column(String(1000), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1000), default="")
    title: Mapped[str] = mapped_column(String(600), nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="")

    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Accessed but deliberately not stored (terms forbid it), or stored under
    # licence. The distinction is recorded, not assumed.
    content_stored: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    simhash: Mapped[str] = mapped_column(String(32), default="")

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.FETCHED, nullable=False
    )
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        Enum(ExtractionMethod), default=ExtractionMethod.FEED_SUMMARY, nullable=False
    )
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    language: Mapped[str] = mapped_column(String(10), default="en")

    prompt_injection_flags: Mapped[int] = mapped_column(Integer, default=0)
    injection_detail: Mapped[str] = mapped_column(Text, default="")
    untrusted: Mapped[bool] = mapped_column(Boolean, default=True)

    source: Mapped[SourceRecord] = relationship(back_populates="documents")
    chunks: Mapped[list[ResearchChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ResearchChunk(Base, TimestampMixin):
    """A retrievable unit of text with a location a human can check.

    `page` and `section` are nullable but never silently dropped: a chunk without
    a locator is still stored, but the report renderer surfaces it as
    "location unknown" rather than pretending it has a citation.
    """

    __tablename__ = "research_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "ordinal", name="uq_chunk_document_ordinal"),
        Index("ix_chunk_document", "document_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("research_documents.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str] = mapped_column(String(300), default="")
    char_start: Mapped[int] = mapped_column(Integer, default=0)
    char_end: Mapped[int] = mapped_column(Integer, default=0)

    # Deterministic 256-dimension lexical embedding. See services/research/vector.py
    # for why this is not a neural embedding and what that means for retrieval.
    embedding: Mapped[str] = mapped_column(Text, default="")
    embedding_model: Mapped[str] = mapped_column(String(80), default="hash-256")

    document: Mapped[ResearchDocument] = relationship(back_populates="chunks")


class DocumentEntity(Base, TimestampMixin):
    """Entities extracted from a document, used for linking research to markets."""

    __tablename__ = "document_entities"
    __table_args__ = (Index("ix_entity_kind_value", "kind", "value"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("research_documents.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[EntityKind] = mapped_column(Enum(EntityKind), nullable=False)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    normalised: Mapped[str] = mapped_column(String(200), default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_chunks.id", ondelete="SET NULL"), nullable=True
    )


class IngestRun(Base, TimestampMixin):
    __tablename__ = "research_ingest_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_id: Mapped[str] = mapped_column(
        ForeignKey("research_sources.id", ondelete="CASCADE"), nullable=False
    )
    outcome: Mapped[IngestOutcome] = mapped_column(Enum(IngestOutcome), nullable=False)
    documents_seen: Mapped[int] = mapped_column(Integer, default=0)
    documents_new: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    # PDFs are counted separately: a feed can look healthy while every policy
    # document in it failed to extract, and that is worth seeing at a glance.
    pdf_extracted: Mapped[int] = mapped_column(Integer, default=0)
    pdf_failures: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchReport(Base, TimestampMixin):
    """A source-linked report.

    `generated_by` names the assembler. In Phase 4 this is deterministic
    extraction (`extractive-v1`), not an LLM: there is no model provider wired
    in yet, and inventing prose from retrieved text without a verifier is how
    research assistants end up confidently wrong.
    """

    __tablename__ = "research_reports"
    __table_args__ = (Index("ix_report_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(400), nullable=False)
    topic: Mapped[str] = mapped_column(String(200), nullable=False)
    instrument_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    query: Mapped[str] = mapped_column(Text, default="")
    generated_by: Mapped[str] = mapped_column(String(80), default="extractive-v1")
    status: Mapped[str] = mapped_column(String(30), default="draft")

    documents_used: Mapped[int] = mapped_column(Integer, default=0)
    claims_total: Mapped[int] = mapped_column(Integer, default=0)
    claims_supported: Mapped[int] = mapped_column(Integer, default=0)
    claims_unverified: Mapped[int] = mapped_column(Integer, default=0)
    contradictions: Mapped[int] = mapped_column(Integer, default=0)
    coverage_score: Mapped[float] = mapped_column(Float, default=0.0)

    body_markdown: Mapped[str] = mapped_column(Text, default="")
    limitations: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    claims: Mapped[list[ReportClaim]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    statements: Mapped[list[ReportStatement]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    citations: Mapped[list[ReportCitation]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


class ReportClaim(Base, TimestampMixin):
    """A checkable statement tied to the evidence that supports it."""

    __tablename__ = "report_claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    verdict: Mapped[ClaimVerdict] = mapped_column(
        Enum(ClaimVerdict), default=ClaimVerdict.UNVERIFIED, nullable=False
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")

    report: Mapped[ResearchReport] = relationship(back_populates="claims")
    citations: Mapped[list[ReportCitation]] = relationship(
        back_populates="claim", cascade="all, delete-orphan"
    )


class ReportStatement(Base, TimestampMixin):
    """Model interpretation. Never presented as fact."""

    __tablename__ = "report_statements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[FindingKind] = mapped_column(Enum(FindingKind), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    report: Mapped[ResearchReport] = relationship(back_populates="statements")


class ReportCitation(Base, TimestampMixin):
    """Pointer from a claim back to the exact span of text that supports it."""

    __tablename__ = "report_citations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str | None] = mapped_column(
        ForeignKey("report_claims.id", ondelete="CASCADE"), nullable=True
    )
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("research_chunks.id", ondelete="SET NULL"), nullable=True
    )
    document_id: Mapped[str] = mapped_column(String(36), default="")
    source_code: Mapped[str] = mapped_column(String(64), default="")
    source_name: Mapped[str] = mapped_column(String(200), default="")
    url: Mapped[str] = mapped_column(String(1000), default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    quote: Mapped[str] = mapped_column(Text, default="")
    locator: Mapped[str] = mapped_column(String(300), default="")
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    retrieval_score: Mapped[float] = mapped_column(Numeric(10, 6), default=0)

    report: Mapped[ResearchReport] = relationship(back_populates="citations")
    claim: Mapped[ReportClaim | None] = relationship(back_populates="citations")
