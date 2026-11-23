"""Instrument registry.

The registry is the single source of truth for *what* the platform can observe.
It is seeded declaratively here and persisted idempotently, so a fresh database
and an existing one converge on the same instruments.

Every instrument records:
* canonical identity (symbol, name, asset class)
* the venue that sets its calendar and timezone
* a `price_scale` that normalises provider units into quote currency
* one or more provider identifiers, ordered by routing priority

Phase 3 instruments are deliberately limited to liquid, well-known symbols across
all four asset classes. `is_tradable` stays **false**: the registry describes what
can be observed, not what may be traded. Phase 8 flips that flag per strategy
after risk review.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.providers.base import InstrumentSpec
from app.models.market import AssetClass, Instrument, InstrumentProvider, Venue

# ---------------------------------------------------------------------------
# Venues
# ---------------------------------------------------------------------------
VENUES: list[dict[str, object]] = [
    {
        "code": "KRAKEN",
        "name": "Kraken",
        "asset_class": AssetClass.CRYPTO,
        "timezone": "UTC",
        "is_24_7": True,
        "settlement_days": 0,
    },
    {
        "code": "NASDAQ",
        "name": "Nasdaq Stock Market",
        "asset_class": AssetClass.EQUITY,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "session_close": "16:00",
        "is_24_7": False,
        "settlement_days": 1,
    },
    {
        "code": "NYSE",
        "name": "New York Stock Exchange",
        "asset_class": AssetClass.EQUITY,
        "timezone": "America/New_York",
        "session_open": "09:30",
        "session_close": "16:00",
        "is_24_7": False,
        "settlement_days": 1,
    },
    {
        "code": "LSE",
        "name": "London Stock Exchange",
        "asset_class": AssetClass.EQUITY,
        "timezone": "Europe/London",
        "session_open": "08:00",
        "session_close": "16:30",
        "is_24_7": False,
        "settlement_days": 1,
    },
    {
        "code": "FX",
        "name": "FX Interbank",
        "asset_class": AssetClass.FOREX,
        "timezone": "UTC",
        "session_open": "22:00",
        "session_close": "22:00",
        "is_24_7": False,
        "settlement_days": 2,
    },
]

# ---------------------------------------------------------------------------
# Instruments
# ---------------------------------------------------------------------------
# London-listed lines are quoted in pence by every provider we use, so their
# price_scale is 0.01 to normalise into GBP.
INSTRUMENTS: list[InstrumentSpec] = [
    # -- Crypto (Kraken, USD quoted) -------------------------------------
    InstrumentSpec(
        symbol="BTC/USD",
        name="Bitcoin",
        asset_class="crypto",
        venue_code="KRAKEN",
        venue_name="Kraken",
        base_currency="BTC",
        quote_currency="USD",
        tick_size=0.1,
        lot_size=0.00001,
        description="Spot bitcoin against the US dollar.",
        provider_symbols={"kraken": "XBTUSD"},
    ),
    InstrumentSpec(
        symbol="ETH/USD",
        name="Ethereum",
        asset_class="crypto",
        venue_code="KRAKEN",
        venue_name="Kraken",
        base_currency="ETH",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=0.0001,
        description="Spot ether against the US dollar.",
        provider_symbols={"kraken": "ETHUSD"},
    ),
    InstrumentSpec(
        symbol="SOL/USD",
        name="Solana",
        asset_class="crypto",
        venue_code="KRAKEN",
        venue_name="Kraken",
        base_currency="SOL",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=0.001,
        description="Spot solana against the US dollar.",
        provider_symbols={"kraken": "SOLUSD"},
    ),
    InstrumentSpec(
        symbol="XRP/USD",
        name="XRP",
        asset_class="crypto",
        venue_code="KRAKEN",
        venue_name="Kraken",
        base_currency="XRP",
        quote_currency="USD",
        tick_size=0.0001,
        lot_size=1,
        description="Spot XRP against the US dollar.",
        provider_symbols={"kraken": "XRPUSD"},
    ),
    InstrumentSpec(
        symbol="ADA/USD",
        name="Cardano",
        asset_class="crypto",
        venue_code="KRAKEN",
        venue_name="Kraken",
        base_currency="ADA",
        quote_currency="USD",
        tick_size=0.0001,
        lot_size=1,
        description="Spot cardano against the US dollar.",
        provider_symbols={"kraken": "ADAUSD"},
    ),
    # -- US equities ------------------------------------------------------
    InstrumentSpec(
        symbol="AAPL",
        name="Apple Inc.",
        asset_class="equity",
        venue_code="NASDAQ",
        venue_name="Nasdaq Stock Market",
        base_currency="AAPL",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=1,
        description="Apple Inc. common stock.",
        provider_symbols={"yahoo": "AAPL", "twelvedata": "AAPL"},
    ),
    InstrumentSpec(
        symbol="MSFT",
        name="Microsoft Corporation",
        asset_class="equity",
        venue_code="NASDAQ",
        venue_name="Nasdaq Stock Market",
        base_currency="MSFT",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=1,
        description="Microsoft Corporation common stock.",
        provider_symbols={"yahoo": "MSFT", "twelvedata": "MSFT"},
    ),
    InstrumentSpec(
        symbol="NVDA",
        name="NVIDIA Corporation",
        asset_class="equity",
        venue_code="NASDAQ",
        venue_name="Nasdaq Stock Market",
        base_currency="NVDA",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=1,
        description="NVIDIA Corporation common stock.",
        provider_symbols={"yahoo": "NVDA", "twelvedata": "NVDA"},
    ),
    InstrumentSpec(
        symbol="TSLA",
        name="Tesla, Inc.",
        asset_class="equity",
        venue_code="NASDAQ",
        venue_name="Nasdaq Stock Market",
        base_currency="TSLA",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=1,
        description="Tesla, Inc. common stock.",
        provider_symbols={"yahoo": "TSLA", "twelvedata": "TSLA"},
    ),
    # -- UK equities (pence quoted) ---------------------------------------
    InstrumentSpec(
        symbol="SHEL.L",
        name="Shell plc",
        asset_class="equity",
        venue_code="LSE",
        venue_name="London Stock Exchange",
        base_currency="SHEL",
        quote_currency="GBP",
        price_scale=0.01,
        tick_size=0.01,
        lot_size=1,
        description="Shell plc ordinary shares, quoted in pence and normalised to GBP.",
        provider_symbols={"yahoo": "SHEL.L", "twelvedata": "SHEL.L"},
    ),
    InstrumentSpec(
        symbol="HSBA.L",
        name="HSBC Holdings plc",
        asset_class="equity",
        venue_code="LSE",
        venue_name="London Stock Exchange",
        base_currency="HSBA",
        quote_currency="GBP",
        price_scale=0.01,
        tick_size=0.01,
        lot_size=1,
        description="HSBC Holdings plc ordinary shares, normalised from pence to GBP.",
        provider_symbols={"yahoo": "HSBA.L", "twelvedata": "HSBA.L"},
    ),
    # -- ETFs --------------------------------------------------------------
    InstrumentSpec(
        symbol="SPY",
        name="SPDR S&P 500 ETF Trust",
        asset_class="etf",
        venue_code="NYSE",
        venue_name="New York Stock Exchange",
        base_currency="SPY",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=1,
        description="US large-cap equity ETF tracking the S&P 500.",
        provider_symbols={"yahoo": "SPY", "twelvedata": "SPY"},
    ),
    InstrumentSpec(
        symbol="QQQ",
        name="Invesco QQQ Trust",
        asset_class="etf",
        venue_code="NASDAQ",
        venue_name="Nasdaq Stock Market",
        base_currency="QQQ",
        quote_currency="USD",
        tick_size=0.01,
        lot_size=1,
        description="US technology-weighted ETF tracking the Nasdaq-100.",
        provider_symbols={"yahoo": "QQQ", "twelvedata": "QQQ"},
    ),
    InstrumentSpec(
        symbol="VUSA.L",
        name="Vanguard S&P 500 UCITS ETF",
        asset_class="etf",
        venue_code="LSE",
        venue_name="London Stock Exchange",
        base_currency="VUSA",
        quote_currency="GBP",
        price_scale=0.01,
        tick_size=0.01,
        lot_size=1,
        description="GBP-denominated S&P 500 UCITS ETF, normalised from pence to GBP.",
        provider_symbols={"yahoo": "VUSA.L", "twelvedata": "VUSA.L"},
    ),
    # -- FX ----------------------------------------------------------------
    InstrumentSpec(
        symbol="GBP/USD",
        name="British pound / US dollar",
        asset_class="forex",
        venue_code="FX",
        venue_name="FX Interbank",
        base_currency="GBP",
        quote_currency="USD",
        tick_size=0.00001,
        lot_size=1000,
        description="Major FX pair. Falls back to ECB daily reference rates.",
        provider_symbols={"yahoo": "GBPUSD=X", "frankfurter": "GBPUSD", "twelvedata": "GBP/USD"},
    ),
    InstrumentSpec(
        symbol="EUR/USD",
        name="Euro / US dollar",
        asset_class="forex",
        venue_code="FX",
        venue_name="FX Interbank",
        base_currency="EUR",
        quote_currency="USD",
        tick_size=0.00001,
        lot_size=1000,
        description="Major FX pair. Falls back to ECB daily reference rates.",
        provider_symbols={"yahoo": "EURUSD=X", "frankfurter": "EURUSD", "twelvedata": "EUR/USD"},
    ),
    InstrumentSpec(
        symbol="USD/JPY",
        name="US dollar / Japanese yen",
        asset_class="forex",
        venue_code="FX",
        venue_name="FX Interbank",
        base_currency="USD",
        quote_currency="JPY",
        tick_size=0.001,
        lot_size=1000,
        description="Major FX pair. Falls back to ECB daily reference rates.",
        provider_symbols={"yahoo": "USDJPY=X", "frankfurter": "USDJPY", "twelvedata": "USD/JPY"},
    ),
    InstrumentSpec(
        symbol="GBP/EUR",
        name="British pound / euro",
        asset_class="forex",
        venue_code="FX",
        venue_name="FX Interbank",
        base_currency="GBP",
        quote_currency="EUR",
        tick_size=0.00001,
        lot_size=1000,
        description="European cross. Falls back to ECB daily reference rates.",
        provider_symbols={"yahoo": "GBPEUR=X", "frankfurter": "GBPEUR", "twelvedata": "GBP/EUR"},
    ),
]


def seed_registry(db: Session) -> dict[str, int]:
    """Create or update venues, instruments and provider mappings.

    Idempotent: safe to call on every start and on every request in development.
    """
    venue_ids: dict[str, str] = {}
    created = {"venues": 0, "instruments": 0, "providers": 0}

    for spec in VENUES:
        venue = db.scalar(select(Venue).where(Venue.code == spec["code"]))
        if venue is None:
            venue = Venue(**spec)  # type: ignore[arg-type]
            db.add(venue)
            db.flush()
            created["venues"] += 1
        else:
            for field, value in spec.items():
                if field != "code":
                    setattr(venue, field, value)
        venue_ids[str(spec["code"])] = venue.id

    for spec in INSTRUMENTS:
        instrument = db.scalar(select(Instrument).where(Instrument.symbol == spec.symbol))
        if instrument is None:
            instrument = Instrument(
                symbol=spec.symbol,
                name=spec.name,
                asset_class=AssetClass(spec.asset_class),
                venue_id=venue_ids[spec.venue_code],
                base_currency=spec.base_currency,
                quote_currency=spec.quote_currency,
                price_scale=spec.price_scale,
                tick_size=spec.tick_size,
                lot_size=spec.lot_size,
                is_tradable=spec.is_tradable,
                description=spec.description,
            )
            db.add(instrument)
            db.flush()
            created["instruments"] += 1
        else:
            # Registry metadata is owned by code, not by runtime drift.
            instrument.name = spec.name
            instrument.asset_class = AssetClass(spec.asset_class)
            instrument.venue_id = venue_ids[spec.venue_code]
            instrument.price_scale = spec.price_scale
            instrument.tick_size = spec.tick_size
            instrument.lot_size = spec.lot_size
            instrument.description = spec.description

        for provider, provider_symbol in spec.provider_symbols.items():
            mapping = db.scalar(
                select(InstrumentProvider).where(
                    InstrumentProvider.instrument_id == instrument.id,
                    InstrumentProvider.provider == provider,
                )
            )
            priority = list(spec.provider_symbols).index(provider)
            if mapping is None:
                db.add(
                    InstrumentProvider(
                        instrument_id=instrument.id,
                        provider=provider,
                        provider_symbol=provider_symbol,
                        priority=priority,
                    )
                )
                created["providers"] += 1
            else:
                mapping.provider_symbol = provider_symbol
                mapping.priority = priority

    db.flush()
    return created
