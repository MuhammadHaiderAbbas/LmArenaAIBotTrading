"""Research source adapter contract.

A source adapter does exactly one thing: given a configured feed, return a list of
`FeedItem`s. It never writes to the database, never makes decisions about
relevance, and never summarises. That separation means a new feed is a parser,
not a change to the pipeline.

Everything returned here is **untrusted input**. The pipeline sanitises it,
records what it found, and never executes anything a document says.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime


class ResearchProviderError(Exception):
    def __init__(self, provider: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.message = message
        self.retryable = retryable


@dataclass(slots=True)
class FeedItem:
    """One item as published by a source, before any processing."""

    external_id: str
    title: str
    url: str
    summary: str = ""
    published_at: datetime | None = None
    content: str = ""
    content_type: str = "text/html"


@dataclass(slots=True)
class FetchResult:
    items: list[FeedItem] = field(default_factory=list)
    feed_title: str = ""
    http_status: int = 200


class ResearchProvider(abc.ABC):
    """Base class for a research source adapter."""

    name: str
    display_name: str
    kinds: tuple[str, ...] = ()

    def __init__(self, timeout: float = 20.0, client=None) -> None:
        self._timeout = timeout
        self._client = client

    @abc.abstractmethod
    def fetch(self, feed_url: str, *, limit: int = 40) -> FetchResult:
        """Return the newest `limit` items from the feed."""

    def health(self, feed_url: str) -> tuple[bool, str]:
        try:
            result = self.fetch(feed_url, limit=1)
        except ResearchProviderError as exc:
            return False, exc.message
        if not result.items:
            return False, "feed returned no items"
        return True, f"reachable ({result.http_status})"
