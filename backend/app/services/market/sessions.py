"""Venue session awareness.

Trading decisions must never be made against a closed market's last print, and a
Sunday quote must never be read as fresh. This module answers, for any venue and
instant, whether the venue is open and how old its most recent expected bar is.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.models.market import SessionState, Venue

# Equity and ETF venues close for weekends; FX runs from Sunday 22:00 to Friday
# 22:00 UTC; crypto never closes. Public holidays are intentionally *not* modelled
# in Phase 3 — the data-quality layer flags the resulting gaps instead of the
# calendar silently lying, and exchange calendars land in a later phase.
WEEKEND = {5, 6}  # Saturday, Sunday


def _parse(clock: str | None) -> time | None:
    if not clock:
        return None
    hours, minutes = clock.split(":")
    return time(int(hours), int(minutes))


def venue_state(venue: Venue, at: datetime | None = None) -> SessionState:
    """Return the current session state for a venue."""
    moment = (at or datetime.now(UTC)).astimezone(UTC)

    if venue.is_24_7:
        return SessionState.ALWAYS_ON

    local = moment.astimezone(ZoneInfo(venue.timezone))
    weekday = local.weekday()

    if venue.asset_class.value == "forex":
        # FX week: opens Sunday 22:00 UTC, closes Friday 22:00 UTC.
        if weekday == 5:
            return SessionState.CLOSED
        if weekday == 6 and local.hour < 22:
            return SessionState.CLOSED
        if weekday == 4 and local.hour >= 22:
            return SessionState.CLOSED
        return SessionState.OPEN

    if weekday in WEEKEND:
        return SessionState.CLOSED

    open_at = _parse(venue.session_open)
    close_at = _parse(venue.session_close)
    if open_at is None or close_at is None:
        return SessionState.OPEN

    now = local.time()
    if now < open_at:
        return SessionState.PRE_OPEN
    if now >= close_at:
        return SessionState.POST_CLOSE
    return SessionState.OPEN


def is_open(venue: Venue, at: datetime | None = None) -> bool:
    return venue_state(venue, at) in (SessionState.OPEN, SessionState.ALWAYS_ON)


def last_expected_bar_close(
    venue: Venue, timeframe_seconds: int, at: datetime | None = None
) -> datetime:
    """The most recent instant at which a *closed* bar should have existed.

    Used to distinguish "market is quiet" from "our data is stale". For a closed
    venue this is the previous session close; for an open venue it is the start of
    the current bar.
    """
    moment = (at or datetime.now(UTC)).astimezone(UTC)
    state = venue_state(venue, moment)

    if state in (SessionState.OPEN, SessionState.ALWAYS_ON):
        epoch = int(moment.timestamp())
        return datetime.fromtimestamp(epoch - (epoch % timeframe_seconds), tz=UTC)

    # Walk back to the most recent point the venue was open.
    local_close = _parse(venue.session_close)
    if venue.is_24_7 or local_close is None:
        return moment.replace(second=0, microsecond=0)

    for days_back in range(1, 8):
        candidate_day = (moment - timedelta(days=days_back)).astimezone(ZoneInfo(venue.timezone))
        if candidate_day.weekday() in WEEKEND:
            continue
        close_dt = candidate_day.replace(
            hour=local_close.hour, minute=local_close.minute, second=0, microsecond=0
        )
        return close_dt.astimezone(UTC)
    return moment.replace(second=0, microsecond=0)


def staleness_seconds(
    venue: Venue,
    last_bar_close: datetime | None,
    timeframe_seconds: int,
    at: datetime | None = None,
) -> float | None:
    """Seconds by which our newest bar lags what the venue should have produced."""
    if last_bar_close is None:
        return None
    expected = last_expected_bar_close(venue, timeframe_seconds, at)
    return max(0.0, (expected - last_bar_close.astimezone(UTC)).total_seconds())
