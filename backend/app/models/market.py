"""Market-data domain: venues, instruments, provider mappings, candles, quotes.

Design notes
------------
* `Instrument` is the canonical identity used everywhere else in the platform.
  Providers are addressed through `InstrumentProvider` rows so a venue can be
  swapped or added without touching strategies, orders or the UI.
* Prices are `Numeric(24, 10)`: enough precision for JPY pairs and small-cap
  crypto without ever using floats for money.
* `MarketCandle` is keyed on (instrument, timeframe, open_time) so ingestion is
  idempotent — re-running a sync can never duplicate or fork a bar.
* London-quoted equities arrive in pence (GBp); `price_scale` records that so
  display and accounting never confuse 3564 pence with £3,564.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.session import Base, TimestampMixin
from app.models.identity import _uuid

if TYPE_CHECKING:
    pass


class AssetClass(str, enum.Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    EQUITY = "equity"
    ETF = "etf"


class InstrumentStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELISTED = "delisted"


class Timeframe(str, enum.Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    @property
    def seconds(self) -> int:
        return {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
            "1w": 604800,
        }[self.value]


class SessionState(str, enum.Enum):
    OPEN = "open"
    CLOSED = "closed"
    PRE_OPEN = "pre_open"
    POST_CLOSE = "post_close"
    ALWAYS_ON = "always_on"


class QualitySeverity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class QualityEventType(str, enum.Enum):
    GAP = "gap"
    STALE = "stale"
    OUTLIER = "outlier"
    DUPLICATE = "duplicate"
    SCALE_MISMATCH = "scale_mismatch"
    NON_MONOTONIC = "non_monotonic"
    PROVIDER_ERROR = "provider_error"


class Venue(Base, TimestampMixin):
    """A trading venue: exchange, FX interbank or crypto marketplace."""

    __tablename__ = "venues"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, native_enum=False, length=20), nullable=False
    )
    timezone: Mapped[str] = mapped_column(String(48), default="UTC", nullable=False)
    # Minutes from midnight in venue-local time. Null means 24/7.
    session_open: Mapped[str | None] = mapped_column(String(5), nullable=True)
    session_close: Mapped[str | None] = mapped_column(String(5), nullable=True)
    is_24_7: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settlement_days: Mapped[int] = mapped_column(default=0, nullable=False)


class Instrument(Base, TimestampMixin):
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(
        Enum(AssetClass, native_enum=False, length=20), nullable=False, index=True
    )
    venue_id: Mapped[str] = mapped_column(ForeignKey("venues.id", ondelete="RESTRICT"), index=True)
    base_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(8), nullable=False)
    # Multiplier applied to provider prices to reach quote-currency units
    # (0.01 for London pence quotes, 1 otherwise).
    price_scale: Mapped[float] = mapped_column(Numeric(12, 8), default=1, nullable=False)
    tick_size: Mapped[float] = mapped_column(Numeric(24, 10), default=0.0001, nullable=False)
    lot_size: Mapped[float] = mapped_column(Numeric(24, 10), default=0.0001, nullable=False)
    status: Mapped[InstrumentStatus] = mapped_column(
        Enum(InstrumentStatus, native_enum=False, length=20),
        default=InstrumentStatus.ACTIVE,
        nullable=False,
    )
    is_tradable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    venue: Mapped[Venue] = relationship(lazy="joined")
    providers: Mapped[list[InstrumentProvider]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_instruments_class_status", "asset_class", "status"),)

    @property
    def display_symbol(self) -> str:
        return self.symbol


class InstrumentProvider(Base, TimestampMixin):
    """Maps an instrument to a provider's identifier, with routing priority."""

    __tablename__ = "instrument_providers"
    __table_args__ = (UniqueConstraint("instrument_id", "provider", name="uq_instrument_provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    provider_symbol: Mapped[str] = mapped_column(String(48), nullable=False)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    instrument: Mapped[Instrument] = relationship(back_populates="providers")


class MarketCandle(Base, TimestampMixin):
    __tablename__ = "market_candles"
    __table_args__ = (
        UniqueConstraint("instrument_id", "timeframe", "open_time", name="uq_candle_bar"),
        Index("ix_candles_lookup", "instrument_id", "timeframe", "open_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    timeframe: Mapped[Timeframe] = mapped_column(
        Enum(Timeframe, native_enum=False, length=8), nullable=False
    )
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    vwap: Mapped[float | None] = mapped_column(Numeric(24, 10), nullable=True)
    volume: Mapped[float] = mapped_column(Numeric(28, 10), default=0, nullable=False)
    trades: Mapped[int | None] = mapped_column(nullable=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketQuote(Base, TimestampMixin):
    """Latest known quote per instrument (one row, overwritten on refresh)."""

    __tablename__ = "market_quotes"
    __table_args__ = (UniqueConstraint("instrument_id", name="uq_quote_instrument"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    bid: Mapped[float | None] = mapped_column(Numeric(24, 10), nullable=True)
    ask: Mapped[float | None] = mapped_column(Numeric(24, 10), nullable=True)
    mid: Mapped[float | None] = mapped_column(Numeric(24, 10), nullable=True)
    last: Mapped[float] = mapped_column(Numeric(24, 10), nullable=False)
    open_24h: Mapped[float | None] = mapped_column(Numeric(24, 10), nullable=True)
    change_24h_pct: Mapped[float | None] = mapped_column(Numeric(12, 6), nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Numeric(28, 10), nullable=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DataQualityEvent(Base, TimestampMixin):
    """A detected data-integrity problem. Phase 9 will gate trading on these."""

    __tablename__ = "data_quality_events"
    __table_args__ = (Index("ix_dq_instrument_created", "instrument_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    provider: Mapped[str] = mapped_column(String(40), default="", nullable=False)
    event_type: Mapped[QualityEventType] = mapped_column(
        Enum(QualityEventType, native_enum=False, length=24), nullable=False, index=True
    )
    severity: Mapped[QualitySeverity] = mapped_column(
        Enum(QualitySeverity, native_enum=False, length=16),
        default=QualitySeverity.WARNING,
        nullable=False,
    )
    timeframe: Mapped[Timeframe | None] = mapped_column(
        Enum(Timeframe, native_enum=False, length=8), nullable=True
    )
    message: Mapped[str] = mapped_column(String(400), nullable=False)
    detail: Mapped[str] = mapped_column(Text, default="", nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class IngestionRun(Base, TimestampMixin):
    """Audit record of every provider call, so data provenance is answerable."""

    __tablename__ = "ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # candles | quotes | registry
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    symbols: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rows_written: Mapped[int] = mapped_column(default=0, nullable=False)
    rows_skipped: Mapped[int] = mapped_column(default=0, nullable=False)
    duration_ms: Mapped[int] = mapped_column(default=0, nullable=False)
    error: Mapped[str] = mapped_column(Text, default="", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
