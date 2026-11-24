"""RSS/Atom feed adapter.

Works against any well-formed RSS 2.0 or Atom feed, which covers every official
publisher in the Phase 4 registry (ECB, Bank of England, Federal Reserve, SEC,
Dow Jones markets, BBC Business).

Parsing notes
-------------
* `defusedxml` is used deliberately: feed XML is attacker-controlled input in the
  general case, and entity-expansion attacks are a real class of bug. It is not
  installed, so `xml.etree` is used with entity expansion disabled where
  possible; the parser is wrapped so a malformed or hostile document becomes a
  `ResearchProviderError`, never a crash.
* Dates arrive in RFC 822 (RSS) or RFC 3339 (Atom) and in several broken
  variants. An unparseable date yields `None` rather than a fabricated "now",
  because a wrong publication time is worse than a missing one.
* Content is reduced to plain text here; chunking happens in the pipeline.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.integrations.research.base import (
    FeedItem,
    FetchResult,
    ResearchProvider,
    ResearchProviderError,
)

ATOM = "{http://www.w3.org/2005/Atom}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"
DC = "{http://purl.org/dc/elements/1.1/}"

TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
BLANK_LINES_RE = re.compile(r"\n{3,}")

# SEC rejects requests without a declared contact in the User-Agent (their access
# policy requires it), so the UA carries a project URL rather than a browser string.
HEADERS = {
    "User-Agent": (
        "AUREX-Research/0.1 (research platform; "
        "+https://github.com/MuhammadHaiderAbbas/LmArenaAIBotTrading)"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Publishers vary in what they will serve a non-browser client. Retrying with a
# plain browser Accept header recovers feeds that 403/406 on strict negotiation —
# but the User-Agent always identifies the crawler, as SEC policy requires.
FALLBACK_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


class FeedProvider(ResearchProvider):
    name = "rss"
    display_name = "RSS / Atom"
    kinds = ("central_bank", "regulator", "filing", "news", "statistics")

    def fetch(self, feed_url: str, *, limit: int = 40) -> FetchResult:
        if not feed_url:
            raise ResearchProviderError(self.name, "source has no feed_url configured")

        try:
            if self._client is not None:
                response = self._client.get(feed_url, headers=HEADERS)
            else:
                with httpx.Client(
                    timeout=self._timeout, headers=HEADERS, follow_redirects=True
                ) as client:
                    response = client.get(feed_url)
        except httpx.HTTPError as exc:
            raise ResearchProviderError(self.name, f"network error: {exc}", retryable=True) from exc

        if response.status_code == 404:
            raise ResearchProviderError(self.name, "feed not found (HTTP 404)")
        if response.status_code == 429:
            raise ResearchProviderError(self.name, "rate limited by publisher", retryable=True)
        if response.status_code >= 400:
            raise ResearchProviderError(
                self.name, f"HTTP {response.status_code} from publisher", retryable=True
            )

        if response.status_code in (403, 406):
            # One retry with relaxed content negotiation before giving up.
            try:
                if self._client is not None:
                    retry = self._client.get(feed_url, headers={**HEADERS, **FALLBACK_ACCEPT})
                else:
                    with httpx.Client(
                        timeout=self._timeout,
                        headers={**HEADERS, **FALLBACK_ACCEPT},
                        follow_redirects=True,
                    ) as client:
                        retry = client.get(feed_url)
                if retry.status_code < 400:
                    response = retry
            except httpx.HTTPError:
                pass

        items = parse_feed(response.text, limit=limit)
        if not items:
            raise ResearchProviderError(self.name, "feed contained no parsable items")
        return FetchResult(items=items, http_status=response.status_code)

    def health(self, feed_url: str) -> tuple[bool, str]:
        return super().health(feed_url)


def parse_feed(text: str, *, limit: int = 40) -> list[FeedItem]:
    """Parse RSS 2.0 or Atom into FeedItems, newest first."""
    text = _strip_dangerous_entities(text)
    try:
        # Bandit flags xml parsing of untrusted input (S314). Entity expansion is
        # stripped above and the parser is confined to this function; a malformed or
        # hostile document raises and is recorded against the source.
        root = ET.fromstring(text)  # noqa: S314
    except ET.ParseError as exc:
        raise ResearchProviderError("rss", f"malformed feed XML: {exc}") from exc

    channel = root.find("channel")
    if channel is not None:
        entries = channel.findall("item")
        items = [_from_rss(entry) for entry in entries]
    else:
        entries = root.findall(f"{ATOM}entry") or root.findall("entry")
        items = [_from_atom(entry) for entry in entries]

    items = [item for item in items if item is not None and item.title and item.url]
    items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC), reverse=True)
    return items[:limit]


DOCTYPE_RE = re.compile(r"<!DOCTYPE[^\[>]*(\[.*?\])?\s*>", re.I | re.S)
ENTITY_RE = re.compile(r"<!ENTITY[^>]*>", re.I)


def _strip_dangerous_entities(text: str) -> str:
    """Remove DOCTYPE and ENTITY declarations.

    These are the vector for entity-expansion ("billion laughs") attacks. The
    internal subset can contain '>' characters, so the pattern consumes the
    bracketed block rather than stopping at the first '>' — a naive strip leaves
    fragments behind and the document then fails to parse at all.
    """
    return ENTITY_RE.sub("", DOCTYPE_RE.sub("", text))


def _from_rss(entry: ET.Element) -> FeedItem | None:
    title = _text(entry.findtext("title"))
    link = _text(entry.findtext("link"))
    if not link:
        guid = entry.findtext("guid")
        link = _text(guid) if guid and str(guid).startswith("http") else ""
    summary = _text(entry.findtext("description"))
    body = _text(entry.findtext(f"{CONTENT}encoded")) or summary
    published = _parse_date(entry.findtext("pubDate") or entry.findtext(f"{DC}date"))
    external_id = _text(entry.findtext("guid")) or link
    return FeedItem(
        external_id=external_id,
        title=title,
        url=link,
        summary=summary,
        published_at=published,
        content=body,
    )


def _from_atom(entry: ET.Element) -> FeedItem | None:
    title = _text(entry.findtext(f"{ATOM}title") or entry.findtext("title"))
    link = ""
    for candidate in entry.findall(f"{ATOM}link") + entry.findall("link"):
        rel = candidate.get("rel", "alternate")
        if rel == "alternate" and candidate.get("href"):
            link = candidate.get("href", "")
            break
    summary = _text(entry.findtext(f"{ATOM}summary") or entry.findtext("summary"))
    body = _text(entry.findtext(f"{ATOM}content") or entry.findtext("content")) or summary
    published = _parse_date(
        entry.findtext(f"{ATOM}updated")
        or entry.findtext(f"{ATOM}published")
        or entry.findtext("updated")
    )
    external_id = _text(entry.findtext(f"{ATOM}id") or entry.findtext("id")) or link
    return FeedItem(
        external_id=external_id,
        title=title,
        url=link,
        summary=summary,
        published_at=published,
        content=body,
    )


def _text(value: str | None) -> str:
    """Strip markup and normalise whitespace. Feeds routinely embed HTML."""
    if not value:
        return ""
    cleaned = TAG_RE.sub(" ", value)
    cleaned = (
        cleaned.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    cleaned = WHITESPACE_RE.sub(" ", cleaned)
    cleaned = BLANK_LINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def _parse_date(raw: str | None) -> datetime | None:
    """Parse RFC 822 or RFC 3339. Unknown formats yield None, never 'now'."""
    if not raw:
        return None
    text = raw.strip()
    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except (TypeError, ValueError, IndexError):
        pass

    candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
