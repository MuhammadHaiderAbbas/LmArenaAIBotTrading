"""Market-data endpoints: registry, screener, candles, quality and ingestion.

Read endpoints are available to any authenticated user. Ingestion is an
operator action and is therefore restricted to admin/analyst roles, audited, and
rate limited so a client cannot exhaust a provider quota.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, StaffUser
from app.core.audit import record_audit, record_system_event
from app.core.rate_limit import enforce_rate_limit
from app.models.market import (
    AssetClass,
    DataQualityEvent,
    IngestionRun,
    Instrument,
    InstrumentProvider,
    MarketCandle,
    MarketQuote,
    QualityEventType,
    QualitySeverity,
    Timeframe,
)
from app.schemas.market import (
    CandleRead,
    CandleSeries,
    IndicatorRead,
    IngestionRunRead,
    InstrumentRead,
    MarketRow,
    ProviderHealth,
    QualityEventRead,
    QualitySummary,
    QuoteRead,
    SessionRead,
    SyncRequest,
    SyncResponse,
    SyncResultRead,
    VenueRead,
)
from app.services.errors import DomainError
from app.services.market import indicators as indicator_service
from app.services.market import ingestion, registry, sessions

router = APIRouter(prefix="/markets", tags=["markets"])

TIMEFRAME_LABEL = {tf: tf.value for tf in Timeframe}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _instrument_read(instrument: Instrument) -> InstrumentRead:
    # Construct explicitly: `providers` holds ORM objects on the relationship, but
    # the API contract exposes provider *names*.
    return InstrumentRead(
        id=instrument.id,
        symbol=instrument.symbol,
        name=instrument.name,
        asset_class=instrument.asset_class,
        base_currency=instrument.base_currency,
        quote_currency=instrument.quote_currency,
        price_scale=float(instrument.price_scale),
        tick_size=float(instrument.tick_size),
        lot_size=float(instrument.lot_size),
        status=instrument.status,
        is_tradable=instrument.is_tradable,
        description=instrument.description,
        venue=VenueRead.model_validate(instrument.venue),
        providers=sorted(mapping.provider for mapping in instrument.providers),
    )


def _session_read(instrument: Instrument, at: datetime | None = None) -> SessionRead:
    venue = instrument.venue
    state = sessions.venue_state(venue, at)
    moment = (at or datetime.now(UTC)).astimezone(ZoneInfo(venue.timezone))
    return SessionRead(
        venue=venue.code,
        state=state,
        timezone=venue.timezone,
        local_time=moment.strftime("%H:%M"),
        is_open=state.value in ("open", "always_on"),
        session_open=venue.session_open,
        session_close=venue.session_close,
    )


def _quote_read(quote: MarketQuote | None) -> QuoteRead | None:
    if quote is None:
        return None
    spread_bps = None
    if quote.bid and quote.ask and quote.mid:
        spread_bps = round((quote.ask - quote.bid) / quote.mid * 10_000, 3)
    return QuoteRead(
        bid=float(quote.bid) if quote.bid is not None else None,
        ask=float(quote.ask) if quote.ask is not None else None,
        mid=float(quote.mid) if quote.mid is not None else None,
        last=float(quote.last),
        open_24h=float(quote.open_24h) if quote.open_24h is not None else None,
        change_24h_pct=float(quote.change_24h_pct) if quote.change_24h_pct is not None else None,
        volume_24h=float(quote.volume_24h) if quote.volume_24h is not None else None,
        spread_bps=spread_bps,
        provider=quote.provider,
        observed_at=quote.observed_at,
        received_at=quote.received_at,
        age_seconds=round(
            (datetime.now(UTC) - quote.observed_at.astimezone(UTC)).total_seconds(), 1
        ),
    )


def _load_candles(
    db: DbSession, instrument_id: str, timeframe: Timeframe, limit: int
) -> list[MarketCandle]:
    rows = list(
        db.scalars(
            select(MarketCandle)
            .where(
                MarketCandle.instrument_id == instrument_id,
                MarketCandle.timeframe == timeframe,
            )
            .order_by(MarketCandle.open_time.desc())
            .limit(limit)
        )
    )
    return list(reversed(rows))


def _indicator_read(rows: list[MarketCandle]) -> IndicatorRead:
    if not rows:
        return IndicatorRead()
    snapshot = indicator_service.snapshot(
        closes=[float(row.close) for row in rows],
        highs=[float(row.high) for row in rows],
        lows=[float(row.low) for row in rows],
    )
    return IndicatorRead(
        **{field: getattr(snapshot, field) for field in IndicatorRead.model_fields}
    )


def _quality_state(db: DbSession, instrument_id: str) -> str:
    worst = db.scalar(
        select(DataQualityEvent.severity)
        .where(
            DataQualityEvent.instrument_id == instrument_id,
            DataQualityEvent.resolved.is_(False),
        )
        .order_by(
            # critical > warning > info via simple CASE ordering
            func.coalesce(DataQualityEvent.severity, QualitySeverity.INFO).desc()
        )
        .limit(1)
    )
    if worst is None:
        return "ok"
    value = worst.value if hasattr(worst, "value") else str(worst)
    return {"critical": "critical", "warning": "warning", "info": "ok"}.get(value, "ok")


def _get_instrument(db: DbSession, symbol: str) -> Instrument:
    instrument = db.scalar(select(Instrument).where(Instrument.symbol == symbol.upper()))
    if instrument is None:
        # Accept the slash-less form (BTCUSD) as a convenience.
        instrument = db.scalar(
            select(Instrument).where(
                func.replace(Instrument.symbol, "/", "") == symbol.upper().replace("/", "")
            )
        )
    if instrument is None:
        raise DomainError(f"Instrument '{symbol}' is not registered.", status_code=404)
    return instrument


# ---------------------------------------------------------------------------
# registry & sessions
# ---------------------------------------------------------------------------
@router.get("/venues", response_model=list[VenueRead])
def list_venues(db: DbSession, _: CurrentUser) -> list[VenueRead]:
    from app.models.market import Venue

    return [VenueRead.model_validate(venue) for venue in db.scalars(select(Venue))]


@router.get("/instruments", response_model=list[InstrumentRead])
def list_instruments(
    db: DbSession,
    _: CurrentUser,
    asset_class: AssetClass | None = Query(default=None),
    tradable_only: bool = Query(default=False),
) -> list[InstrumentRead]:
    stmt = select(Instrument).order_by(Instrument.asset_class, Instrument.symbol)
    if asset_class is not None:
        stmt = stmt.where(Instrument.asset_class == asset_class)
    if tradable_only:
        stmt = stmt.where(Instrument.is_tradable.is_(True))
    return [_instrument_read(instrument) for instrument in db.scalars(stmt)]


@router.get("/instruments/{symbol}", response_model=InstrumentRead)
def get_instrument(symbol: str, db: DbSession, _: CurrentUser) -> InstrumentRead:
    return _instrument_read(_get_instrument(db, symbol))


# ---------------------------------------------------------------------------
# screener & candles
# ---------------------------------------------------------------------------
@router.get("/overview", response_model=list[MarketRow])
def market_overview(
    db: DbSession,
    _: CurrentUser,
    asset_class: AssetClass | None = Query(default=None),
    limit: int = Query(default=40, ge=1, le=100),
) -> list[MarketRow]:
    """Screener rows: quote, session state, indicators and a 30-bar sparkline."""
    stmt = select(Instrument).order_by(Instrument.asset_class, Instrument.symbol).limit(limit)
    if asset_class is not None:
        stmt = stmt.where(Instrument.asset_class == asset_class)

    rows: list[MarketRow] = []
    for instrument in db.scalars(stmt):
        quote = db.scalar(select(MarketQuote).where(MarketQuote.instrument_id == instrument.id))
        daily = _load_candles(db, instrument.id, Timeframe.D1, 40)
        rows.append(
            MarketRow(
                instrument=_instrument_read(instrument),
                session=_session_read(instrument),
                quote=_quote_read(quote),
                indicators=_indicator_read(daily) if daily else None,
                sparkline=[round(float(candle.close), 6) for candle in daily[-30:]],
                data_quality=_quality_state(db, instrument.id),
            )
        )
    return rows


@router.get("/{symbol:path}/candles", response_model=CandleSeries)
def get_candles(
    symbol: str,
    db: DbSession,
    _: CurrentUser,
    timeframe: Timeframe = Query(default=Timeframe.D1),
    limit: int = Query(default=300, ge=10, le=1000),
) -> CandleSeries:
    instrument = _get_instrument(db, symbol)
    rows = _load_candles(db, instrument.id, timeframe, limit)
    open_events = (
        db.scalar(
            select(func.count())
            .select_from(DataQualityEvent)
            .where(
                DataQualityEvent.instrument_id == instrument.id,
                DataQualityEvent.timeframe == timeframe,
                DataQualityEvent.resolved.is_(False),
            )
        )
        or 0
    )

    is_stale = False
    if rows:
        lag = sessions.staleness_seconds(
            instrument.venue,
            rows[-1].close_time,
            timeframe.seconds,
        )
        is_stale = lag is not None and lag > timeframe.seconds + 30

    return CandleSeries(
        instrument=_instrument_read(instrument),
        timeframe=timeframe,
        candles=[
            CandleRead(
                open_time=row.open_time,
                close_time=row.close_time,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=float(row.volume),
                vwap=float(row.vwap) if row.vwap is not None else None,
                is_final=row.is_final,
                provider=row.provider,
            )
            for row in rows
        ],
        indicators=_indicator_read(rows),
        session=_session_read(instrument),
        quality_events=int(open_events),
        is_stale=is_stale,
    )


@router.get("/{symbol:path}/indicators", response_model=IndicatorRead)
def get_indicators(
    symbol: str,
    db: DbSession,
    _: CurrentUser,
    timeframe: Timeframe = Query(default=Timeframe.D1),
) -> IndicatorRead:
    instrument = _get_instrument(db, symbol)
    return _indicator_read(_load_candles(db, instrument.id, timeframe, 260))


# ---------------------------------------------------------------------------
# data quality & provenance
# ---------------------------------------------------------------------------
@router.get("/quality/summary", response_model=QualitySummary)
def quality_summary(db: DbSession, _: CurrentUser) -> QualitySummary:
    open_events = list(
        db.scalars(
            select(DataQualityEvent)
            .where(DataQualityEvent.resolved.is_(False))
            .order_by(DataQualityEvent.created_at.desc())
        )
    )
    latest = [QualityEventRead.model_validate(event) for event in open_events[:20]]

    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    stale_ids: set[str] = set()
    for event in open_events:
        by_severity[event.severity.value] = by_severity.get(event.severity.value, 0) + 1
        by_type[event.event_type.value] = by_type.get(event.event_type.value, 0) + 1
        if event.event_type == QualityEventType.STALE and event.instrument_id:
            stale_ids.add(event.instrument_id)

    symbols: list[str] = []
    if stale_ids:
        symbols = list(db.scalars(select(Instrument.symbol).where(Instrument.id.in_(stale_ids))))

    return QualitySummary(
        total_open=len(open_events),
        by_severity=by_severity,
        by_type=by_type,
        stale_instruments=symbols,
        latest=latest,
    )


@router.get("/quality/events", response_model=list[QualityEventRead])
def quality_events(
    db: DbSession,
    _: CurrentUser,
    symbol: str | None = Query(default=None),
    severity: QualitySeverity | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[QualityEventRead]:
    stmt = select(DataQualityEvent).order_by(DataQualityEvent.created_at.desc()).limit(limit)
    if symbol:
        instrument = _get_instrument(db, symbol)
        stmt = stmt.where(DataQualityEvent.instrument_id == instrument.id)
    if severity is not None:
        stmt = stmt.where(DataQualityEvent.severity == severity)
    return [QualityEventRead.model_validate(event) for event in db.scalars(stmt)]


@router.get("/providers", response_model=list[ProviderHealth])
def provider_health(db: DbSession, _: CurrentUser) -> list[ProviderHealth]:
    results: list[ProviderHealth] = []
    for name, provider in ingestion.PROVIDERS.items():
        healthy, message = provider.health()
        covered = (
            db.scalar(
                select(func.count())
                .select_from(InstrumentProvider)
                .where(InstrumentProvider.provider == name)
            )
            or 0
        )
        results.append(
            ProviderHealth(
                provider=name,
                display_name=provider.display_name,
                healthy=healthy,
                message=message,
                asset_classes=list(provider.supported_asset_classes),
                timeframes=[tf.value for tf in provider.supported_timeframes],
                instruments_covered=int(covered),
            )
        )
    return results


@router.get("/ingestion-runs", response_model=list[IngestionRunRead])
def ingestion_runs(
    db: DbSession,
    _: CurrentUser,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[IngestionRunRead]:
    runs = db.scalars(select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit))
    return [IngestionRunRead.model_validate(run) for run in runs]


# ---------------------------------------------------------------------------
# ingestion (operator action)
# ---------------------------------------------------------------------------
@router.post("/sync", response_model=SyncResponse)
def sync_market_data(
    payload: SyncRequest,
    request: Request,
    db: DbSession,
    user: StaffUser,
) -> SyncResponse:
    """Fetch fresh data from providers. Restricted to admin/analyst roles."""
    enforce_rate_limit(request, "market-sync", limit=6, window_seconds=60)
    started = time.monotonic()

    stmt = select(Instrument)
    if payload.symbols:
        wanted = {symbol.upper().replace("/", "") for symbol in payload.symbols}
        instruments = [
            instrument
            for instrument in db.scalars(stmt)
            if instrument.symbol.upper().replace("/", "") in wanted
        ]
        unknown = wanted - {
            instrument.symbol.upper().replace("/", "") for instrument in instruments
        }
        if unknown:
            raise DomainError(f"Unknown instruments: {', '.join(sorted(unknown))}", status_code=400)
    else:
        if payload.asset_classes:
            stmt = stmt.where(Instrument.asset_class.in_(payload.asset_classes))
        instruments = list(db.scalars(stmt))

    if not instruments:
        raise DomainError("No instruments matched the request.", status_code=400)

    results = ingestion.sync_many(
        db,
        instruments,
        payload.timeframe,
        limit=payload.limit,
        with_quotes=payload.with_quotes,
    )
    db.commit()

    succeeded = sum(1 for result in results if result.ok)
    failed = len(results) - succeeded
    duration = int((time.monotonic() - started) * 1000)

    record_audit(
        db,
        action="market.sync",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="market_data",
        entity_id=payload.timeframe.value,
        outcome="success" if failed == 0 else "partial",
        detail=f"{succeeded} ok / {failed} failed across {len(instruments)} instruments",
        request=request,
    )
    record_system_event(
        db,
        category="market_data",
        message=f"Ingestion run: {succeeded} succeeded, {failed} failed",
        severity="warning" if failed else "info",
        detail=", ".join(f"{r.instrument}:{r.provider}" for r in results if not r.ok)[:1000],
    )
    db.commit()

    return SyncResponse(
        results=[
            SyncResultRead(
                instrument=result.instrument,
                provider=result.provider,
                timeframe=result.timeframe,
                bars_written=result.bars_written,
                bars_skipped=result.bars_skipped,
                findings=result.findings,
                duration_ms=result.duration_ms,
                ok=result.ok,
                error=result.error,
            )
            for result in results
        ],
        succeeded=succeeded,
        failed=failed,
        duration_ms=duration,
    )


@router.post("/registry/seed", response_model=dict)
def reseed_registry(request: Request, db: DbSession, user: StaffUser) -> dict[str, int]:
    """Re-apply the declarative instrument registry (idempotent)."""
    created = registry.seed_registry(db)
    record_audit(
        db,
        action="market.registry_seed",
        actor_id=user.id,
        actor_email=user.email,
        entity_type="market_data",
        detail=str(created),
        request=request,
    )
    db.commit()
    return created
