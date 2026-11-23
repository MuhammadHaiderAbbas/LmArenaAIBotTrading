"""Article body fetching.

Why this exists
---------------
Feeds carry a headline and a one-sentence summary. That is enough to *find* a
document and nowhere near enough to *support a claim*. An earlier version of this
pipeline built reports from feed summaries and produced documents averaging 17
words — retrieval looked fine and every claim it could extract was trivia.

So bodies are fetched properly, and the licence decides what happens next:

* `storage_permitted = True` (US federal material, which is public domain) —
  the extracted body is stored and indexed at ingest time.
* `storage_permitted = False` (ECB, BoE, BBC, Dow Jones) — the body is fetched
  **on demand**, used in memory for the current analysis, and never persisted.
  The database keeps the publisher's own summary and a link, which is what their
  terms allow.

Main-content extraction is heuristic on purpose. A readability model would pull
in a dependency and still guess; the heuristics here are the ones that survive
contact with government and news sites: prefer the largest text-dense block,
drop navigation and boilerplate, and keep paragraphs as separate lines so
chunking preserves structure.
"""

from __future__ import annotations

import re

import httpx

from app.integrations.research.rss import HEADERS
from app.services.research import extraction

# List/index pages are not articles. An earlier version accepted a Bank of England
# news index as an article body, which put navigation text into the evidence pool
# and made every claim look unsupported.
LISTING_MARKERS = (
    "published on", "see all", "view all", "load more", "next page", "previous page",
    "sort by", "filter by", "results per page", "skip to main content",
)

BOILERPLATE = (
    "cookie", "subscribe", "sign up", "newsletter", "all rights reserved",
    "share this", "follow us", "advertisement", "javascript", "enable cookies",
    "privacy policy", "terms of use", "download the app", "log in", "sign in",
    "this video can not be played", "this video cannot be played", "watch live",
    "this content is not available", "getty images", "image source",
)

PARAGRAPH_RE = re.compile(r"<(?:p|div|li|h[1-6])[^>]*>(.*?)</(?:p|div|li|h[1-6])>", re.I | re.S)
ARTICLE_RE = re.compile(r"<(article|main)[^>]*>(.*?)</\1>", re.I | re.S)


class BodyFetchError(Exception):
    def __init__(self, url: str, message: str) -> None:
        super().__init__(f"{url}: {message}")
        self.url = url
        self.message = message


def fetch_body(url: str, *, timeout: float = 20.0, client=None, expect_title: str = "") -> str:
    """Fetch and extract the main text of an article."""
    if not url or not url.startswith(("http://", "https://")):
        raise BodyFetchError(url, "not an absolute URL")

    try:
        if client is not None:
            response = client.get(url, headers=HEADERS)
        else:
            with httpx.Client(
                timeout=timeout, headers=HEADERS, follow_redirects=True
            ) as session:
                response = session.get(url)
    except httpx.HTTPError as exc:
        raise BodyFetchError(url, f"network error: {exc}") from exc

    if response.status_code >= 400:
        raise BodyFetchError(url, f"HTTP {response.status_code}")

    content_type = response.headers.get("content-type", "")
    if "pdf" in content_type or url.lower().endswith(".pdf"):
        # PDF bodies are a Phase 5 concern; saying so is better than indexing
        # binary noise.
        raise BodyFetchError(url, "PDF body extraction is not implemented")

    text = extract_main_text(response.text)
    if len(text.split()) < 40:
        raise BodyFetchError(url, f"extracted only {len(text.split())} words")
    if looks_like_listing(text):
        raise BodyFetchError(url, "page looks like an index/listing, not an article")
    if expect_title and not _title_present(response.text, text, expect_title):
        raise BodyFetchError(url, "page does not contain the expected headline")
    return text


def _title_present(html: str, text: str, expect_title: str) -> bool:
    """Compare the expected headline with the page's title or opening text."""
    def normalise(value: str) -> set[str]:
        return {word for word in re.findall(r"[a-z]+", value.lower()) if len(word) > 3}

    wanted = normalise(expect_title)
    if not wanted:
        return True
    haystack = normalise(f"{_headline_of(html)} {text[:2000]}")
    overlap = len(wanted & haystack) / len(wanted)
    return overlap >= 0.6


def _headline_of(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    if match:
        return extraction.sanitise(match.group(1)).text
    match = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
    return extraction.sanitise(match.group(1)).text if match else ""


def looks_like_listing(text: str) -> bool:
    """True when extracted text reads as an index page rather than an article."""
    words = text.split()
    if len(words) < 120:
        return False
    lowered = text.lower()
    hits = sum(1 for marker in LISTING_MARKERS if marker in lowered)
    # Index pages repeat the same link text; articles repeat sentences rarely.
    sentences = [segment.strip() for segment in re.split(r"[.!?]", text) if len(segment.strip()) > 20]
    unique_ratio = len(set(sentences)) / max(len(sentences), 1)
    return hits >= 2 or (unique_ratio < 0.75 and hits >= 1)


def extract_main_text(html: str) -> str:
    """Pick the densest text block and drop boilerplate lines."""
    candidate = html
    container = ARTICLE_RE.search(html)
    if container is not None:
        candidate = container.group(2)

    blocks = PARAGRAPH_RE.findall(candidate) or [candidate]

    paragraphs: list[str] = []
    for block in blocks:
        cleaned = extraction.sanitise(block).text
        if len(cleaned.split()) < 6:
            continue
        lowered = cleaned.lower()
        if any(marker in lowered for marker in BOILERPLATE):
            continue
        if cleaned.count("|") > 4:  # table or nav strip
            continue
        paragraphs.append(cleaned)

    if not paragraphs:
        return extraction.sanitise(candidate).text

    # Drop short leading/trailing fragments that are usually captions or credits.
    while paragraphs and len(paragraphs[0].split()) < 10:
        paragraphs.pop(0)
    while paragraphs and len(paragraphs[-1].split()) < 10:
        paragraphs.pop()

    return "\n\n".join(paragraphs)
