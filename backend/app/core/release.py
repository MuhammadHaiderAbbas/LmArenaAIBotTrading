"""The delivered phase, in one place.

Earlier changes hardcoded the phase string in `main.py`, `system.py`, `operations.py` and
the tests. After Phase 12 shipped, three of the four still said "Phase 11" — the frontend
and the docs said 12, the API said 11. That kind of drift is invisible in a test suite
that asserts the string it was written against, and very visible to anyone reading `/`.

So it is a setting, read through one helper, and the tests assert the *shape* (a phase
number, and that every surface agrees) rather than a literal that must be edited in five
places.

`PHASES_COMPLETE` is derived from the phase name, and `is_final_phase` says whether the
blueprint's roadmap has been fully delivered — which is a fact about the project, not a
claim about readiness for live trading.
"""

from __future__ import annotations

import re

from app.core.config import settings

PHASE_NAME = settings.PHASE
VERSION = settings.VERSION


def _phase_number(name: str) -> int:
    match = re.search(r"Phase\s+(\d+)", name)
    return int(match.group(1)) if match else 0


PHASES_COMPLETE = _phase_number(PHASE_NAME)
TOTAL_PHASES = 12  # blueprint §15 lists twelve phases.
IS_FINAL_PHASE = PHASES_COMPLETE >= TOTAL_PHASES


def identity() -> dict:
    """The public statement of what this build is. Every surface reads from here."""
    return {
        "name": settings.PROJECT_NAME,
        "version": VERSION,
        "phase": PHASE_NAME,
        "phases_complete": PHASES_COMPLETE,
        "total_phases": TOTAL_PHASES,
        "roadmap_complete": IS_FINAL_PHASE,
        "environment": settings.ENVIRONMENT,
        "execution_mode": "paper",
        "live_trading_enabled": settings.LIVE_TRADING_ENABLED,
        "real_money_enabled": settings.REAL_MONEY_ENABLED,
    }
