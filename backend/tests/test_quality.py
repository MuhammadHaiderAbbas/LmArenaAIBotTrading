"""Data-quality detection.

These checks are what make "reject trading on stale or incomplete data"
enforceable in Phase 9, so each detector is pinned by a test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.integrations.providers.base import NormalisedBar
from app.models.market import AssetClass, QualityEventType, QualitySeverity, Timeframe, Venue
from app.services.market.quality import check_bars, check_quote

NOW = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)


def make_venue(**overrides) -> Venue:
    defaults = {
        "code": "KRAKEN",
        "name": "Kraken",
        "asset_class": AssetClass.CRYPTO,
        "timezone": "UTC",
        "is_24_7": True,
    }
    defaults.update(overrides)
    return Venue(**defaults)


def series(count: int, *, start: datetime = NOW - timedelta(hours=24), step_minutes: int = 60,
           price: float = 100.0, volume: float = 10.0) -> list[NormalisedBar]:
    bars = []
    for index in range(count):
        value = price + index * 0.5
        bars.append(
            NormalisedBar(
                open_time=start + timedelta(minutes=step_minutes * index),
                open=value,
                high=value + 1,
                low=value - 1,
                close=value + 0.25,
                volume=volume,
            )
        )
    return bars


def types(findings) -> set[QualityEventType]:
    return {finding.event_type for finding in findings}


def test_clean_series_reports_nothing() -> None:
    bars = series(24, start=NOW - timedelta(hours=24))
    assert check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW) == []


def test_non_monotonic_bars_are_critical() -> None:
    bars = series(5, start=NOW - timedelta(hours=5))
    bars[2].open_time = bars[1].open_time
    findings = check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    assert QualityEventType.NON_MONOTONIC in types(findings)
    assert any(f.severity is QualitySeverity.CRITICAL for f in findings)


def test_ohlc_bounds_violation_is_flagged() -> None:
    bars = series(5, start=NOW - timedelta(hours=5))
    bars[-1].high = bars[-1].low - 1  # impossible bar
    findings = check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    assert QualityEventType.OUTLIER in types(findings)


def test_non_positive_price_is_critical() -> None:
    bars = series(5, start=NOW - timedelta(hours=5))
    bars[-1].close = 0
    findings = check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    assert any(f.severity is QualitySeverity.CRITICAL for f in findings)


def test_gap_while_venue_open_is_reported() -> None:
    bars = series(6, start=NOW - timedelta(hours=6))
    del bars[3]  # hole in the middle
    findings = check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    gap = [f for f in findings if f.event_type is QualityEventType.GAP]
    assert gap, "expected a gap finding"
    assert "missing" in gap[0].message


def test_gap_during_closed_session_is_not_reported() -> None:
    """A weekend hole in an equity series is expected, not an alert."""
    venue = make_venue(
        code="NASDAQ",
        asset_class=AssetClass.EQUITY,
        timezone="America/New_York",
        is_24_7=False,
        session_open="09:30",
        session_close="16:00",
    )
    friday_close = datetime(2026, 11, 20, 21, 0, tzinfo=UTC)
    monday_open = datetime(2026, 11, 23, 14, 30, tzinfo=UTC)
    bars = [
        NormalisedBar(open_time=friday_close - timedelta(hours=1), open=100, high=101, low=99, close=100.5),
        NormalisedBar(open_time=friday_close, open=100.5, high=101, low=99, close=100.2),
        NormalisedBar(open_time=monday_open, open=100.2, high=102, low=100, close=101.5),
        NormalisedBar(open_time=monday_open + timedelta(hours=1), open=101.5, high=103, low=101, close=102.5),
    ]
    findings = check_bars(
        bars, timeframe=Timeframe.H1, venue=venue, at=monday_open + timedelta(hours=2)
    )
    assert QualityEventType.GAP not in types(findings)


def test_stale_series_is_detected() -> None:
    # Data ends 6 hours before "now": 1h bars, so this is clearly stale.
    bars = series(6, start=NOW - timedelta(hours=12))
    findings = check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    stale = [f for f in findings if f.event_type is QualityEventType.STALE]
    assert stale
    assert stale[0].severity in (QualitySeverity.WARNING, QualitySeverity.CRITICAL)


def test_outlier_move_is_flagged_against_recent_distribution() -> None:
    bars = series(30, start=NOW - timedelta(hours=30))
    bars[-1].close = bars[-1].open * 1.8  # +80% jump
    bars[-1].high = bars[-1].close + 1
    findings = check_bars(bars, timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    assert QualityEventType.OUTLIER in types(findings)


def test_all_zero_volume_is_reported_as_informational() -> None:
    bars = series(10, start=NOW - timedelta(hours=10), volume=0.0)
    findings = check_bars(
        bars, timeframe=Timeframe.H1, venue=make_venue(), expected_volume=True, at=NOW
    )
    volume_findings = [f for f in findings if "volume" in f.message.lower()]
    assert volume_findings
    assert volume_findings[0].severity is QualitySeverity.INFO


def test_empty_response_is_a_provider_problem() -> None:
    findings = check_bars([], timeframe=Timeframe.H1, venue=make_venue(), at=NOW)
    assert types(findings) == {QualityEventType.PROVIDER_ERROR}


def test_quote_staleness_thresholds_on_open_venue() -> None:
    venue = make_venue()  # crypto: always open
    fresh = check_quote(100.0, NOW - timedelta(seconds=20), venue=venue, at=NOW)
    assert fresh == []

    old = check_quote(100.0, NOW - timedelta(minutes=10), venue=venue, at=NOW)
    assert old and old[0].event_type is QualityEventType.STALE
    assert old[0].severity is QualitySeverity.WARNING

    ancient = check_quote(100.0, NOW - timedelta(hours=6), venue=venue, at=NOW)
    assert ancient[0].severity is QualitySeverity.CRITICAL


def test_quote_from_closed_venue_is_informational_not_stale() -> None:
    """A weekend equity quote is the last print, not a broken feed."""
    venue = make_venue(
        code="LSE",
        asset_class=AssetClass.EQUITY,
        timezone="Europe/London",
        is_24_7=False,
        session_open="08:00",
        session_close="16:30",
    )
    saturday = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    findings = check_quote(
        100.0, datetime(2026, 11, 20, 16, 30, tzinfo=UTC), venue=venue, at=saturday
    )
    assert findings
    assert findings[0].severity is QualitySeverity.INFO


def test_quote_without_timestamp_is_flagged() -> None:
    findings = check_quote(100.0, None, at=NOW)
    assert findings and findings[0].event_type is QualityEventType.STALE
