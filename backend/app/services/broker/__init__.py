"""Broker integration — sandbox only, by construction.

The blueprint's Phase 12 asks for *broker sandbox integrations*. It does not ask for a
live broker, and the UK regulatory map in §13 says why: live execution means client
money, custody, permissions and a written perimeter assessment that this project does
not have.

So the design makes the safe state the *structural* one rather than the *configured* one:

* `BrokerAdapter` subclasses exist for sandbox handshakes and a paper broker;
* `LIVE_TRADING_ENABLED` is checked in **one** place, `require_sandbox`, and the live
  adapter class does not exist at all. Enabling the flag changes what the readiness
  report says; it does not create an order path.

That is the difference between a kill switch and a wall. A kill switch can be flipped by
mistake; a wall has to be built.
"""

from app.services.broker.base import (
    BrokerAdapter,
    BrokerError,
    LiveTradingRefused,
    SandboxAdapter,
    adapter_for,
    available_brokers,
    require_sandbox,
)
from app.services.broker.sandbox import connect, disconnect, probe, submit_to_sandbox

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "LiveTradingRefused",
    "SandboxAdapter",
    "adapter_for",
    "available_brokers",
    "require_sandbox",
    "connect",
    "disconnect",
    "probe",
    "submit_to_sandbox",
]
