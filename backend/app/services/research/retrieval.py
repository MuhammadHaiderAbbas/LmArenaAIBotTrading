"""Retrieval over ingested research.

Hybrid, on purpose
------------------
Two independent rankers run and their scores are fused:

* **Lexical (BM25-ish)** over chunk text. Good at exact terms — "Bank Rate 4%",
  tickers, and the rare proper nouns that matter in financial news.
* **Vector (hashed lexical embedding)**. Tolerant of word order and repetition.

Fusing them is not decoration. A pure vector score puts every chunk containing
"rate" near the top, which for a central-bank corpus is most of them. A pure
lexical score misses paraphrases. Reciprocal rank fusion combines the two without
needing the scores to be on comparable scales.

Every result carries its citation coordinates — document, source, url, published
time, page/section and character span — because a retrieval result that cannot be
cited cannot be used in a report.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import ResearchChunk, ResearchDocument, SourceRecord
from app.services.research import bodies, extraction, vector

# Reciprocal rank fusion constant. 60 is the value from the original RRF paper and
# damps the influence of top ranks enough that one aggressive ranker cannot
# dominate.
RRF_K = 60

# Cap on chunks contributed by a single document. Without this, one long Bank of
# England minute supplies every result and the report cites one source eight times
# — which looks like corroboration and is not.
MAX_CHUNKS_PER_DOCUMENT = 3


@dataclass(slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    text: str
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    source_code: str
    source_name: str
    source_tier: str
    title: str
    url: str
    published_at: datetime | None
    page: int | None
    section: str
    char_start: int
    char_end: int
    injection_flags: int

    @property
    def locator(self) -> str:
        if self.page is not None:
            return f"p.{self.page}"
        if self.section:
            return self.section
        return f"chars {self.char_start}–{self.char_end}"


@dataclass(slots=True)
class SearchFilters:
    source_codes: list[str] | None = None
    since: datetime | None = None
    instruments: list[str] | None = None
    limit: int = 12
    # Fetch and index article bodies for stored-summary-only documents. Off by
    # default for interactive search (a page load must not fire 20 HTTP requests)
    # and on for report generation, which needs text long enough to support a claim.
    hydrate: bool = False
    hydrate_limit: int = 8


def search(db: Session, query: str, filters: SearchFilters | None = None) -> list[RetrievedChunk]:
    filters = filters or SearchFilters()
    term = (query or "").strip()
    if not term:
        return []

    statement = (
        select(ResearchChunk, ResearchDocument, SourceRecord)
        .join(ResearchDocument, ResearchChunk.document_id == ResearchDocument.id)
        .join(SourceRecord, ResearchDocument.source_id == SourceRecord.id)
    )
    if filters.source_codes:
        statement = statement.where(SourceRecord.code.in_(filters.source_codes))
    if filters.since is not None:
        statement = statement.where(ResearchDocument.published_at >= filters.since)

    rows = list(db.execute(statement.limit(2000)))
    if not rows:
        return []

    if filters.hydrate:
        # Hydration must follow ranking, not precede it. An earlier version
        # fetched the first ten documents in database order, which meant reports
        # were built from whatever happened to be newest while the relevant
        # documents stayed as one-line summaries.
        preview = _rank(rows, filters, limit=filters.hydrate_limit * 2, term=term)
        wanted: list[str] = []
        for hit in preview:
            if hit.document_id not in wanted:
                wanted.append(hit.document_id)
            if len(wanted) >= filters.hydrate_limit:
                break
        rows = _hydrate(db, rows, filters, wanted)

    if filters.instruments:
        # Filter before scoring: applying it after truncation would silently
        # return fewer hits than requested and look like a retrieval failure.
        wanted = {symbol.upper() for symbol in filters.instruments}
        rows = [
            row
            for row in rows
            if any(symbol in f"{row[1].title} {row[0].text}".upper() for symbol in wanted)
        ]
    if not rows:
        return []

    return _rank(rows, filters, limit=filters.limit, term=term)


def _rank(rows: list, filters: SearchFilters, *, limit: int, term: str | None = None) -> list[RetrievedChunk]:
    if term is None:
        term = getattr(filters, "_term", "")
    query_tokens = set(vector.tokenise(term))
    query_vector = vector.embed(term)
    if not query_tokens:
        return []

    lexical: list[tuple[float, int]] = []
    semantic: list[tuple[float, int]] = []
    for index, (chunk, _document, _source) in enumerate(rows):
        lexical.append((_bm25ish(chunk.text, query_tokens), index))
        semantic.append((vector.cosine(query_vector, vector.deserialise(chunk.embedding)), index))

    lexical_rank = _ranks(lexical)
    vector_rank = _ranks(semantic)

    scored: list[tuple[float, int]] = []
    for index, (chunk, document, _source) in enumerate(rows):
        # Relevance floor: a chunk must actually contain a query term in its text
        # or its document title. Without this, fusion ranks every chunk in the
        # corpus and noise wins on tie-break order — a report about sterling
        # ended up citing a story about gold.
        haystack = set(vector.tokenise(f"{document.title} {chunk.text}"))
        if not (query_tokens & haystack):
            continue
        score = 0.0
        if index in lexical_rank:
            score += 1.0 / (RRF_K + lexical_rank[index])
        if index in vector_rank:
            score += 1.0 / (RRF_K + vector_rank[index])
        if score > 0:
            scored.append((score, index))

    scored.sort(key=lambda pair: pair[0], reverse=True)

    # Diversity pass: walk the ranking and cap how many chunks each document may
    # contribute. A second pass fills any remaining slots if the corpus is thin.
    results: list[RetrievedChunk] = []
    per_document: dict[str, int] = {}
    deferred: list[tuple[float, int]] = []
    for score, index in scored:
        document_id = rows[index][1].id
        if per_document.get(document_id, 0) >= MAX_CHUNKS_PER_DOCUMENT:
            deferred.append((score, index))
            continue
        results.append(_to_hit(score, index, rows, lexical_rank, vector_rank))
        per_document[document_id] = per_document.get(document_id, 0) + 1
        if len(results) >= limit:
            return results

    for score, index in deferred:
        if len(results) >= limit:
            break
        results.append(_to_hit(score, index, rows, lexical_rank, vector_rank))
    return results


def _to_hit(
    score: float, index: int, rows: list, lexical_rank: dict, vector_rank: dict
) -> RetrievedChunk:
    chunk, document, source = rows[index]
    return RetrievedChunk(
        chunk_id=chunk.id,
        document_id=document.id,
        text=chunk.text,
        score=round(score, 8),
        lexical_rank=lexical_rank.get(index),
        vector_rank=vector_rank.get(index),
        source_code=source.code,
        source_name=source.name,
        source_tier=source.trust_tier.value,
        title=document.title,
        url=document.url,
        published_at=document.published_at,
        page=chunk.page,
        section=chunk.section,
        char_start=chunk.char_start,
        char_end=chunk.char_end,
        injection_flags=document.prompt_injection_flags,
    )


def _hydrate(db: Session, rows: list, filters: SearchFilters, document_ids: list[str]) -> list:
    """Fetch article bodies for documents that currently only hold a summary.

    Two important properties:
    * the body is used for this analysis only. It is never written to
      `research_chunks` unless the source licence permits storage, so a
      link-only publisher's text does not end up persisted in the platform;
    * a failure is silent here and visible in the returned `hydrated` count —
      one dead article must not fail the whole search.
    """
    hydrated = 0
    wanted = set(document_ids)
    for row in list(rows):
        if hydrated >= filters.hydrate_limit:
            break
        chunk, document, source = row
        if document.id not in wanted:
            continue
        if document.content_stored and document.word_count > 120:
            continue
        try:
            body = bodies.fetch_body(document.url, expect_title=document.title)
        except bodies.BodyFetchError:
            continue
        sanitised = extraction.sanitise(body)
        document.word_count = max(document.word_count, len(sanitised.text.split()))
        document.prompt_injection_flags = max(
            document.prompt_injection_flags, len(sanitised.flags)
        )
        if sanitised.flags:
            document.injection_detail = ", ".join(sanitised.flags)
        hydrated += 1

        if source.storage_permitted:
            # Licensed to store: index the body permanently.
            for piece in extraction.chunk_text(sanitised.text):
                db.add(
                    ResearchChunk(
                        document_id=document.id,
                        ordinal=10_000 + piece.ordinal,
                        text=piece.text,
                        token_estimate=piece.token_estimate,
                        page=piece.page,
                        section=piece.section or "body",
                        char_start=piece.char_start,
                        char_end=piece.char_end,
                        embedding=vector.serialise(vector.embed(piece.text)),
                        embedding_model=vector.MODEL_NAME,
                    )
                )
            db.flush()
            continue

        # Not licensed to store: analyse in memory. The chunk object is transient
        # and is discarded when the session ends.
        transient = [
            (ResearchChunk(
        id=f"transient-{document.id}-{piece.ordinal}",
        document_id=document.id,
        ordinal=piece.ordinal,
        text=piece.text,
        token_estimate=piece.token_estimate,
        page=piece.page,
        section=piece.section or "body",
        char_start=piece.char_start,
        char_end=piece.char_end,
        embedding=vector.serialise(vector.embed(piece.text)),
        embedding_model=f"{vector.MODEL_NAME}-transient",
            ), document, source)
            for piece in extraction.chunk_text(sanitised.text)
        ]
        rows = [row for row in rows if row[0].document_id != document.id]
        rows.extend(transient)

    return rows


def _bm25ish(text: str, query_tokens: set[str]) -> float:
    """Term-overlap score with length normalisation.

    A full BM25 needs corpus-wide statistics kept per query; this keeps the
    ranking property that matters — rare query terms in a short chunk outrank a
    long chunk that mentions everything — without another index to maintain.
    """
    if not query_tokens:
        return 0.0
    tokens = vector.tokenise(text)
    if not tokens:
        return 0.0
    counts: dict[str, int] = {}
    for token in tokens:
        counts[token] = counts.get(token, 0) + 1
    hits = sum(counts.get(token, 0) for token in query_tokens)
    if not hits:
        return 0.0
    length_penalty = 1.0 / (1.0 + len(tokens) / 200.0)
    # Reward chunks that cover more of the query, not just repeat one word.
    coverage = sum(1 for token in query_tokens if token in counts) / len(query_tokens)
    return hits * length_penalty * (0.5 + coverage)


def _ranks(scores: list[tuple[float, int]]) -> dict[int, int]:
    ordered = [index for score, index in sorted(scores, key=lambda pair: pair[0], reverse=True) if score > 0]
    return {index: rank + 1 for rank, index in enumerate(ordered)}


# Split on sentence-ending punctuation, tolerating a missing space after it
# ("…(Box C).Consistent with…") and decimal points ("3.75%").
QUOTE_RE = re.compile(r"[^.!?]*?[.!?](?=\s|$|[A-Z])")


DECIMAL_RE = re.compile(r"(?<=\d)\.(?=\d)")


def _sentences(text: str) -> list[str]:
    """Split text into sentences, protecting decimal points.

    Decimal points are masked before splitting so "3.75%" and "1.3074" do not
    break a sentence in half.
    """
    masked = DECIMAL_RE.sub("\u0001", text)
    return [
        segment.replace("\u0001", ".").strip()
        for segment in QUOTE_RE.findall(masked)
        if segment.strip()
    ]


def candidate_quotes(text: str, query: str, *, limit: int = 4, max_chars: int = 320) -> list[str]:
    """Candidate sentences for citation, best first.

    A chunk can be cut mid-sentence, so the leading fragment is a dead end for
    extraction. Returning several candidates lets the report builder keep looking
    rather than concluding the chunk has nothing to say.
    """
    sentences = _sentences(text)
    if not sentences:
        return [text[:max_chars].strip()]

    tokens = set(vector.tokenise(query))

    def score(sentence: str) -> float:
        sentence_tokens = set(vector.tokenise(sentence))
        if not sentence_tokens:
            return 0.0
        overlap = len(tokens & sentence_tokens) / max(len(tokens), 1)
        value = overlap / (1 + len(sentence) / 400.0)
        if QUANTIFIED_RE.search(sentence):
            value += 0.75
            if tokens & sentence_tokens:
                value += 0.5
        # Fragments that begin mid-word are penalised, not excluded: they are last
        # resorts only.
        if sentence[0].islower():
            value -= 0.2
        return value

    ordered = sorted(sentences, key=score, reverse=True)
    seen: list[str] = []
    for sentence in ordered:
        clipped = sentence[:max_chars].strip()
        if clipped and clipped not in seen:
            seen.append(clipped)
        if len(seen) >= limit:
            break
    return seen


# Sentences that carry a checkable quantity. A quote without one cannot support a
# claim, so it is not a candidate for the primary quotation.
QUANTIFIED_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:%|per cent|percent|bps|basis points|pence|points|bn|billion|million)"
    r"|[$£€]\s?\d",
    re.I,
)


def best_quote(text: str, query: str, *, max_chars: int = 320) -> str:
    """Pick the sentence that best supports the query.

    This is extraction, not generation: the returned string always exists verbatim
    in the source chunk, so a reader can find it.

    Ranking prefers a sentence that contains both a query term and a quantity.
    Picking the most query-word-dense sentence alone returned sentences that were
    about the right subject and asserted nothing — which is how a report ends up
    with zero claims despite having good evidence in hand.
    """
    sentences = _sentences(text)
    if not sentences:
        return text[:max_chars].strip()

    tokens = set(vector.tokenise(query))

    def score(sentence: str) -> float:
        sentence_tokens = set(vector.tokenise(sentence))
        if not sentence_tokens:
            return 0.0
        overlap = len(tokens & sentence_tokens) / max(len(tokens), 1)
        # Length damping keeps a long paragraph from winning by sheer word count.
        length_factor = 1.0 / (1 + len(sentence) / 400.0)
        relevance = overlap * length_factor
        if QUANTIFIED_RE.search(sentence):
            relevance += 0.75
        # Clearly better bonus when the sentence both names the subject and
        # states a number.
        if QUANTIFIED_RE.search(sentence) and tokens & sentence_tokens:
            relevance += 0.5
        return relevance

    best = max(sentences, key=score)
    return best[:max_chars].strip()
