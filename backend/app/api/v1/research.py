"""Research API: sources, documents, retrieval and source-linked reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core.rate_limit import enforce_rate_limit
from app.models.research import (
    DocumentEntity,
    ResearchChunk,
    ResearchDocument,
    ResearchReport,
    SourceRecord,
)
from app.schemas.research import (
    ChunkRead,
    CitationRead,
    ClaimRead,
    DocumentRead,
    EntityRead,
    IngestRequest,
    IngestResponse,
    ReportDetail,
    ReportRead,
    ReportRequest,
    ResearchOverview,
    SearchHit,
    SearchResponse,
    SourceRead,
    StatementRead,
)
from app.services.research import extraction, ingestion, registry, reports, retrieval

router = APIRouter(prefix="/research", tags=["research"])


def _source_read(db: Session, source: SourceRecord) -> SourceRead:
    data = SourceRead.model_validate(source)
    data.document_count = int(
        db.scalar(
            select(func.count(ResearchDocument.id)).where(ResearchDocument.source_id == source.id)
        )
        or 0
    )
    return data


def _document_read(db: Session, document: ResearchDocument, source: SourceRecord) -> DocumentRead:
    data = DocumentRead.model_validate(document)
    data.source_code = source.code
    data.source_name = source.name
    data.source_tier = source.trust_tier.value
    data.chunk_count = int(
        db.scalar(
            select(func.count(ResearchChunk.id)).where(ResearchChunk.document_id == document.id)
        )
        or 0
    )
    data.body_available_on_demand = bool(not document.content_stored and document.url)
    return data


# --------------------------------------------------------------------- sources


@router.get("/sources", response_model=list[SourceRead])
def list_sources(db: DbSession, user: CurrentUser) -> list[SourceRead]:
    sources = list(
        db.scalars(select(SourceRecord).order_by(SourceRecord.trust_tier, SourceRecord.code))
    )
    if not sources:
        registry.seed_sources(db)
        db.commit()
        sources = list(
            db.scalars(select(SourceRecord).order_by(SourceRecord.trust_tier, SourceRecord.code))
        )
    return [_source_read(db, source) for source in sources]


@router.get("/overview", response_model=ResearchOverview)
def overview(db: DbSession, user: CurrentUser) -> ResearchOverview:
    sources = list(db.scalars(select(SourceRecord)))
    if not sources:
        registry.seed_sources(db)
        db.commit()
        sources = list(db.scalars(select(SourceRecord)))

    documents_total = int(db.scalar(select(func.count(ResearchDocument.id))) or 0)
    flagged = int(
        db.scalar(
            select(func.count(ResearchDocument.id)).where(
                ResearchDocument.prompt_injection_flags > 0
            )
        )
        or 0
    )
    trust_breakdown: dict[str, int] = {}
    for source in sources:
        tier = source.trust_tier.value
        count = int(
            db.scalar(
                select(func.count(ResearchDocument.id)).where(
                    ResearchDocument.source_id == source.id
                )
            )
            or 0
        )
        trust_breakdown[tier] = trust_breakdown.get(tier, 0) + count

    recent = list(
        db.execute(
            select(ResearchDocument, SourceRecord)
            .join(SourceRecord, ResearchDocument.source_id == SourceRecord.id)
            .order_by(ResearchDocument.retrieved_at.desc())
            .limit(12)
        )
    )

    top = list(
        db.execute(
            select(
                DocumentEntity.kind, DocumentEntity.value, func.count(DocumentEntity.id).label("n")
            )
            .group_by(DocumentEntity.kind, DocumentEntity.value)
            .order_by(func.count(DocumentEntity.id).desc())
            .limit(10)
        )
    )

    return ResearchOverview(
        sources_total=len(sources),
        sources_enabled=sum(1 for source in sources if source.is_enabled),
        sources_failing=sum(1 for source in sources if source.consecutive_failures > 0),
        documents_total=documents_total,
        documents_untrusted=documents_total,
        documents_flagged=flagged,
        chunks_total=int(db.scalar(select(func.count(ResearchChunk.id))) or 0),
        reports_total=int(db.scalar(select(func.count(ResearchReport.id))) or 0),
        latest_documents=[_document_read(db, document, source) for document, source in recent],
        trust_breakdown=trust_breakdown,
        top_entities=[
            EntityRead(kind=kind, value=value, confidence=1.0) for kind, value, _count in top
        ],
    )


# ------------------------------------------------------------------- documents


@router.get("/documents", response_model=list[DocumentRead])
def list_documents(
    db: DbSession,
    user: CurrentUser,
    source: str | None = None,
    instrument: str | None = None,
    flagged_only: bool = False,
    limit: int = Query(default=40, ge=1, le=200),
) -> list[DocumentRead]:
    statement = (
        select(ResearchDocument, SourceRecord)
        .join(SourceRecord, ResearchDocument.source_id == SourceRecord.id)
        .order_by(ResearchDocument.retrieved_at.desc())
    )
    if source:
        statement = statement.where(SourceRecord.code == source)
    if flagged_only:
        statement = statement.where(ResearchDocument.prompt_injection_flags > 0)

    rows = list(db.execute(statement.limit(limit * 4 if instrument else limit)))
    results: list[DocumentRead] = []
    for document, source_record in rows:
        if instrument:
            match = db.scalar(
                select(DocumentEntity.id).where(
                    DocumentEntity.document_id == document.id,
                    DocumentEntity.kind == "instrument",
                    DocumentEntity.value == instrument.upper(),
                )
            )
            if match is None:
                continue
        results.append(_document_read(db, document, source_record))
        if len(results) >= limit:
            break
    return results


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document(document_id: str, db: DbSession, user: CurrentUser) -> DocumentRead:
    row = db.execute(
        select(ResearchDocument, SourceRecord)
        .join(SourceRecord, ResearchDocument.source_id == SourceRecord.id)
        .where(ResearchDocument.id == document_id)
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return _document_read(db, row[0], row[1])


@router.get("/documents/{document_id}/chunks", response_model=list[ChunkRead])
def document_chunks(
    document_id: str,
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[ChunkRead]:
    exists = db.get(ResearchDocument, document_id)
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    chunks = db.scalars(
        select(ResearchChunk)
        .where(ResearchChunk.document_id == document_id)
        .order_by(ResearchChunk.ordinal)
        .limit(limit)
    )
    return [ChunkRead.model_validate(chunk) for chunk in chunks]


@router.get("/documents/{document_id}/entities", response_model=list[EntityRead])
def document_entities(document_id: str, db: DbSession, user: CurrentUser) -> list[EntityRead]:
    entities = db.scalars(select(DocumentEntity).where(DocumentEntity.document_id == document_id))
    return [EntityRead.model_validate(entity) for entity in entities]


# ------------------------------------------------------------------- retrieval


@router.get("/search", response_model=SearchResponse)
def search(
    db: DbSession,
    user: CurrentUser,
    q: str = Query(min_length=2, max_length=300),
    source: str | None = None,
    instrument: str | None = None,
    days: int | None = Query(default=None, ge=1, le=730),
    limit: int = Query(default=12, ge=1, le=50),
) -> SearchResponse:
    filters = retrieval.SearchFilters(
        source_codes=[source] if source else None,
        since=datetime.now(UTC) - timedelta(days=days) if days else None,
        instruments=[instrument] if instrument else None,
        limit=limit,
    )
    hits = retrieval.search(db, q, filters)
    return SearchResponse(
        query=q,
        hits=[
            SearchHit(
                chunk_id=hit.chunk_id,
                document_id=hit.document_id,
                text=hit.text,
                score=hit.score,
                source_code=hit.source_code,
                source_name=hit.source_name,
                source_tier=hit.source_tier,
                title=hit.title,
                url=hit.url,
                published_at=hit.published_at,
                locator=hit.locator,
                page=hit.page,
                section=hit.section,
                injection_flags=hit.injection_flags,
            )
            for hit in hits
        ],
    )


# --------------------------------------------------------------------- reports


def _report_detail(db: Session, report: ResearchReport) -> ReportDetail:
    detail = ReportDetail.model_validate(report)
    detail.claims = [
        ClaimRead(
            **{
                "id": claim.id,
                "ordinal": claim.ordinal,
                "text": claim.text,
                "verdict": claim.verdict,
                "confidence": claim.confidence,
                "evidence_count": claim.evidence_count,
                "note": claim.note,
                "citations": [
                    CitationRead.model_validate(citation) for citation in claim.citations
                ],
            }
        )
        for claim in sorted(report.claims, key=lambda item: item.ordinal)
    ]
    detail.statements = [StatementRead.model_validate(item) for item in report.statements]
    return detail


@router.get("/reports", response_model=list[ReportRead])
def list_reports(
    db: DbSession,
    user: CurrentUser,
    limit: int = Query(default=25, ge=1, le=100),
) -> list[ReportRead]:
    rows = db.scalars(
        select(ResearchReport).order_by(ResearchReport.created_at.desc()).limit(limit)
    )
    return [ReportRead.model_validate(report) for report in rows]


@router.get("/reports/{report_id}", response_model=ReportDetail)
def get_report(report_id: str, db: DbSession, user: CurrentUser) -> ReportDetail:
    report = db.get(ResearchReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    return _report_detail(db, report)


@router.post("/reports", response_model=ReportDetail, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: ReportRequest,
    db: DbSession,
    user: CurrentUser,
) -> ReportDetail:
    report = reports.build_report(
        db,
        query=payload.query,
        topic=payload.topic or payload.query,
        instrument_symbol=payload.instrument_symbol,
        since=datetime.now(UTC) - timedelta(days=payload.days),
        source_codes=payload.sources or None,
        created_by_id=user.id,
    )
    db.commit()
    db.refresh(report)
    return _report_detail(db, report)


# -------------------------------------------------------------------- ingestion


@router.post("/ingest", response_model=IngestResponse)
def ingest(
    payload: IngestRequest,
    request: Request,
    db: DbSession,
    user: StaffUser,
) -> IngestResponse:
    enforce_rate_limit(request, "research_ingest", limit=6, window_seconds=60)
    summary = ingestion.ingest_all(
        db, limit_per_source=payload.limit_per_source, source_codes=payload.sources or None
    )
    return IngestResponse(**summary)  # type: ignore[arg-type]


@router.post("/sources/seed")
def seed_sources(db: DbSession, user: StaffUser) -> dict[str, int]:
    result = registry.seed_sources(db)
    db.commit()
    return result


# ------------------------------------------------------------------ utilities


@router.get("/inspection/scan")
def scan_text(
    user: CurrentUser, text: str = Query(min_length=1, max_length=8000)
) -> dict[str, object]:
    """Expose the sanitiser so an operator can see what it detects.

    Useful when a document is quarantined for review: the reviewer can paste the
    passage and see exactly which patterns fired, instead of trusting a boolean.
    """
    sanitised = extraction.sanitise(text)
    return {
        "flags": sanitised.flags,
        "removed_hidden_characters": sanitised.removed_hidden,
        "cleaned_preview": sanitised.text[:400],
        "patterns": [name for name, _ in extraction.INJECTION_PATTERNS],
    }
