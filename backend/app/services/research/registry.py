"""Research source registry.

Seeded declaratively and reconciled idempotently, exactly like the instrument
registry: a fresh database and an existing one converge on the same sources.

Every entry records licence and redistribution status. "Publicly reachable" is
not "licensed for redistribution", and the platform must be able to answer that
question per source without guessing. All Phase 4 sources are official
publishers whose feeds are published for syndication, and every one is stored as
`link_only` with `storage_permitted=False` unless noted: the platform keeps the
headline, the publisher's own summary and a link, and fetches the article body
only for on-demand analysis.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.research import SourceKind, SourceRecord, TrustTier

# code, name, publisher, kind, tier, feed, homepage, jurisdiction, licence, terms, redistribution
SOURCES: list[dict[str, object]] = [
    {
        "code": "ecb_press",
        "name": "ECB press releases",
        "publisher": "European Central Bank",
        "kind": SourceKind.CENTRAL_BANK,
        "trust_tier": TrustTier.PRIMARY,
        "feed_url": "https://www.ecb.europa.eu/rss/press.html",
        "homepage": "https://www.ecb.europa.eu/press/html/index.en.html",
        "jurisdiction": "EU",
        "licence": "ECB public website — reuse permitted with attribution",
        "terms_url": "https://www.ecb.europa.eu/services/using-utils/html/index.en.html",
        "redistribution": "link_only",
        "storage_permitted": False,
    },
    {
        "code": "boe_news",
        "name": "Bank of England news",
        "publisher": "Bank of England",
        "kind": SourceKind.CENTRAL_BANK,
        "trust_tier": TrustTier.PRIMARY,
        "feed_url": "https://www.bankofengland.co.uk/rss/news",
        "homepage": "https://www.bankofengland.co.uk/news",
        "jurisdiction": "UK",
        "licence": "Bank of England website terms",
        "terms_url": "https://www.bankofengland.co.uk/legal/terms-and-conditions",
        "redistribution": "link_only",
        "storage_permitted": False,
    },
    {
        "code": "fed_press",
        "name": "Federal Reserve press releases",
        "publisher": "Board of Governors of the Federal Reserve System",
        "kind": SourceKind.CENTRAL_BANK,
        "trust_tier": TrustTier.PRIMARY,
        "feed_url": "https://www.federalreserve.gov/feeds/press_all.xml",
        "homepage": "https://www.federalreserve.gov/newsevents.htm",
        "jurisdiction": "US",
        "licence": "US federal government work — public domain",
        "terms_url": "https://www.federalreserve.gov/website-linking-policy.htm",
        "redistribution": "excerpt",
        "storage_permitted": True,
    },
    {
        "code": "sec_edgar",
        "name": "SEC EDGAR filings",
        "publisher": "US Securities and Exchange Commission",
        "kind": SourceKind.FILING,
        "trust_tier": TrustTier.PRIMARY,
        # data.sec.gov is the documented programmatic endpoint; the legacy
        # browse-edgar atom feed returns 403 to non-browser clients.
        "feed_url": "https://data.sec.gov/submissions/CIK0000320193.json",
        "homepage": "https://www.sec.gov/edgar/searchedgar/companysearch",
        "jurisdiction": "US",
        "licence": "US federal government work — public domain",
        "terms_url": "https://www.sec.gov/os/accessing-edgar-data",
        "redistribution": "excerpt",
        "storage_permitted": True,
    },
    {
        "code": "dj_markets",
        "name": "Dow Jones markets news",
        "publisher": "Dow Jones & Company",
        "kind": SourceKind.NEWS,
        "trust_tier": TrustTier.COMMENTARY,
        "feed_url": "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
        "homepage": "https://www.wsj.com/news/markets",
        "jurisdiction": "US",
        "licence": "Dow Jones feed — headlines and links only",
        "terms_url": "https://www.dowjones.com/terms-of-use/",
        "redistribution": "link_only",
        "storage_permitted": False,
    },
    {
        "code": "bbc_business",
        "name": "BBC Business",
        "publisher": "BBC News",
        "kind": SourceKind.NEWS,
        "trust_tier": TrustTier.COMMENTARY,
        "feed_url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "homepage": "https://www.bbc.co.uk/news/business",
        "jurisdiction": "UK",
        "licence": "BBC content — BBC terms, link and short excerpt only",
        "terms_url": "https://www.bbc.co.uk/usingthebbc/terms/",
        "redistribution": "link_only",
        "storage_permitted": False,
    },
]


def seed_sources(db: Session) -> dict[str, int]:
    """Create or update the research source registry. Idempotent."""
    created = 0
    updated = 0
    for spec in SOURCES:
        existing = db.scalar(select(SourceRecord).where(SourceRecord.code == spec["code"]))
        if existing is None:
            db.add(SourceRecord(**spec))  # type: ignore[arg-type]
            created += 1
            continue
        # Registry metadata is owned by code; runtime health fields are not touched.
        for field, value in spec.items():
            if field == "code":
                continue
            setattr(existing, field, value)
        updated += 1
    db.flush()
    return {"created": created, "updated": updated, "total": len(SOURCES)}


def enabled_sources(db: Session) -> list[SourceRecord]:
    return list(
        db.scalars(
            select(SourceRecord)
            .where(SourceRecord.is_enabled.is_(True))
            .order_by(SourceRecord.trust_tier, SourceRecord.code)
        )
    )
