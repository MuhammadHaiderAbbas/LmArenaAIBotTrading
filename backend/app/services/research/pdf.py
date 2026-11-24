"""PDF text extraction for research sources.

Several central banks publish policy documents as PDFs — ECB press releases and
many of the Fed's statistical releases among them. Ignoring them means a research
tool that goes quiet exactly when a committee publishes a full document, so the
text is extracted in memory and handled like any other body.

Two rules carry over from the rest of the pipeline:

- **Nothing is written to disk.** Extraction happens on bytes already in memory.
- **A failure is reported, not swallowed.** A scanned PDF with no text layer
  raises ``PdfExtractionError`` with a message that says so; it does not return an
  empty string that looks like a successful read of an empty document.
"""

from __future__ import annotations

import io
import logging
import re

from app.services.research import extraction

logger = logging.getLogger(__name__)

MIN_WORDS = 40
# Page numbers, running headers and footers repeat on every page. They are not
# content, and they dominate short documents if left in.
PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s+)?\d{1,3}(?:\s*(?:of|/)\s*\d{1,3})?\s*$", re.I)


class PdfExtractionError(Exception):
    """The PDF could not be turned into usable text."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def extract_text(data: bytes, *, max_pages: int = 40) -> str:
    """Extract and clean the text layer of a PDF held in memory.

    ``max_pages`` is a guard, not a policy: a 300-page statistical annex would
    otherwise occupy the whole request budget. When the cap bites, the truncation
    is stated in the returned text so a reader is not misled about completeness.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise PdfExtractionError("pypdf is not installed") from exc

    if not data:
        raise PdfExtractionError("empty response body")

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # pypdf raises a variety of parse errors
        raise PdfExtractionError(f"not a readable PDF: {exc}") from exc

    if getattr(reader, "is_encrypted", False):
        # Try the empty password, which is what most "protected" published PDFs use.
        try:
            if reader.decrypt("") == 0:
                raise PdfExtractionError("PDF is encrypted and cannot be opened")
        except PdfExtractionError:
            raise
        except Exception as exc:
            raise PdfExtractionError(f"PDF is encrypted and cannot be opened: {exc}") from exc

    pages = reader.pages
    total = len(pages)
    truncated = total > max_pages

    collected: list[str] = []
    for page in pages[:max_pages]:
        try:
            collected.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the document
            logger.warning("PDF page %s could not be extracted: %s", page, exc)
            continue

    text = _clean("\n".join(collected))
    words = text.split()
    if len(words) < MIN_WORDS:
        raise PdfExtractionError(
            f"only {len(words)} words of text found in {total} page(s) — "
            "likely a scanned document with no text layer"
        )

    if truncated:
        text += f"\n\n[Extracted from the first {max_pages} of {total} pages.]"
    return text


def _clean(text: str) -> str:
    """Normalise whitespace and drop page furniture."""
    text = extraction.sanitise(text).text
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or PAGE_NUMBER_RE.match(stripped):
            continue
        kept.append(stripped)
    # Re-join: PDF extraction emits a line per visual row, which would otherwise
    # look like hundreds of one-line sentences to the chunker and quote picker.
    return _reflow(kept)


def _reflow(lines: list[str]) -> str:
    """Join wrapped lines back into paragraphs.

    A line ending in sentence punctuation or followed by an indented line starts a
    new paragraph; everything else is a wrap and gets joined with a space.
    """
    paragraphs: list[str] = []
    current: list[str] = []
    for line in lines:
        if current and (
            current[-1].endswith((".", ":", "?", "!", '"', "”")) and line[:1].isupper()
        ):
            paragraphs.append(" ".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs)


def is_pdf(url: str, content_type: str) -> bool:
    return "pdf" in (content_type or "").lower() or url.lower().split("?")[0].endswith(".pdf")
