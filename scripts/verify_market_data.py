"""End-to-end verification against the real providers.

Run from the backend directory:

    PYTHONPATH=backend python scripts/verify_market_data.py

Confirms each provider is reachable, ingests real bars into a throwaway SQLite
database, and reports the data-quality findings it produced. This is the script
referenced by the Phase 3 docs; CI uses stubbed providers instead so builds never
depend on a third party being up.
"""

from __future__ import annotations

import os
import tempfile

DB_PATH = os.path.join(tempfile.mkdtemp(prefix="aurex-verify-"), "market.db")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{DB_PATH}")

from sqlalchemy import select  # noqa: E402

import app.models  # noqa: E402,F401
from app.database.session import Base, SessionLocal, engine  # noqa: E402
from app.models import (  # noqa: E402
    DataQualityEvent,
    IngestionRun,
    Instrument,
    MarketCandle,
    MarketQuote,
    Timeframe,
)
from app.services.market import ingestion, registry  # noqa: E402

TARGETS = [
    ("BTC/USD", Timeframe.H1, 60),
    ("AAPL", Timeframe.D1, 60),
    ("SHEL.L", Timeframe.D1, 60),
    ("GBP/USD", Timeframe.D1, 60),
]


def main() -> int:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        created = registry.seed_registry(db)
        db.commit()
        print(f"registry: {created}")

        print("\nprovider health")
        for name, provider in ingestion.PROVIDERS.items():
            healthy, message = provider.health()
            print(f"  {name:12s} {'OK  ' if healthy else 'FAIL'} {message}")

        print("\ncandles + quotes")
        failures = 0
        for symbol, timeframe, limit in TARGETS:
            instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
            result = ingestion.sync_candles(db, instrument, timeframe, limit=limit)
            quote = ingestion.sync_quote(db, instrument)
            db.commit()
            status = "ok" if result.ok else "FAILED"
            print(
                f"  {symbol:8s} {timeframe.value:3s} {status:6s} "
                f"provider={result.provider:12s} written={result.bars_written:3d} "
                f"skipped={result.bars_skipped:3d} findings={result.findings}"
            )
            if quote.ok:
                print(f"           quote via {quote.provider}")
            else:
                print(f"           quote FAILED: {quote.error}")
            failures += 0 if result.ok else 1

        print("\ndatabase")
        print(f"  candles        : {db.query(MarketCandle).count()}")
        print(f"  quotes         : {db.query(MarketQuote).count()}")
        print(f"  quality events : {db.query(DataQualityEvent).count()}")
        print(f"  ingestion runs : {db.query(IngestionRun).count()}")

        events = db.scalars(
            select(DataQualityEvent).order_by(DataQualityEvent.created_at.desc()).limit(6)
        )
        if any(True for _ in events):
            print("\nrecent findings")
            for event in db.scalars(
                select(DataQualityEvent).order_by(DataQualityEvent.created_at.desc()).limit(6)
            ):
                print(
                    f"  [{event.severity.value:8s}] {event.event_type.value:14s} "
                    f"{event.provider:11s} {event.message[:70]}"
                )

        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
