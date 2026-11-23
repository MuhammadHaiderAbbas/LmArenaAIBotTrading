"""Pydantic contracts for the market-data API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.market import (
    AssetClass,
    InstrumentStatus,
    QualityEventType,
    QualitySeverity,
    SessionState,
    Timeframe,
)


class VenueRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    asset_class: AssetClass
    timezone: str
    session_open: str | None
    session_close: str | None
    is_24_7: bool


class SessionRead(BaseModel):
    venue: str
    state: SessionState
    timezone: str
    local_time: str
    is_open: bool
    session_open: str | None = None
    session_close: str | None = None


class QuoteRead(BaseModel):
    bid: float | None = None
    ask: float | None = None
    mid: float | None = None
    last: float
    open_24h: float | None = None
    change_24h_pct: float | None = None
    volume_24h: float | None = None
    spread_bps: float | None = None
    provider: str
    observed_at: datetime
    received_at: datetime
    age_seconds: float


class CandleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    is_final: bool
    provider: str


class IndicatorRead(BaseModel):
    sma_20: float | None = None
    sma_50: float | None = None
    ema_20: float | None = None
    rsi_14: float | None = None
    macd: float | None = None
    macd_signal: float | None = None
    macd_histogram: float | None = None
    atr_14: float | None = None
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    volatility_20: float | None = None
    change_pct_1: float | None = None
    change_pct_5: float | None = None
    change_pct_20: float | None = None


class InstrumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    symbol: str
    name: str
    asset_class: AssetClass
    base_currency: str
    quote_currency: str
    price_scale: float
    tick_size: float
    lot_size: float
    status: InstrumentStatus
    is_tradable: bool
    description: str
    venue: VenueRead
    providers: list[str] = []


class MarketRow(BaseModel):
    """One row of the market screener."""

    instrument: InstrumentRead
    session: SessionRead
    quote: QuoteRead | None = None
    indicators: IndicatorRead | None = None
    sparkline: list[float] = []
    data_quality: str = "unknown"  # ok | warning | critical | no_data


class CandleSeries(BaseModel):
    instrument: InstrumentRead
    timeframe: Timeframe
    candles: list[CandleRead]
    indicators: IndicatorRead
    session: SessionRead
    quality_events: int
    is_stale: bool


class QualityEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    event_type: QualityEventType
    severity: QualitySeverity
    timeframe: Timeframe | None
    message: str
    detail: str
    window_start: datetime | None
    window_end: datetime | None
    resolved: bool
    created_at: datetime


class IngestionRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    provider: str
    kind: str
    status: str
    symbols: str
    rows_written: int
    rows_skipped: int
    duration_ms: int
    error: str
    started_at: datetime
    finished_at: datetime | None


class SyncRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=40)
    timeframe: Timeframe = Timeframe.D1
    limit: int = Field(default=250, ge=10, le=720)
    with_quotes: bool = True
    asset_classes: list[AssetClass] = Field(default_factory=list)


class SyncResultRead(BaseModel):
    instrument: str
    provider: str
    timeframe: Timeframe
    bars_written: int
    bars_skipped: int
    findings: int
    duration_ms: int
    ok: bool
    error: str | None = None


class SyncResponse(BaseModel):
    results: list[SyncResultRead]
    succeeded: int
    failed: int
    duration_ms: int


class ProviderHealth(BaseModel):
    provider: str
    display_name: str
    healthy: bool
    message: str
    asset_classes: list[str]
    timeframes: list[str]
    instruments_covered: int


class QualitySummary(BaseModel):
    total_open: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    stale_instruments: list[str]
    latest: list[QualityEventRead]
