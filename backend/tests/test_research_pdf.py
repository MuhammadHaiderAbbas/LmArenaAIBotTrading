"""PDF body extraction.

A scanned PDF must fail loudly. Returning an empty string would look like a
successful read of an empty document and would quietly remove a policy statement
from the evidence base.
"""

from __future__ import annotations

import io

import pytest

from app.services.research import bodies, pdf


def build_pdf(lines: list[str], *, pages: int = 1, footer: str = "") -> bytes:
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = io.BytesIO()
    canvas = reportlab.Canvas(buffer)
    for page in range(pages):
        y = 800
        for line in lines:
            canvas.drawString(60, y, line)
            y -= 52
        if footer:
            canvas.drawString(60, 40, footer.format(page=page + 1, total=pages))
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


POLICY_LINES = [
    "Monetary Policy Summary",
    "The Monetary Policy Committee voted by a majority of 7 to 2 to hold Bank Rate at 4%.",
    "Twelve-month CPI inflation was 2.6% in September, down from 3.1% in August.",
    "The committee judged that a restrictive stance of monetary policy remained appropriate.",
    "Services inflation at 4.4% is still above the level consistent with the target.",
    "The labour market has loosened and unemployment rose to 4.4% in the three months to August.",
]


def test_pdf_text_is_extracted() -> None:
    text = pdf.extract_text(build_pdf(POLICY_LINES, pages=2))
    assert "Bank Rate at 4%" in text
    assert "CPI inflation was 2.6%" in text
    assert len(text.split()) >= pdf.MIN_WORDS


def test_page_numbers_are_dropped() -> None:
    text = pdf.extract_text(build_pdf(POLICY_LINES, pages=3, footer="Page {page} of {total}"))
    assert "Page 1 of 3" not in text
    assert "Page 3 of 3" not in text


def test_wrapped_lines_are_reflowed_into_paragraphs() -> None:
    """PDF text arrives one visual row per line; leaving that shape would make
    every sentence look like a paragraph and defeat the quote picker."""
    text = pdf.extract_text(build_pdf(POLICY_LINES, pages=1))
    assert "\n\n" in text or len(text.splitlines()) <= len(POLICY_LINES)
    assert "4%\n" not in text.replace("\n\n", "\n\n")


def test_page_cap_is_stated_in_the_output() -> None:
    text = pdf.extract_text(build_pdf(POLICY_LINES, pages=6), max_pages=2)
    assert "first 2 of 6 pages" in text


def test_empty_bytes_are_rejected() -> None:
    with pytest.raises(pdf.PdfExtractionError):
        pdf.extract_text(b"")


def test_non_pdf_bytes_are_rejected_with_a_clear_reason() -> None:
    with pytest.raises(pdf.PdfExtractionError) as exc:
        pdf.extract_text(b"<html><body>This is not a PDF at all.</body></html>")
    assert "not a readable PDF" in exc.value.reason


def test_pdf_with_no_text_layer_is_reported_not_returned_empty() -> None:
    reportlab = pytest.importorskip("reportlab.pdfgen.canvas")
    buffer = io.BytesIO()
    canvas = reportlab.Canvas(buffer)
    canvas.rect(100, 500, 200, 200)  # a drawing, no text
    canvas.showPage()
    canvas.save()

    with pytest.raises(pdf.PdfExtractionError) as exc:
        pdf.extract_text(buffer.getvalue())
    assert "scanned document" in exc.value.reason


def test_is_pdf_detects_by_content_type_and_extension() -> None:
    assert pdf.is_pdf("https://e.com/a.pdf", "") is True
    assert pdf.is_pdf("https://e.com/a?x=1", "application/pdf") is True
    assert pdf.is_pdf("https://e.com/a.html", "text/html") is False
    assert pdf.is_pdf("https://e.com/a?file=b.pdf", "application/pdf") is True


class _Response:
    def __init__(self, content: bytes, content_type: str = "application/pdf") -> None:
        self.content = content
        self.text = "<html></html>"
        self.status_code = 200
        self.headers = {"content-type": content_type}


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response

    def get(self, url, **kwargs):  # noqa: ANN001, ANN003, ARG002
        return self.response


def test_fetch_body_reads_a_pdf_through_the_same_interface() -> None:
    payload = build_pdf(POLICY_LINES, pages=2)
    text = bodies.fetch_body("https://e.com/statement.pdf", client=_Client(_Response(payload)))
    assert "Bank Rate at 4%" in text


def test_a_broken_pdf_surfaces_as_a_body_fetch_error() -> None:
    client = _Client(_Response(b"%PDF-1.4 not really a pdf"))
    with pytest.raises(bodies.BodyFetchError) as exc:
        bodies.fetch_body("https://e.com/broken.pdf", client=client)
    assert "PDF" in str(exc.value)


def test_ingest_fetches_and_indexes_a_pdf_for_a_storage_permitted_source(
    client, monkeypatch
) -> None:
    """US federal material is public domain, so a PDF body is stored and indexed."""
    from datetime import UTC, datetime

    from app.database.session import SessionLocal
    from app.integrations.research.base import FeedItem, FetchResult
    from app.models.research import (
        ResearchChunk,
        ResearchDocument,
        SourceRecord,
    )
    from app.services.research import ingestion, registry

    db = SessionLocal()
    try:
        registry.seed_sources(db)
        db.commit()
        source = db.query(SourceRecord).filter(SourceRecord.code == "fed_press").one()
        assert source.storage_permitted is True

        payload = build_pdf(POLICY_LINES, pages=2)

        class Provider:
            name = "rss"

            def fetch(self, feed_url, *, limit=40):  # noqa: ANN001, ARG002
                return FetchResult(
                    items=[
                        FeedItem(
                            external_id="pdf-1",
                            title="Monetary Policy Summary and Statement",
                            url="https://www.federalreserve.gov/statement.pdf",
                            summary="A policy statement.",
                            published_at=datetime.now(UTC),
                        )
                    ]
                )

            def health(self, feed_url):  # noqa: ANN001, ARG002
                return True, "stubbed"

        monkeypatch.setitem(ingestion.PROVIDERS, "rss", Provider())
        monkeypatch.setattr(bodies, "fetch_body", lambda url, **kw: pdf.extract_text(payload))

        run = ingestion.ingest_source(db, source, limit=5)
        db.commit()

        document = (
            db.query(ResearchDocument)
            .filter(ResearchDocument.external_id == "pdf-1")
            .one()
        )
        assert document.content_stored is True
        assert document.word_count > 100
        assert run.pdf_extracted == 1
        assert run.pdf_failures == 0

        chunk_words = sum(
            len(chunk.text.split())
            for chunk in db.query(ResearchChunk).filter(
                ResearchChunk.document_id == document.id
            )
        )
        assert chunk_words > 100, "PDF text must reach the index, not just the row"
    finally:
        db.close()


def test_a_failed_pdf_fetch_is_counted_and_disclosed(client, monkeypatch) -> None:
    from datetime import UTC, datetime

    from app.database.session import SessionLocal
    from app.integrations.research.base import FeedItem, FetchResult
    from app.models.research import ResearchDocument, SourceRecord
    from app.services.research import ingestion, registry

    db = SessionLocal()
    try:
        registry.seed_sources(db)
        db.commit()
        source = db.query(SourceRecord).filter(SourceRecord.code == "fed_press").one()

        class Provider:
            name = "rss"

            def fetch(self, feed_url, *, limit=40):  # noqa: ANN001, ARG002
                return FetchResult(
                    items=[
                        FeedItem(
                            external_id="pdf-bad",
                            title="Statistical release on bank lending standards",
                            url="https://www.federalreserve.gov/scanned.pdf",
                            summary="A statistical release covering bank lending standards.",
                            published_at=datetime.now(UTC),
                        )
                    ]
                )

            def health(self, feed_url):  # noqa: ANN001, ARG002
                return True, "stubbed"

        def explode(url, **kwargs):  # noqa: ANN001, ARG001
            raise bodies.BodyFetchError(url, "PDF: likely a scanned document with no text layer")

        monkeypatch.setitem(ingestion.PROVIDERS, "rss", Provider())
        monkeypatch.setattr(bodies, "fetch_body", explode)

        run = ingestion.ingest_source(db, source, limit=5)
        db.commit()

        assert run.pdf_failures == 1
        document = (
            db.query(ResearchDocument)
            .filter(ResearchDocument.external_id == "pdf-bad")
            .one()
        )
        # The row still exists and the failure is recorded on it, so a reader can
        # open the source and knows why the text is not here.
        assert "PDF text unavailable" in document.summary
    finally:
        db.close()
