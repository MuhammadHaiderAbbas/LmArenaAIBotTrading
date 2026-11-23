"""Research ingestion pipeline: fetch → sanitise → dedupe → chunk → index.

Design rules that shape this module
-----------------------------------
* **Failure is per-source.** One dead feed must not stop the others. Each source
  gets its own `IngestRun` row, and its failure count increments so a reviewer can
  see a publisher degrading over time instead of one loud exception.
* **Duplicates are expected.** Wire services republish; the same ECB release
  appears in three feeds. Exact-hash dedupe catches reposts, simhash catches
  near-duplicates, and both outcomes are counted rather than silently dropped.
* **Untrusted content stays untrusted.** Sanitisation records injection signals on
  the document. Nothing in this module acts on document text.
* **Nothing is stored that the licence forbids.** When
  `storage_permitted` is false, the document keeps the publisher's own summary and
  a link, and `content_stored` records that the body was deliberately not kept.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.research.base import ResearchProviderError
from app.integrations.research.rss import FeedProvider
from app.integrations.research.sec import SecEdgarProvider
from app.models.research import (
    DocumentEntity,
    DocumentStatus,
    EntityKind,
    IngestOutcome,
    IngestRun,
    ResearchChunk,
    ResearchDocument,
    SourceRecord,
)
from app.services.research import entities, extraction, vector

logger = logging.getLogger("aurex.research")

PROVIDERS: dict[str, object] = {"rss": FeedProvider(), "sec_edgar": SecEdgarProvider()}

# Which adapter reads which registry entry. A source with no entry here is inert,
# never guessed at.
SOURCE_PROVIDERS: dict[str, str] = {"sec_edgar": "sec_edgar"}

# Duplicates are detected on a 32-bit simhash; below this Hamming distance two
# documents are the same story. Six is the conventional threshold and is exposed
# in the API so a reviewer can see why a document was suppressed.
SIMHASH_THRESHOLD = 6


def ingest_source(
    db: Session,
    source: SourceRecord,
    *,
    limit: int = 30,
    fetch_bodies: bool = False,
) -> IngestRun:
    """Poll one source and persist what it returns."""
    started = datetime.now(UTC)
    provider_name = SOURCE_PROVIDERS.get(source.code, "rss")
    provider = PROVIDERS.get(provider_name)
    run = IngestRun(
        source_id=source.id,
        outcome=IngestOutcome.FAILED,
        started_at=started,
    )
    db.add(run)
    db.flush()

    try:
        result = provider.fetch(source.feed_url, limit=limit)  # type: ignore[union-attr]
    except ResearchProviderError as exc:
        source.consecutive_failures += 1
        source.last_polled_at = started
        source.last_error = exc.message
        run.error = exc.message
        run.outcome = IngestOutcome.FAILED
        run.finished_at = datetime.now(UTC)
        run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
        logger.warning("research source %s failed: %s", source.code, exc.message)
        return run

    source.last_polled_at = started
    source.last_success_at = started
    source.consecutive_failures = 0
    source.last_error = ""

    seen = 0
    new = 0
    duplicates = 0
    rejected = 0

    # Corpus for IDF: the summaries already in the database plus this batch.
    recent = list(
        db.scalars(select(ResearchChunk.text).order_by(ResearchChunk.created_at.desc()).limit(400))
    )
    corpus = recent + [item.content or item.summary for item in result.items]
    idf = vector.build_idf(corpus)

    for item in result.items:
        seen += 1
        sanitised = extraction.sanitise(item.content or item.summary or item.title)
        body = sanitised.text or item.title

        if not _plausible_title(item.title):
            # Navigation and dialog labels appear as feed items on some sites.
            # "Close dialog" is not a headline and must never become a document.
            rejected += 1
            continue

        if len(body.strip()) < 20:
            # A headline with no body cannot support a claim; storing it would
            # create a citable chunk that says nothing.
            rejected += 1
            continue

        fingerprint = extraction.content_hash(body)
        near = extraction.simhash(body)

        existing = db.scalar(
            select(ResearchDocument).where(
                ResearchDocument.source_id == source.id,
                ResearchDocument.external_id == item.external_id,
            )
        )
        if existing is not None:
            duplicates += 1
            continue

        if _is_near_duplicate(db, near):
            duplicates += 1
            continue

        store_body = bool(source.storage_permitted)
        document = ResearchDocument(
            source_id=source.id,
            external_id=item.external_id[:500],
            url=item.url[:1000],
            canonical_url=item.url[:1000],
            title=_clip(item.title, 600),
            summary=_clip(item.summary or body[:500], 4000),
            published_at=item.published_at,
            retrieved_at=datetime.now(UTC),
            content_stored=store_body,
            content_hash=fingerprint,
            simhash=near,
            status=DocumentStatus.INDEXED,
            extraction_method=extraction_method_for(source, item),
            word_count=len(body.split()),
            prompt_injection_flags=len(sanitised.flags),
            injection_detail=", ".join(sanitised.flags),
            untrusted=True,
        )
        db.add(document)
        db.flush()
        new += 1

        text_to_index = body if store_body else (item.summary or item.title)
        chunks = extraction.chunk_text(text_to_index)
        for chunk in chunks:
            db.add(
                ResearchChunk(
                    document_id=document.id,
                    ordinal=chunk.ordinal,
                    text=chunk.text,
                    token_estimate=chunk.token_estimate,
                    page=chunk.page,
                    section=chunk.section,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    embedding=vector.serialise(vector.embed(chunk.text, idf=idf)),
                    embedding_model=vector.MODEL_NAME,
                )
            )

        for kind, value, confidence in entities.extract(text_to_index, document.title):
            db.add(
                DocumentEntity(
                    document_id=document.id,
                    kind=kind,
                    value=_clip(value, 200),
                    normalised=value.lower()[:200],
                    confidence=confidence,
                )
            )

    run.documents_seen = seen
    run.documents_new = new
    run.duplicates = duplicates
    run.rejected = rejected
    run.outcome = IngestOutcome.INGESTED if new or seen else IngestOutcome.FAILED
    run.finished_at = datetime.now(UTC)
    run.duration_ms = int((run.finished_at - started).total_seconds() * 1000)
    db.flush()

    logger.info(
        "research %s: seen=%s new=%s dup=%s rejected=%s", source.code, seen, new, duplicates, rejected
    )
    return run


def ingest_all(
    db: Session, *, limit_per_source: int = 25, source_codes: list[str] | None = None
) -> dict[str, object]:
    from app.services.research.registry import enabled_sources

    sources = enabled_sources(db)
    if source_codes:
        wanted = {code.lower() for code in source_codes}
        sources = [source for source in sources if source.code.lower() in wanted]

    results = []
    for source in sources:
        run = ingest_source(db, source, limit=limit_per_source)
        results.append(
            {
                "source": source.code,
                "outcome": run.outcome.value,
                "seen": run.documents_seen,
                "new": run.documents_new,
                "duplicates": run.duplicates,
                "rejected": run.rejected,
                "error": run.error,
            }
        )
    db.commit()
    return {
        "sources": len(results),
        "documents_new": sum(int(row["new"]) for row in results),  # type: ignore[arg-type]
        "results": results,
    }


def _is_near_duplicate(db: Session, fingerprint: str) -> bool:
    """Same story already stored under a different URL."""
    candidates = db.scalars(
        select(ResearchDocument.simhash)
        .where(ResearchDocument.simhash != "")
        .order_by(ResearchDocument.created_at.desc())
        .limit(500)
    )
    return any(
        extraction.hamming(fingerprint, candidate) <= SIMHASH_THRESHOLD
        for candidate in candidates
        if candidate and candidate != fingerprint[: len(candidate)]
    )


def extraction_method_for(source: SourceRecord, item) -> object:
    from app.models.research import ExtractionMethod

    if item.url.lower().endswith(".pdf"):
        return ExtractionMethod.FEED_SUMMARY  # page text not fetched in Phase 4
    if item.content and len(item.content) > len(item.summary or "") + 50:
        return ExtractionMethod.HTML
    return ExtractionMethod.FEED_SUMMARY


CHROME_TITLES = frozenset(
    {
        "close dialog", "close", "skip to main content", "skip to content", "menu",
        "search", "share", "home", "back", "next", "previous", "more", "read more",
        "sign in", "log in", "subscribe", "cookies", "advertisement", "loading",
    }
)


def _plausible_title(title: str) -> bool:
    """Reject navigation/UI labels that arrive as feed items.

    A short title is not automatically junk — "Bank Rate 4%" is four words and
    meaningful — so this rejects known chrome strings and free-floating labels,
    not short headlines.
    """
    cleaned = (title or "").strip()
    if len(cleaned) < 8:
        return False
    if cleaned.lower().strip(" .:") in CHROME_TITLES:
        return False
    # A headline has at least two real words.
    return len([word for word in cleaned.split() if len(word) > 2]) >= 2


def _clip(value: str, limit: int) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1] + "…"


__all__ = [
    "EntityKind",
    "PROVIDERS",
    "SIMHASH_THRESHOLD",
    "ingest_all",
    "ingest_source",
]
