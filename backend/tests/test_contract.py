"""Contract tests: the served API, the documented API and the stated identity must agree.

These exist because they drifted. After Phase 12 shipped, the frontend and the docs said
Phase 12 while the API root, `/system/meta` and `/operations/health` all still said
Phase 11 — and every test passed, because each test asserted the literal string it had
been written against.

A documentation suite that cannot fail is not a check. This one fails when a route exists
that the API reference does not mention, and when any public surface disagrees about what
build it is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core import release
from app.core.config import settings
from app.main import app

DOCS = Path(__file__).resolve().parents[2] / "docs" / "api" / "README.md"


def _normalise(path: str) -> str:
    """`/api/v1/accounts/{account_id}` and `/accounts/{id}` are the same endpoint."""
    return re.sub(r"\{[^}]+\}", "{p}", path.replace(settings.API_V1_PREFIX, "")).rstrip("/") or "/"


def served_paths() -> set[str]:
    spec = app.openapi()
    return {_normalise(path) for path in spec["paths"]}


def documented_paths() -> set[str]:
    text = DOCS.read_text()
    return {_normalise(match) for match in re.findall(r"\| `(/[A-Za-z0-9\-{}/_.]+)`", text)}


def test_the_api_reference_exists() -> None:
    assert DOCS.exists(), f"API reference missing at {DOCS}"


def test_every_served_endpoint_is_documented() -> None:
    """The one that would have caught the drift. Root `/` is the only exemption."""
    undocumented = sorted(served_paths() - documented_paths() - {"/"})
    assert undocumented == [], (
        f"{len(undocumented)} endpoint(s) are served but absent from docs/api/README.md: "
        f"{undocumented}"
    )


def test_the_documentation_does_not_promise_routes_that_do_not_exist() -> None:
    """The other direction: a documented endpoint nobody serves is a broken promise."""
    invented = sorted(documented_paths() - served_paths())
    assert invented == [], f"documented but not served: {invented}"


def test_the_route_count_is_what_the_docs_claim() -> None:
    assert len(app.openapi()["paths"]) == 118


# --------------------------------------------------------------- identity


def test_the_api_root_states_the_delivered_phase(client) -> None:
    body = client.get("/").json()
    assert body["phase"] == release.PHASE_NAME
    assert body["phases_complete"] == 12
    assert body["roadmap_complete"] is True


def test_the_api_root_still_refuses_to_claim_live_trading(client) -> None:
    body = client.get("/").json()
    assert body["execution_mode"] == "paper"
    assert body["live_trading_enabled"] is False
    assert body["real_money_enabled"] is False


@pytest.mark.parametrize(
    "path",
    ["/", "/api/v1/system/meta", "/api/v1/operations/health"],
)
def test_every_public_surface_agrees_on_the_phase(client, path: str) -> None:
    """Three surfaces, one answer. This is what was broken."""
    body = client.get(path).json()
    assert body["phase"] == release.PHASE_NAME


def test_the_phase_name_matches_the_frontend() -> None:
    """The static build's own phase string must not lag the backend."""
    engine = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "demo" / "engine.ts"
    if not engine.exists():  # pragma: no cover - frontend not always present
        pytest.skip("frontend not present")
    text = engine.read_text()
    match = re.search(r'phase:\s*"([^"]+)"', text)
    assert match, "frontend does not declare a phase"
    assert match.group(1).startswith(f"Phase {release.PHASES_COMPLETE}"), match.group(1)


def test_the_release_module_counts_phases_from_the_name() -> None:
    """Guard the derivation itself: a typo in PHASE must not silently read as phase 0."""
    assert release.PHASES_COMPLETE == 12
    assert release.IS_FINAL_PHASE is True
