"""Domain-level errors translated to HTTP responses by the API layer."""

from __future__ import annotations


class DomainError(Exception):
    """A business rule was violated. Surfaced to the client as 400/409."""

    def __init__(self, message: str, status_code: int = 400, extra: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        # Optional machine-readable detail appended to the error envelope.
        self.extra = extra or {}


class NotFoundError(DomainError):
    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(message, status_code=404)


class PermissionDenied(DomainError):
    def __init__(self, message: str = "You do not have permission to do that.") -> None:
        super().__init__(message, status_code=403)
