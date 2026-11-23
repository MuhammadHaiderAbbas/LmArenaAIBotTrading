"""SEC EDGAR adapter using the JSON submissions API.

Why not the RSS feed
--------------------
`www.sec.gov/cgi-bin/browse-edgar?...&output=atom` returns 403 to non-browser
clients regardless of a compliant User-Agent. `data.sec.gov` serves the same
information as JSON, keyed by CIK, and is the endpoint SEC documents for
programmatic access.

SEC access policy requires a declared contact in the User-Agent; requests without
one are blocked. `SEC_USER_AGENT` supplies it. When it is absent the provider
reports itself unhealthy rather than quietly hammering an endpoint that will
never answer.

Only filing metadata is read here — accession number, form type, filing date and
document URL. The filing body itself is a multi-megabyte document and is fetched
on demand, not in bulk.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from app.integrations.research.base import (
    FeedItem,
    FetchResult,
    ResearchProvider,
    ResearchProviderError,
)

BASE_URL = "https://data.sec.gov"
DEFAULT_UA = "AUREX-Research/0.1 (research platform; contact via project repository)"

# Forms worth reading for an equity research pipeline. 10-K/10-Q are the periodic
# reports; 8-K is material events; the rest are ownership and offering notices.
RELEVANT_FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K", "S-1", "DEF 14A"}


class SecEdgarProvider(ResearchProvider):
    name = "sec_edgar"
    display_name = "SEC EDGAR"
    kinds = ("filing",)

    def __init__(self, timeout: float = 20.0, client=None) -> None:
        super().__init__(timeout=timeout, client=client)
        self._user_agent = os.getenv("SEC_USER_AGENT", DEFAULT_UA)

    def fetch(self, feed_url: str, *, limit: int = 40) -> FetchResult:
        cik = _cik_from(feed_url)
        url = f"{BASE_URL}/submissions/CIK{cik}.json"
        headers = {"User-Agent": self._user_agent, "Accept": "application/json"}

        try:
            if self._client is not None:
                response = self._client.get(url, headers=headers)
            else:
                with httpx.Client(timeout=self._timeout, headers=headers) as client:
                    response = client.get(url)
        except httpx.HTTPError as exc:
            raise ResearchProviderError(self.name, f"network error: {exc}", retryable=True) from exc

        if response.status_code in (403, 401):
            raise ResearchProviderError(
                self.name,
                "SEC refused the request — set SEC_USER_AGENT to a value containing a contact address",
            )
        if response.status_code == 404:
            raise ResearchProviderError(self.name, f"no such CIK: {cik}")
        if response.status_code >= 400:
            raise ResearchProviderError(
                self.name, f"HTTP {response.status_code} from data.sec.gov", retryable=True
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ResearchProviderError(self.name, "malformed JSON from SEC") from exc

        company = payload.get("name", "")
        recent = payload.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])

        items: list[FeedItem] = []
        for index, form in enumerate(forms):
            if len(items) >= limit:
                break
            if form not in RELEVANT_FORMS:
                continue
            accession = accessions[index] if index < len(accessions) else ""
            document = primary[index] if index < len(primary) else ""
            filing_date = dates[index] if index < len(dates) else ""
            description = descriptions[index] if index < len(descriptions) else ""

            accession_path = accession.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
                f"{accession_path}/{document}"
                if document
                else f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
            )
            published = _parse_day(filing_date)
            summary = (
                f"{company} filed form {form} on {filing_date}. "
                f"{description or 'Primary document available at the source.'}"
            ).strip()

            items.append(
                FeedItem(
                    external_id=accession or url,
                    title=f"{company} — {form} ({filing_date})",
                    url=url,
                    summary=summary,
                    published_at=published,
                    content="",
                )
            )

        if not items:
            raise ResearchProviderError(self.name, "no relevant filings in the recent window")
        return FetchResult(items=items, feed_title=company, http_status=response.status_code)

    def health(self, feed_url: str) -> tuple[bool, str]:
        try:
            result = self.fetch(feed_url, limit=1)
        except ResearchProviderError as exc:
            return False, exc.message
        return True, f"reachable ({result.http_status})"


def _cik_from(feed_url: str) -> str:
    """Extract a zero-padded 10-digit CIK from the configured source URL.

    Accepts `CIK=0000320193`, a bare number, or an EDGAR URL with the CIK in the
    path, so the registry entry stays readable.
    """
    import re

    match = re.search(r"CIK=(\d{1,10})", feed_url or "", re.I)
    if match:
        return match.group(1).zfill(10)
    match = re.search(r"/data/(\d{1,10})/", feed_url or "")
    if match:
        return match.group(1).zfill(10)
    match = re.search(r"\b(\d{6,10})\b", feed_url or "")
    if match:
        return match.group(1).zfill(10)
    return "0000320193"  # Apple Inc., the default registry entry


def _parse_day(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None
