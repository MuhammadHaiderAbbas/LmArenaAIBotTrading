"""Model registry.

Importing this package registers every mapper on the shared declarative Base so
that `Base.metadata.create_all` and Alembic autogenerate see the whole schema.
"""

from app.models.account import AccountBalance, AccountStatus, Environment, TradingAccount
from app.models.identity import User, UserRole, UserSession, UserSettings
from app.models.ledger import (
    LedgerAccount,
    LedgerAccountKind,
    LedgerAccountType,
    LedgerEntry,
    LedgerTransaction,
    ReconciliationRecord,
    TransactionType,
)
from app.models.operations import (
    AuditLog,
    IntegrationHealth,
    Notification,
    Severity,
    SystemEvent,
)

__all__ = [
    "AccountBalance",
    "AccountStatus",
    "AuditLog",
    "Environment",
    "IntegrationHealth",
    "LedgerAccount",
    "LedgerAccountKind",
    "LedgerAccountType",
    "LedgerEntry",
    "LedgerTransaction",
    "Notification",
    "ReconciliationRecord",
    "Severity",
    "SystemEvent",
    "TradingAccount",
    "TransactionType",
    "User",
    "UserRole",
    "UserSession",
    "UserSettings",
]
