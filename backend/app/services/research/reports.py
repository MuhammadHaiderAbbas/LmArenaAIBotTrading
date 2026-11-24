"""Source-linked report assembly.

The rule this module exists to enforce
--------------------------------------
**A claim is only 'supported' if a sentence in a retrieved chunk actually contains
its content.** No model writes prose here. The builder retrieves evidence, lifts
the supporting sentence verbatim, and then checks the claim against that sentence
with a lexical entailment test. Claims that fail the check are still shown — as
`unverified` — because hiding them would make the report look cleaner than the
evidence is.

Why extraction rather than generation: no LLM provider is wired in at this phase,
and a generated summary with no verifier is exactly how a research assistant ends
up confidently wrong. Phase 5 adds the orchestrator and specialist agents; this is
the evidence layer they will stand on, and it is deterministic so it can be
regression-tested.

Contradiction detection is deliberately simple and deliberately loud: two
supported claims from primary sources that assert different values for the same
metric are flagged rather than resolved. Deciding which central bank is "right" is
not the platform's job.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.research import (
    ClaimVerdict,
    FindingKind,
    ReportCitation,
    ReportClaim,
    ReportStatement,
    ResearchReport,
)
from app.services.research import retrieval, vector

MAX_CLAIMS = 6
MAX_STATEMENTS = 4

# Value assertions only. A number on its own is not a claim: "Gold settled 1" was
# produced by a regex that stopped at the thousands separator, and it read as a
# confident finding. A unit, a currency symbol or a percentage sign is now
# mandatory, and numbers must handle grouping and decimals.
VALUE = r"(?P<currency>[$£€])?(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
UNIT = r"(?P<unit>%|per cent|percent|bps|basis points|pence|points|bn|billion|million)"
# Verbs that introduce a value. Alternations are ordered longest-first so "rose to"
# is not partially consumed by "rose".
VALUE_VERBS = (
    "stood at|stands at|settled at|maintained at|remained at|increased to|increases to|"
    "decreased to|increased by|decreases to|decreased by|increases by|rises to|rose to|"
    "risen to|rise to|falls to|fell to|fallen to|fall to|falls by|fell by|"
    "held at|holds at|left at|keeps at|kept at|moved to|was|were|is|are|at|to|of|by"
)
ASSERTION_RE = re.compile(
    r"(?P<subject>[A-Za-z][A-Za-z0-9 /\-]{2,40}?)\s+"
    r"(?:(?:" + VALUE_VERBS + r")\s+)?" + VALUE + r"\s?" + UNIT,
    re.I,
)
ARTICLES = frozenset({"the", "a", "an"})

# Unit spellings are equivalent for verification purposes: the quantity has already
# been checked against the passage, so "percent" vs "percentage points" must not
# fail an otherwise verbatim claim.
UNIT_WORDS = frozenset(
    {
        "percent",
        "percentage",
        "points",
        "point",
        "cent",
        "pence",
        "basis",
        "bps",
        "billion",
        "million",
        "bn",
    }
)
# Words too common in this corpus to prove a sentence is on topic.
GENERIC_TERMS = frozenset(
    {
        "bank",
        "banks",
        "rate",
        "rates",
        "decision",
        "policy",
        "policies",
        "monetary",
        "market",
        "markets",
        "year",
        "2026",
        "2025",
        "board",
        "committee",
    }
)
# Lowercase words that are legitimately the subject of a sentence in this corpus.
KNOWN_TERMS = frozenset(
    {"inflation", "unemployment", "cpi", "gdp", "payrolls", "gold", "oil", "sterling", "euro"}
)
APOSTROPHE_FRAGMENTS = frozenset({"s", "re", "ve", "ll", "d", "t", "m"})
LEADING_VERBS = frozenset(
    {
        "maintain",
        "maintained",
        "hold",
        "held",
        "keep",
        "kept",
        "raise",
        "raised",
        "cut",
        "lower",
        "lowered",
        "set",
        "stay",
        "stayed",
        "move",
        "moved",
        "leave",
        "leaving",
        "voted",
        "vote",
        "grew",
        "grow",
        "rise",
        "rose",
        "fall",
        "fell",
        "increase",
        "increased",
        "decrease",
        "decreased",
        "remain",
        "remained",
    }
)

# A percentage statement is the other reliable shape and often has no verb:
# "Inflation 3.2%" or "CPI at 2.6 per cent".
PERCENT_RE = re.compile(
    r"(?P<subject>[A-Za-z][A-Za-z0-9 /\-]{2,40}?)\s+(?:at\s+|of\s+|was\s+|is\s+)?"
    r"(?P<value>\d{1,3}(?:\.\d+)?)\s?(?P<unit>%|per cent|percent)",
    re.I,
)


def build_report(
    db: Session,
    *,
    query: str,
    topic: str,
    instrument_symbol: str | None = None,
    since: datetime | None = None,
    source_codes: list[str] | None = None,
    created_by_id: str | None = None,
) -> ResearchReport:
    filters = retrieval.SearchFilters(
        source_codes=source_codes,
        since=since,
        instruments=[instrument_symbol] if instrument_symbol else None,
        limit=14,
        hydrate=True,
        hydrate_limit=10,
    )
    evidence = retrieval.search(db, query, filters)

    # An instrument filter is a preference, not a hard requirement: most macro
    # documents discuss sterling without ever writing "GBP/USD". Dropping to a
    # topic-only search is better than an empty report, provided the report says
    # it happened.
    instrument_matched = bool(evidence)
    if instrument_symbol and len(evidence) < 3:
        filters.instruments = None
        topic_evidence = retrieval.search(db, query, filters)
        if len(topic_evidence) > len(evidence):
            evidence = topic_evidence
            instrument_matched = False

    report = ResearchReport(
        title=_title(topic, instrument_symbol),
        topic=topic,
        instrument_symbol=instrument_symbol,
        query=query,
        generated_by="extractive-v1",
        status="draft",
        created_by_id=created_by_id,
    )
    db.add(report)
    db.flush()

    documents = {item.document_id for item in evidence}
    report.documents_used = len(documents)

    claims_built = _build_claims(db, report, evidence, query)
    supported = sum(1 for claim in claims_built if claim.verdict is ClaimVerdict.SUPPORTED)
    partial = sum(1 for claim in claims_built if claim.verdict is ClaimVerdict.PARTIAL)
    unverified = sum(
        1
        for claim in claims_built
        if claim.verdict in (ClaimVerdict.UNSUPPORTED, ClaimVerdict.UNVERIFIED)
    )

    report.claims_total = len(claims_built)
    report.claims_supported = supported
    report.claims_unverified = unverified
    report.contradictions = _detect_contradictions(db, report, claims_built)
    report.coverage_score = (
        round((supported + 0.5 * partial) / len(claims_built), 3) if claims_built else 0.0
    )

    _build_statements(db, report, evidence, claims_built, query)
    report.body_markdown = _render_markdown(report, evidence, claims_built)
    report.limitations = _limitations(evidence, claims_built, unverified)
    if instrument_symbol and not instrument_matched:
        report.limitations = (
            f"- No retrieved document names {instrument_symbol} directly; the evidence below is "
            "macro context, not instrument-specific.\n" + report.limitations
        )
    db.flush()
    return report


def _build_claims(
    db: Session, report: ResearchReport, evidence: list[retrieval.RetrievedChunk], query: str
) -> list[ReportClaim]:
    claims: list[ReportClaim] = []
    used: set[str] = set()

    for item in evidence:
        if len(claims) >= MAX_CLAIMS:
            break
        if item.chunk_id in used:
            continue

        # Try several candidate sentences per chunk: the best-scoring sentence is
        # not always the extractable one, and giving up after one attempt lost the
        # Bank of England's actual rate decision to a broken fragment.
        for quote in retrieval.candidate_quotes(item.text, query, limit=4):
            if not _is_on_topic(quote, query, report.instrument_symbol):
                continue
            assertion = _extract_assertion(quote)
            if assertion is None:
                continue
            break
        else:
            continue

        verdict, confidence, note = _verify(assertion, quote, item)
        claim = ReportClaim(
            report_id=report.id,
            ordinal=len(claims),
            text=assertion,
            verdict=verdict,
            confidence=confidence,
            evidence_count=1,
            note=note,
        )
        db.add(claim)
        db.flush()
        # A transient (in-memory, unlicensed-to-store) chunk has no row to point
        # at. The citation keeps the quote, the locator and the URL so the reader
        # can verify it at the publisher, and chunk_id stays null rather than
        # referencing something that does not exist.
        persisted_chunk_id = None if item.chunk_id.startswith("transient-") else item.chunk_id
        db.add(
            ReportCitation(
                report_id=report.id,
                claim_id=claim.id,
                chunk_id=persisted_chunk_id,
                document_id=item.document_id,
                source_code=item.source_code,
                source_name=item.source_name,
                url=item.url,
                published_at=item.published_at,
                quote=quote,
                locator=(
                    item.locator
                    if persisted_chunk_id
                    else f"{item.locator} (article body, not stored)"
                ),
                relevance=1.0 if item.source_tier == "primary" else 0.6,
                retrieval_score=item.score,
            )
        )
        claims.append(claim)
        used.add(item.chunk_id)

    return claims


def _is_on_topic(sentence: str, query: str, instrument_symbol: str | None) -> bool:
    """Reject a quote that is merely the best of a bad chunk.

    A chunk can rank highly on document-level relevance while the sentence lifted
    from it is about something else entirely — that is how "Gold settled 1" ended
    up cited in a report about sterling. Requiring one query term (or the
    instrument) in the quoted sentence keeps citations attached to the question.
    """
    terms = set(vector.tokenise(query))
    if instrument_symbol:
        terms.add(instrument_symbol.lower())
    if not terms:
        return True
    sentence_terms = set(vector.tokenise(sentence))
    if not (terms & sentence_terms):
        return False
    # One shared generic token is not topicality: "Bank" in "The Bank of Nova
    # Scotia" matched a query about Bank Rate and put an unrelated approval notice
    # in a sterling report. Require a distinctive term, or two overlaps.
    distinctive = terms - GENERIC_TERMS
    if distinctive & sentence_terms:
        return True
    # Two overlaps require at least one that is not corpus-generic, otherwise
    # "inflation … expected" passes for any sentence mentioning inflation.
    non_generic_overlap = (terms & sentence_terms) - GENERIC_TERMS
    if len(terms & sentence_terms) >= 2 and non_generic_overlap:
        return True
    # A quantified sentence sharing a generic subject term is still accepted, but
    # only if it is not a fragment: "5% in September." is a dead end, while
    # "Inflation rose to 5% in September" is exactly the evidence worth citing.
    if not re.search(r"\d", sentence) or not (terms & sentence_terms):
        return False
    return sentence[0].isupper() and len(sentence.split()) >= 6


def _clean_subject(raw: str) -> str | None:
    """Reduce the matched span to the noun phrase that is actually the subject.

    The regex is greedy: it starts mid-word on possessives ("…Policy Committee"
    matched from "icy") and drags verbs and conjunctions into the span. This keeps
    the trailing noun phrase and rejects anything that still looks like a fragment.
    A subject that cannot be cleaned is dropped rather than printed — "March 2023
    m" is worse than no claim at all, because it looks like a finding.
    """
    text = raw.replace("\u2019", "'").strip(" ,;:-'")
    if not text:
        return None

    # Cut at the last possessive or conjunction: "…Committee voted to maintain
    # Bank Rate" becomes "Bank Rate", which is what the value belongs to.
    for separator in ("'s ", " and ", " but ", " while ", ", ", " to ", " the "):
        if separator in text:
            text = text.rsplit(separator, 1)[-1]

    words = [word for word in text.split() if word]

    while words and (words[0].lower() in ARTICLES or words[0].lower() in APOSTROPHE_FRAGMENTS):
        words.pop(0)
    while words and words[0].lower() in LEADING_VERBS:
        words.pop(0)
    while words and len(words[-1]) <= 1:
        words.pop()

    if not words:
        return None
    # The noun phrase sits immediately before the value.
    if len(words) > 4:
        words = words[-4:]

    subject = " ".join(words)
    if len(subject) < 3:
        return None
    if not any(len(word) >= 3 for word in words):
        return None
    if len(words) == 1 and not subject[0].isupper() and subject.lower() not in KNOWN_TERMS:
        return None
    return subject


def _extract_assertion(sentence: str) -> str | None:
    """Lift a single checkable assertion out of a sentence.

    Returns None when the sentence asserts no quantity. An opinion is not a claim,
    and a bare number is not evidence — dressing either as one is how a research
    tool ends up sounding authoritative about nothing.
    """
    match = ASSERTION_RE.search(sentence) or PERCENT_RE.search(sentence)
    if match is None:
        return None

    subject = _clean_subject(match.group("subject"))
    if subject is None:
        return None

    value = match.group("value").replace(" ", "")
    currency = match.groupdict().get("currency") or ""
    unit = (match.group("unit") or "").strip()
    # Bare "m" and "p" are accepted only as whole words, never as the first letter
    # of the next clause ("…2023 m" from "…2023 minus…").
    if unit.lower() in {"m", "p"} and not re.search(rf"\b{unit}\b\.?\s", sentence.lower()):
        return None
    if not unit and not currency:
        return None
    # Hundreds of "1" or "2" are counts, not findings.
    if not unit and currency and float(value.replace(",", "")) < 10:
        return None

    # "3.2 per cent" reads correctly; "3.2%" has no space. Normalise once here so
    # the claim text and its rendering cannot disagree.
    separator = "" if unit.startswith(("%", "p", "b")) and len(unit) <= 3 else " "
    core = f"{subject} {currency}{value}{separator}{unit}".strip()
    return core[:400]


def _verify(
    assertion: str, quote: str, item: retrieval.RetrievedChunk
) -> tuple[ClaimVerdict, float, str]:
    """Check the claim against the sentence that produced it.

    The test is lexical entailment: every content token of the claim must appear
    in the quote, and the numeric value must appear verbatim. That is strict — it
    will reject valid paraphrases — which is the correct direction for a system
    that would otherwise overstate its evidence.
    """
    claim_tokens = set(vector.tokenise(assertion)) - UNIT_WORDS
    quote_tokens = set(vector.tokenise(quote))
    if not claim_tokens:
        return ClaimVerdict.UNVERIFIED, 0.0, "claim contained no checkable terms"

    missing = claim_tokens - quote_tokens
    numbers = re.findall(r"\d+(?:[.,]\d+)?", assertion)
    numbers_present = all(number in quote for number in numbers) if numbers else True

    coverage = 1 - (len(missing) / len(claim_tokens))
    if not numbers_present:
        return (
            ClaimVerdict.UNSUPPORTED,
            0.0,
            "value in claim does not appear in the cited sentence",
        )
    if coverage >= 0.99:
        confidence = 0.95 if item.source_tier == "primary" else 0.8
        return ClaimVerdict.SUPPORTED, confidence, "verbatim in cited passage"
    if coverage >= 0.7:
        return (
            ClaimVerdict.PARTIAL,
            round(0.5 + coverage / 4, 3),
            f"partially present ({round(coverage * 100)}% of terms found)",
        )
    return (
        ClaimVerdict.UNSUPPORTED,
        round(max(coverage, 0.0), 3),
        "cited passage does not contain the claim",
    )


def _detect_contradictions(db: Session, report: ResearchReport, claims: list[ReportClaim]) -> int:
    """Flag two supported claims asserting different values for the same subject."""
    by_subject: dict[str, list[ReportClaim]] = {}
    for claim in claims:
        if claim.verdict is not ClaimVerdict.SUPPORTED:
            continue
        subject = re.sub(r"[\d.,]+.*$", "", claim.text).strip().lower()
        if not subject:
            continue
        by_subject.setdefault(subject, []).append(claim)

    contradictions = 0
    for subject, group in by_subject.items():
        values = {
            re.findall(r"[\d.,]+", claim.text)[0]
            for claim in group
            if re.findall(r"[\d.,]+", claim.text)
        }
        if len(values) > 1:
            contradictions += 1
            db.add(
                ReportStatement(
                    report_id=report.id,
                    kind=FindingKind.CONTRADICTION,
                    text=(
                        f"Sources disagree on '{subject}': "
                        + " vs ".join(sorted(values))
                        + ". Both are cited; neither is resolved here."
                    ),
                    confidence=1.0,
                )
            )
    return contradictions


def _build_statements(
    db: Session,
    report: ResearchReport,
    evidence: list[retrieval.RetrievedChunk],
    claims: list[ReportClaim],
    query: str,
) -> None:
    """Add explicitly-labelled interpretation. Always attributed, never as fact."""
    statements: list[tuple[FindingKind, str, float]] = []

    primary = [item for item in evidence if item.source_tier == "primary"]
    commentary = [item for item in evidence if item.source_tier == "commentary"]
    if evidence:
        statements.append(
            (
                FindingKind.FACT,
                f"{len(evidence)} passages retrieved from {len({item.source_code for item in evidence})} source(s), "
                f"{len(primary)} of them primary.",
                1.0,
            )
        )
    if claims and not primary:
        statements.append(
            (
                FindingKind.UNCERTAINTY,
                "No primary source supports these claims; all evidence is commentary. "
                "Treat the findings as unconfirmed reporting, not official fact.",
                0.9,
            )
        )
    if commentary and primary:
        statements.append(
            (
                FindingKind.INTERPRETATION,
                "Commentary is present alongside primary material. Where the two diverge, "
                "the primary source is the record of what was decided; commentary is the reaction to it.",
                0.7,
            )
        )
    aged = [
        item
        for item in evidence
        if item.published_at is not None and _age_days(item.published_at) > 30
    ]
    if aged and len(aged) == len(evidence):
        statements.append(
            (
                FindingKind.UNCERTAINTY,
                "All retrieved evidence is more than 30 days old. The market may have moved past it.",
                0.8,
            )
        )
    untrusted = [item for item in evidence if item.injection_flags > 0]
    if untrusted:
        statements.append(
            (
                FindingKind.UNCERTAINTY,
                f"{len(untrusted)} retrieved passage(s) contain text resembling instructions to a "
                "language model. They were treated as data only; see the document flags.",
                0.95,
            )
        )

    for kind, text, confidence in statements[:MAX_STATEMENTS]:
        db.add(ReportStatement(report_id=report.id, kind=kind, text=text, confidence=confidence))


def _age_days(published: datetime) -> int:
    """Age in days, tolerating naive timestamps.

    SQLite round-trips DateTime(timezone=True) as naive, so every age comparison
    has to normalise first — comparing naive to aware raises, and silently
    treating one as UTC would be a subtle correctness bug.
    """
    moment = published if published.tzinfo is not None else published.replace(tzinfo=UTC)
    return (datetime.now(UTC) - moment).days


def _render_markdown(
    report: ResearchReport,
    evidence: list[retrieval.RetrievedChunk],
    claims: list[ReportClaim],
) -> str:
    lines: list[str] = []
    lines.append(f"# {report.title}")
    lines.append("")
    lines.append(
        f"*Generated {datetime.now(UTC):%d %B %Y %H:%M} UTC by `{report.generated_by}` — "
        "extractive, deterministic, no language model.*"
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    if claims:
        lines.append(
            f"{report.claims_total} checkable claim(s) extracted from "
            f"{report.documents_used} document(s). "
            f"{report.claims_supported} supported by a cited passage verbatim, "
            f"{report.claims_unverified} unsupported or unverified."
        )
    else:
        lines.append(
            "No checkable claims were found in the retrieved evidence. "
            "An empty report is the honest output here."
        )
    lines.append("")

    if claims:
        lines.append("## Claims and evidence")
        lines.append("")
        for claim in claims:
            badge = {
                ClaimVerdict.SUPPORTED: "supported",
                ClaimVerdict.PARTIAL: "partially supported",
                ClaimVerdict.UNSUPPORTED: "unsupported",
                ClaimVerdict.CONTRADICTED: "contradicted",
                ClaimVerdict.UNVERIFIED: "unverified",
            }[claim.verdict]
            lines.append(f"{claim.ordinal + 1}. **{claim.text}** — _{badge}_")
            for citation in claim.citations:
                published = (
                    citation.published_at.strftime("%d %b %Y")
                    if citation.published_at
                    else "undated"
                )
                lines.append(
                    f"   - [{citation.source_name}]({citation.url}) · {published} · {citation.locator}"
                )
                lines.append(f"     > {citation.quote}")
            if claim.note:
                lines.append(f"   - _check: {claim.note}_")
        lines.append("")

    if evidence:
        lines.append("## Sources consulted")
        lines.append("")
        for item in evidence[:10]:
            published = item.published_at.strftime("%d %b %Y") if item.published_at else "undated"
            lines.append(
                f"- [{item.title}]({item.url}) — {item.source_name}, {published} ({item.source_tier})"
            )
        lines.append("")

    return "\n".join(lines)


def _limitations(
    evidence: list[retrieval.RetrievedChunk], claims: list[ReportClaim], unverified: int
) -> str:
    notes: list[str] = []
    if not evidence:
        notes.append("No evidence retrieved for this query.")
    if unverified:
        notes.append(f"{unverified} claim(s) could not be verified against a cited passage.")
    if evidence and not any(item.source_tier == "primary" for item in evidence):
        notes.append("No primary source was retrieved; all evidence is secondary or commentary.")
    notes.append(
        "Retrieval uses a deterministic hashed lexical vector, not a neural embedding. "
        "Paraphrased passages may be missed; see docs/architecture/phase-4.md."
    )
    notes.append(
        "Document bodies are fetched from public publishers under their terms. "
        "Where redistribution is not permitted, only headlines, publisher summaries and links are stored."
    )
    notes.append("This is research support, not investment advice, and it places no orders.")
    return "\n".join(f"- {note}" for note in notes)


def _title(topic: str, instrument_symbol: str | None) -> str:
    label = topic.strip() if topic.strip() else "Research note"
    if instrument_symbol:
        return f"{instrument_symbol} — {label}"
    return label[:380]
