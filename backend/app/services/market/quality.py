"""Data-quality checks over ingested bars and quotes.

Blueprint requirement: *reject trading on stale or incomplete market data*. This
module is what makes that enforceable — Phase 9's risk controller will consult the
open quality events before approving an order.

Every check returns findings; it never mutates bars. Ingestion decides what to do
with them (skip the row, record an event, or both).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from app.models.market import (
    QualityEventType,
    QualitySeverity,
    SessionState,
    Timeframe,
    Venue,
)
from app.services.market.sessions import is_open, staleness_seconds, venue_state


@dataclass(slots=True)
class Finding:
    event_type: QualityEventType
    severity: QualitySeverity
    message: str
    detail: str = ""
    window_start: datetime | None = None
    window_end: datetime | None = None


def check_bars(
    bars: list,
    *,
    timeframe: Timeframe,
    venue: Venue,
    expected_volume: bool = True,
    at: datetime | None = None,
) -> list[Finding]:
    """Run the full bar-quality suite. `bars` are NormalisedBar instances."""
    findings: list[Finding] = []
    if not bars:
        findings.append(
            Finding(
                QualityEventType.PROVIDER_ERROR,
                QualitySeverity.WARNING,
                "Provider returned no bars",
            )
        )
        return findings

    findings.extend(_check_monotonic(bars))
    findings.extend(_check_ohlc_consistency(bars))
    findings.extend(_check_outliers(bars))
    findings.extend(_check_gaps(bars, timeframe=timeframe, venue=venue))
    if expected_volume:
        findings.extend(_check_volume(bars))
    findings.extend(_check_staleness(bars, timeframe=timeframe, venue=venue, at=at))
    return findings


def _check_monotonic(bars: list) -> list[Finding]:
    for previous, current in zip(bars, bars[1:], strict=False):
        if current.open_time <= previous.open_time:
            return [
                Finding(
                    QualityEventType.NON_MONOTONIC,
                    QualitySeverity.CRITICAL,
                    "Bars are not in strictly increasing time order",
                    detail=(
                        f"{previous.open_time.isoformat()} followed by "
                        f"{current.open_time.isoformat()}"
                    ),
                )
            ]
    return []


def _check_ohlc_consistency(bars: list) -> list[Finding]:
    findings: list[Finding] = []
    for bar in bars:
        values = (bar.open, bar.high, bar.low, bar.close)
        if any(value is None or value <= 0 for value in values):
            findings.append(
                Finding(
                    QualityEventType.OUTLIER,
                    QualitySeverity.CRITICAL,
                    f"Non-positive or missing price at {bar.open_time.isoformat()}",
                    detail=str(values),
                    window_start=bar.open_time,
                )
            )
            continue
        if not (bar.low <= bar.open <= bar.high and bar.low <= bar.close <= bar.high):
            findings.append(
                Finding(
                    QualityEventType.OUTLIER,
                    QualitySeverity.WARNING,
                    f"OHLC bounds violated at {bar.open_time.isoformat()}",
                    detail=f"o={bar.open} h={bar.high} l={bar.low} c={bar.close}",
                    window_start=bar.open_time,
                )
            )
    return findings


def _check_outliers(bars: list) -> list[Finding]:
    """Flag a bar whose move dwarfs the recent distribution of moves."""
    if len(bars) < 20:
        return []
    changes = []
    for previous, current in zip(bars, bars[1:], strict=False):
        if previous.close:
            changes.append(abs(current.close - previous.close) / previous.close)
    if not changes:
        return []
    typical = median(changes) or 1e-9
    findings: list[Finding] = []
    for previous, current, change in zip(bars, bars[1:], changes, strict=False):
        if change > max(typical * 12, 0.25):
            findings.append(
                Finding(
                    QualityEventType.OUTLIER,
                    QualitySeverity.WARNING,
                    f"Unusual {change * 100:.1f}% move at {current.open_time.isoformat()}",
                    detail=f"close {previous.close} -> {current.close}; median move {typical * 100:.3f}%",
                    window_start=current.open_time,
                )
            )
    return findings


def _check_gaps(bars: list, *, timeframe: Timeframe, venue: Venue) -> list[Finding]:
    """Detect missing bars inside a period the venue was open.

    Gaps during closed sessions are expected and are not reported: reporting them
    would train operators to ignore the alert stream.
    """
    findings: list[Finding] = []
    step = timedelta(seconds=timeframe.seconds)
    tolerance = step * 1.5

    for previous, current in zip(bars, bars[1:], strict=False):
        delta = current.open_time - previous.open_time
        if delta <= tolerance:
            continue
        # Was the venue open at the midpoint of the gap? If not, this is a session
        # break rather than missing data.
        midpoint = previous.open_time + delta / 2
        if not is_open(venue, midpoint):
            continue
        missing = int(delta / step) - 1
        findings.append(
            Finding(
                QualityEventType.GAP,
                QualitySeverity.WARNING,
                f"{missing} missing {timeframe.value} bar(s) while {venue.code} was open",
                detail=f"from {previous.open_time.isoformat()} to {current.open_time.isoformat()}",
                window_start=previous.open_time,
                window_end=current.open_time,
            )
        )
    return findings


def _check_volume(bars: list) -> list[Finding]:
    zero_bars = [bar for bar in bars if not bar.volume]
    if len(bars) >= 5 and len(zero_bars) == len(bars):
        return [
            Finding(
                QualityEventType.GAP,
                QualitySeverity.INFO,
                "Provider reports no volume for this instrument",
                detail="Expected for FX reference rates; volume-based features are unavailable.",
            )
        ]
    return []


def _check_staleness(
    bars: list, *, timeframe: Timeframe, venue: Venue, at: datetime | None
) -> list[Finding]:
    moment = at or datetime.now(UTC)
    last_close = bars[-1].open_time + timedelta(seconds=timeframe.seconds)
    lag = staleness_seconds(venue, last_close, timeframe.seconds, moment)
    if lag is None:
        return []
    # Allow one bar of slack plus 30 seconds of clock skew.
    threshold = timeframe.seconds + 30
    if lag > threshold:
        severity = (
            QualitySeverity.CRITICAL
            if lag > max(threshold * 3, 900)
            else QualitySeverity.WARNING
        )
        return [
            Finding(
                QualityEventType.STALE,
                severity,
                f"Newest bar is {lag / 60:.0f} min older than expected",
                detail=(
                    f"{venue.code} {timeframe.value}: last bar closed "
                    f"{last_close.isoformat()}"
                ),
                window_end=last_close,
            )
        ]
    return []


def check_quote(
    quote_price: float,
    observed_at: datetime | None,
    *,
    venue: Venue | None = None,
    max_age_seconds: int = 300,
    at: datetime | None = None,
) -> list[Finding]:
    """Assess quote freshness.

    Freshness is only meaningful while the venue is open. A quote from a closed
    market is the last print, not a stale feed — reporting it as stale would train
    operators to ignore the alert stream, which is how real outages get missed.
    Closed-venue quotes are therefore recorded at INFO so provenance is kept
    without raising a false alarm.
    """
    moment = at or datetime.now(UTC)
    if observed_at is None:
        return [
            Finding(
                QualityEventType.STALE,
                QualitySeverity.WARNING,
                "Quote carries no observation timestamp",
            )
        ]

    age = (moment - observed_at.astimezone(UTC)).total_seconds()
    if age <= max_age_seconds:
        return []

    if venue is not None and venue_state(venue, moment) not in (
        SessionState.OPEN,
        SessionState.ALWAYS_ON,
    ):
        return [
            Finding(
                QualityEventType.STALE,
                QualitySeverity.INFO,
                f"Quote predates the closed session ({age / 3600:.1f} h old)",
                detail=f"{venue.code} is closed; last print {observed_at.isoformat()}",
            )
        ]

    severity = (
        QualitySeverity.CRITICAL if age >= max_age_seconds * 4 else QualitySeverity.WARNING
    )
    return [
        Finding(
            QualityEventType.STALE,
            severity,
            f"Quote is {age / 60:.0f} min old",
            detail=f"observation time {observed_at.isoformat()}",
        )
    ]
