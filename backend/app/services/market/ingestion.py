"""Ingestion service: provider → validate → normalise → persist.

Responsibilities kept here (and nowhere else):
* provider selection with priority and fallback
* idempotent upsert of candles (unique on instrument + timeframe + open_time)
* data-quality evaluation, recorded as auditable events
* an `IngestionRun` audit row per provider call, so provenance is answerable
* one place where retry/backoff and rate limits are respected

Nothing here decides to trade. Ingestion only describes the world.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.providers.base import (
    MarketDataProvider,
    NormalisedBar,
    NormalisedQuote,
    ProviderError,
)
from app.integrations.providers.frankfurter import FrankfurterProvider
from app.integrations.providers.kraken import KrakenProvider
from app.integrations.providers.twelvedata import TwelveDataProvider
from app.integrations.providers.yahoo import YahooProvider
from app.models.market import (
    DataQualityEvent,
    IngestionRun,
    Instrument,
    InstrumentProvider,
    MarketCandle,
    MarketQuote,
    QualitySeverity,
    Timeframe,
)
from app.services.market import quality

PROVIDERS: dict[str, MarketDataProvider] = {
    "kraken": KrakenProvider(),
    "yahoo": YahooProvider(),
    "frankfurter": FrankfurterProvider(),
    "twelvedata": TwelveDataProvider(),
}


def get_provider(name: str) -> MarketDataProvider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise ProviderError(name, f"provider '{name}' is not registered")
    return provider



@dataclass(slots=True)
class SyncResult:
    instrument: str
    provider: str
    timeframe: Timeframe
    bars_written: int = 0
    bars_skipped: int = 0
    findings: int = 0
    error: str | None = None
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


# ---------------------------------------------------------------------------
# Provider routing
# ---------------------------------------------------------------------------
def provider_candidates(db: Session, instrument: Instrument, timeframe: Timeframe) -> list[tuple[str, str]]:
    """Ordered (provider, provider_symbol) pairs that can serve this timeframe."""
    mappings = sorted(instrument.providers, key=lambda mapping: mapping.priority)
    candidates: list[tuple[str, str]] = []
    for mapping in mappings:
        if not mapping.is_enabled:
            continue
        # Only route to providers we actually have a parser for. An unknown
        # provider identifier is skipped, never guessed at: silently treating an
        # unrecognised symbol as a ticker is how bad data enters a system.
        if mapping.provider not in PROVIDERS:
            continue
        provider = PROVIDERS.get(mapping.provider)
        if provider is None or not provider.supports(timeframe):
            continue
        candidates.append((mapping.provider, mapping.provider_symbol))
    return candidates


def _record_event(
    db: Session,
    *,
    instrument: Instrument | None,
    provider: str,
    finding: quality.Finding,
    timeframe: Timeframe | None,
) -> None:
    db.add(
        DataQualityEvent(
            instrument_id=instrument.id if instrument else None,
            provider=provider,
            event_type=finding.event_type,
            severity=finding.severity,
            timeframe=timeframe,
            message=finding.message[:400],
            detail=finding.detail,
            window_start=finding.window_start,
            window_end=finding.window_end,
        )
    )


# ---------------------------------------------------------------------------
# Candles
# ---------------------------------------------------------------------------
def sync_candles(
    db: Session,
    instrument: Instrument,
    timeframe: Timeframe,
    *,
    limit: int = 300,
    since: datetime | None = None,
) -> SyncResult:
    """Fetch, validate and upsert candles for one instrument/timeframe."""
    candidates = provider_candidates(db, instrument, timeframe)
    if not candidates:
        return SyncResult(
            instrument=instrument.symbol,
            provider="none",
            timeframe=timeframe,
            error=f"no provider supports {timeframe.value} for {instrument.symbol}",
        )

    errors: list[str] = []
    for provider_name, provider_symbol in candidates:
        provider = PROVIDERS[provider_name]
        started = time.monotonic()
        run = IngestionRun(
            provider=provider_name,
            kind="candles",
            status="running",
            symbols=f"{instrument.symbol}:{timeframe.value}",
            started_at=datetime.now(UTC),
        )
        db.add(run)
        db.flush()

        try:
            kwargs: dict[str, object] = {"limit": limit}
            if since is not None:
                kwargs["since"] = since
            # Providers that quote in a different unit (London pence) need the
            # instrument's scale applied at the boundary, not in the database.
            if provider_name in ("yahoo", "twelvedata"):
                kwargs["price_scale"] = float(instrument.price_scale)
            bars = provider.fetch_ohlc(provider_symbol, timeframe, **kwargs)  # type: ignore[call-arg]
        except ProviderError as exc:
            run.status = "failed"
            run.error = exc.message[:1000]
            run.duration_ms = int((time.monotonic() - started) * 1000)
            run.finished_at = datetime.now(UTC)
            _record_event(
                db,
                instrument=instrument,
                provider=provider_name,
                finding=quality.Finding(
                    quality.QualityEventType.PROVIDER_ERROR,
                    QualitySeverity.WARNING,
                    f"{provider_name} failed for {instrument.symbol}: {exc.message}",
                ),
                timeframe=timeframe,
            )
            db.flush()
            errors.append(f"{provider_name}: {exc.message}")
            continue

        findings = quality.check_bars(
            bars,
            timeframe=timeframe,
            venue=instrument.venue,
            expected_volume=instrument.asset_class.value != "forex",
        )
        for finding in findings:
            _record_event(
                db, instrument=instrument, provider=provider_name, finding=finding, timeframe=timeframe
            )

        written, skipped = _upsert_bars(
            db,
            instrument=instrument,
            timeframe=timeframe,
            bars=bars,
            provider=provider_name,
        )

        run.status = "success"
        run.rows_written = written
        run.rows_skipped = skipped
        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.finished_at = datetime.now(UTC)

        mapping = db.scalar(
            select(InstrumentProvider).where(
                InstrumentProvider.instrument_id == instrument.id,
                InstrumentProvider.provider == provider_name,
            )
        )
        if mapping is not None:
            mapping.last_success_at = datetime.now(UTC)
            mapping.last_error = ""

        db.flush()
        return SyncResult(
            instrument=instrument.symbol,
            provider=provider_name,
            timeframe=timeframe,
            bars_written=written,
            bars_skipped=skipped,
            findings=len(findings),
            duration_ms=run.duration_ms,
        )

    return SyncResult(
        instrument=instrument.symbol,
        provider=",".join(name for name, _ in candidates),
        timeframe=timeframe,
        error="; ".join(errors) or "all providers failed",
    )


def _upsert_bars(
    db: Session,
    *,
    instrument: Instrument,
    timeframe: Timeframe,
    bars: list[NormalisedBar],
    provider: str,
) -> tuple[int, int]:
    if not bars:
        return 0, 0

    # Read every bar already stored for this window, then flush so the session
    # sees them: two syncs can overlap and must not both insert the same bar.
    db.flush()
    existing = {
        row.open_time: row
        for row in db.scalars(
            select(MarketCandle).where(
                MarketCandle.instrument_id == instrument.id,
                MarketCandle.timeframe == timeframe,
                MarketCandle.open_time >= bars[0].open_time,
            )
        )
    }

    now = datetime.now(UTC)
    written = 0
    skipped = 0
    step = timedelta(seconds=timeframe.seconds)

    # D1/W1 bars arrive stamped at venue-local midnight, but SQLite/Postgres round-
    # trip them as naive datetimes. Normalise both sides to UTC-naive so the
    # in-memory key matches what the database holds — otherwise every sync re-inserts
    # the same bar and trips the unique constraint.
    def _key(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    existing = {_key(open_time): row for open_time, row in existing.items()}

    for bar in bars:
        row = existing.get(_key(bar.open_time))
        if row is None:
            row = MarketCandle(
                    instrument_id=instrument.id,
                    timeframe=timeframe,
                    open_time=bar.open_time,
                    close_time=bar.open_time + step,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    vwap=bar.vwap,
                    volume=bar.volume,
                    trades=bar.trades,
                    provider=provider,
                    is_final=bar.is_final,
                ingested_at=now,
            )
            db.add(row)
            existing[_key(bar.open_time)] = row
            written += 1
        elif not row.is_final:
            # A forming bar may legitimately change (and its provider may change).
            row.open = bar.open
            row.high = bar.high
            row.low = bar.low
            row.close = bar.close
            row.vwap = bar.vwap
            row.volume = bar.volume
            row.trades = bar.trades
            row.provider = provider
            row.is_final = bar.is_final
            row.ingested_at = now
            written += 1
        else:
            skipped += 1  # closed bar already stored: never overwrite history

    db.flush()
    return written, skipped


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------
def sync_quote(db: Session, instrument: Instrument) -> SyncResult:
    candidates = provider_candidates(db, instrument, Timeframe.D1)
    if not candidates:
        return SyncResult(
            instrument=instrument.symbol,
            provider="none",
            timeframe=Timeframe.D1,
            error="no provider configured",
        )

    errors: list[str] = []
    for provider_name, provider_symbol in candidates:
        provider = PROVIDERS[provider_name]
        started = time.monotonic()
        try:
            quote: NormalisedQuote = provider.fetch_quote(provider_symbol)
        except ProviderError as exc:
            errors.append(f"{provider_name}: {exc.message}")
            _record_event(
                db,
                instrument=instrument,
                provider=provider_name,
                finding=quality.Finding(
                    quality.QualityEventType.PROVIDER_ERROR,
                    QualitySeverity.INFO,
                    f"quote unavailable from {provider_name}: {exc.message}",
                ),
                timeframe=None,
            )
            db.flush()
            continue

        scale = float(instrument.price_scale)
        received = datetime.now(UTC)
        row = db.scalar(
            select(MarketQuote).where(MarketQuote.instrument_id == instrument.id)
        )
        if row is None:
            row = MarketQuote(instrument_id=instrument.id)
            db.add(row)

        row.bid = quote.bid * scale if quote.bid is not None else None
        row.ask = quote.ask * scale if quote.ask is not None else None
        row.mid = quote.mid * scale if quote.mid is not None else None
        row.last = quote.last * scale
        row.open_24h = quote.open_24h * scale if quote.open_24h is not None else None
        row.change_24h_pct = quote.change_24h_pct
        row.volume_24h = quote.volume_24h
        row.provider = provider_name
        row.observed_at = quote.observed_at
        row.received_at = received

        for finding in quality.check_quote(
            row.last, quote.observed_at, venue=instrument.venue
        ):
            _record_event(
                db, instrument=instrument, provider=provider_name, finding=finding, timeframe=None
            )

        db.flush()
        return SyncResult(
            instrument=instrument.symbol,
            provider=provider_name,
            timeframe=Timeframe.D1,
            bars_written=1,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    return SyncResult(
        instrument=instrument.symbol,
        provider=",".join(name for name, _ in candidates),
        timeframe=Timeframe.D1,
        error="; ".join(errors) or "all providers failed",
    )


def sync_many(
    db: Session,
    instruments: list[Instrument],
    timeframe: Timeframe,
    *,
    limit: int = 300,
    with_quotes: bool = True,
) -> list[SyncResult]:
    results = [sync_candles(db, instrument, timeframe, limit=limit) for instrument in instruments]
    if with_quotes:
        results.extend(sync_quote(db, instrument) for instrument in instruments)
    return results
