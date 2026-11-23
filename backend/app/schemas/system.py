"""Pydantic schemas for system health and operations endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    environment: str
    database: str
    live_trading_enabled: bool
    real_money_enabled: bool
    timestamp: datetime


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_email: str | None
    action: str
    entity_type: str | None
    entity_id: str | None
    outcome: str
    ip_address: str | None
    detail: str
    created_at: str


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    body: str
    category: str
    is_read: bool
    created_at: datetime


class IntegrationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    category: str
    status: str
    latency_ms: int | None
    message: str


class SystemStatus(BaseModel):
    environment: str
    version: str
    uptime_seconds: float
    counts: dict[str, int]
    guardrails: dict[str, Any]
