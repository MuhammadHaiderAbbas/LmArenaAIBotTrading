"""The broker adapter protocol and the sandbox wall."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.core.config import settings
from app.services.errors import DomainError


class BrokerError(Exception):
    """A broker interaction failed in a way the caller must handle."""


class LiveTradingRefused(BrokerError, DomainError):
    """Raised by every path that would place a real order.

    This exception has no counterpart that catches it and continues: callers either let
    it become a 403 or record it as a blocker. It exists so the refusal is a typed fact
    in the code, not a comment. Surfaces as HTTP 403: the request is well formed, the
    capability does not exist.
    """

    def __init__(self, message: str) -> None:
        DomainError.__init__(self, message, status_code=403)


@dataclass(slots=True)
class SandboxOrder:
    client_order_id: str
    symbol: str
    side: str
    quantity: float
    order_type: str = "market"
    limit_price: float | None = None


@dataclass(slots=True)
class SandboxAck:
    """What a sandbox venue says back. Never contains a real fill."""

    broker_order_id: str
    accepted: bool
    message: str
    received_at: datetime
    raw: dict = field(default_factory=dict)


@dataclass(slots=True)
class BrokerDescriptor:
    key: str
    name: str
    sandbox_supported: bool
    live_supported: bool
    credential_fields: tuple[str, ...]
    notes: str


def require_sandbox(*, mode: str) -> None:
    """The single place that decides whether a mode may proceed.

    Called before any credential is used. `live` is refused unconditionally — not
    "refused unless LIVE_TRADING_ENABLED", because there is no live adapter to enable,
    and a flag that lights up a non-existent code path is worse than no flag.
    """
    if mode != "sandbox":
        raise LiveTradingRefused(
            "Live broker execution is not implemented. This build can hand an order to a "
            "sandbox venue only; there is no adapter that would reach a real market."
        )


class BrokerAdapter(ABC):
    """Every broker integration implements this, and only this."""

    descriptor: BrokerDescriptor

    @abstractmethod
    def handshake(self, *, api_key: str, api_secret: str) -> SandboxAck: ...

    @abstractmethod
    def clock_probe(self) -> float:
        """Return observed server-clock skew in milliseconds."""

    @abstractmethod
    def submit(self, order: SandboxOrder) -> SandboxAck: ...

    def fingerprint(self, api_key: str) -> str:
        """A stable, non-reversible identifier so a UI can show *which* key is linked."""
        return hashlib.sha256(f"{self.descriptor.key}:{api_key}".encode()).hexdigest()[:12]


class SandboxAdapter(BrokerAdapter, ABC):
    """Marker: an adapter whose only reachable venue is a sandbox."""

    venue = "sandbox"


class SimulatedSandboxAdapter(SandboxAdapter):
    """A deterministic in-process sandbox used for tests and for the demo build.

    It behaves like a venue in the three ways that matter for readiness: it can accept,
    it can reject, and it can be slow. It never touches a network.
    """

    descriptor = BrokerDescriptor(
        key="simulated",
        name="Simulated sandbox",
        sandbox_supported=True,
        live_supported=False,
        credential_fields=("api_key", "api_secret"),
        notes=(
            "In-process venue. Accepts well-formed orders, rejects non-positive "
            "quantities, and reports a fixed clock skew so clock-drift handling is "
            "exercised without a network dependency."
        ),
    )

    def __init__(self, *, skew_ms: float = 12.0) -> None:
        self.skew_ms = skew_ms

    def handshake(self, *, api_key: str, api_secret: str) -> SandboxAck:
        if not api_key or not api_secret:
            return SandboxAck(
                broker_order_id="",
                accepted=False,
                message="Both an API key and a secret are required.",
                received_at=datetime.now(UTC),
            )
        return SandboxAck(
            broker_order_id=f"sim-{self.fingerprint(api_key)}",
            accepted=True,
            message="Handshake accepted by the simulated sandbox.",
            received_at=datetime.now(UTC),
            raw={"venue": "simulated", "mode": "sandbox"},
        )

    def clock_probe(self) -> float:
        return self.skew_ms

    def submit(self, order: SandboxOrder) -> SandboxAck:
        if order.quantity <= 0:
            return SandboxAck(
                broker_order_id="",
                accepted=False,
                message="Quantity must be positive.",
                received_at=datetime.now(UTC),
            )
        return SandboxAck(
            broker_order_id=f"sim-ord-{order.client_order_id[-12:]}",
            accepted=True,
            message=(
                "Sandbox accepted the order. No fill occurred and no money moved: the "
                "paper broker in Phase 8 remains the only venue that fills."
            ),
            received_at=datetime.now(UTC),
            raw={"venue": "simulated", "echo": order.symbol, "mode": "sandbox"},
        )


_ADAPTERS: dict[str, BrokerAdapter] = {}


def adapter_for(broker: str) -> BrokerAdapter:
    key = broker.strip().lower()
    if key not in _ADAPTERS:
        registered = {adapter.descriptor.key for adapter in _ADAPTERS.values()}
        raise BrokerError(
            f"No adapter is registered for {broker!r}. Registered: {sorted(registered)}."
        )
    return _ADAPTERS[key]


def available_brokers() -> list[dict]:
    """The honest catalogue: what is sandbox-capable, and what is not built at all."""
    return [
        {
            "key": adapter.descriptor.key,
            "name": adapter.descriptor.name,
            "sandbox_supported": adapter.descriptor.sandbox_supported,
            "live_supported": adapter.descriptor.live_supported,
            "credential_fields": list(adapter.descriptor.credential_fields),
            "notes": adapter.descriptor.notes,
        }
        for adapter in sorted(_ADAPTERS.values(), key=lambda a: a.descriptor.name)
    ]


def register(adapter: BrokerAdapter) -> None:
    _ADAPTERS[adapter.descriptor.key] = adapter


def live_trading_is_impossible() -> dict:
    """The platform's own statement about its ceiling, used by the readiness report."""
    return {
        "live_trading_flag": settings.LIVE_TRADING_ENABLED,
        "real_money_flag": settings.REAL_MONEY_ENABLED,
        "live_adapter_registered": any(
            adapter.descriptor.live_supported for adapter in _ADAPTERS.values()
        ),
        "reachable": False,
        "reason": (
            "No adapter in this codebase implements live execution, and require_sandbox "
            "raises for any mode other than 'sandbox'. Enabling LIVE_TRADING_ENABLED does "
            "not create an order path."
        ),
    }


register(SimulatedSandboxAdapter())
