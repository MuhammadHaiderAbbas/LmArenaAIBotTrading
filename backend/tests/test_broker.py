"""Broker sandbox integration — and the wall that stops it becoming more than that.

The single most important property in this file: there is no input, flag or credential
that makes this codebase place a real order. These tests try, from three directions.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services.broker import base as broker_base
from app.services.broker.base import (
    LiveTradingRefused,
    SandboxOrder,
    SimulatedSandboxAdapter,
    adapter_for,
    available_brokers,
    require_sandbox,
)

# ----------------------------------------------------------------- the wall


def test_sandbox_mode_is_permitted() -> None:
    require_sandbox(mode="sandbox")  # must not raise


def test_live_mode_raises() -> None:
    with pytest.raises(LiveTradingRefused):
        require_sandbox(mode="live")


@pytest.mark.parametrize("mode", ["live", "LIVE", "production", "", "live-ish"])
def test_every_other_mode_raises(mode: str) -> None:
    """No near-miss slips through: the check is equality with 'sandbox', not a prefix."""
    with pytest.raises(LiveTradingRefused):
        require_sandbox(mode=mode)


def test_the_live_flag_does_not_open_a_path(monkeypatch) -> None:
    """Even with both live flags on, there is still no adapter that could reach a market."""
    monkeypatch.setattr(settings, "LIVE_TRADING_ENABLED", True)
    monkeypatch.setattr(settings, "REAL_MONEY_ENABLED", True)
    reachability = broker_base.live_trading_is_impossible()
    assert reachability["reachable"] is False
    assert reachability["live_adapter_registered"] is False
    with pytest.raises(LiveTradingRefused):
        require_sandbox(mode="live")


def test_no_registered_adapter_supports_live() -> None:
    assert all(not descriptor["live_supported"] for descriptor in available_brokers())


def test_an_unknown_broker_is_a_clear_error() -> None:
    with pytest.raises(broker_base.BrokerError, match="No adapter is registered"):
        adapter_for("interactive-brokers")


# -------------------------------------------------------------- the adapter


def test_the_simulated_sandbox_accepts_a_valid_handshake() -> None:
    adapter = SimulatedSandboxAdapter()
    ack = adapter.handshake(api_key="key-123", api_secret="secret-456")
    assert ack.accepted is True
    assert ack.broker_order_id.startswith("sim-")


def test_a_handshake_without_a_secret_is_refused() -> None:
    ack = SimulatedSandboxAdapter().handshake(api_key="key", api_secret="")
    assert ack.accepted is False


def test_the_sandbox_says_no_fill_occurred() -> None:
    ack = SimulatedSandboxAdapter().submit(
        SandboxOrder(client_order_id="abc123", symbol="GBP/USD", side="buy", quantity=1)
    )
    assert ack.accepted is True
    assert "No fill occurred and no money moved" in ack.message


def test_a_non_positive_quantity_is_refused_by_the_sandbox() -> None:
    ack = SimulatedSandboxAdapter().submit(
        SandboxOrder(client_order_id="abc", symbol="X", side="buy", quantity=0)
    )
    assert ack.accepted is False


def test_the_fingerprint_is_stable_and_does_not_leak_the_key() -> None:
    adapter = SimulatedSandboxAdapter()
    first = adapter.fingerprint("sk-secret-key")
    assert first == adapter.fingerprint("sk-secret-key")
    assert first != adapter.fingerprint("sk-other-key")
    assert "sk-secret" not in first


def test_clock_probe_reports_skew() -> None:
    assert SimulatedSandboxAdapter(skew_ms=42.0).clock_probe() == 42.0
