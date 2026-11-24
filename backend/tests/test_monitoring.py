"""Operational monitoring: the four signals the blueprint names.

Stale data, API failures, abnormal order activity and model degradation — plus the two
this platform added because it has them: an open circuit breaker and a broken audit
chain.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.account import Environment, TradingAccount
from app.models.execution import Order, OrderSide, OrderStatus
from app.models.identity import User
from app.models.market import (
    DataQualityEvent,
    Instrument,
    MarketCandle,
    QualityEventType,
    QualitySeverity,
    Timeframe,
    Venue,
)
from app.services import ledger_service, monitoring


def make_account(db, email: str = "mon@aurex.ai", funding: float = 50_000.0) -> TradingAccount:
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        user = User(
            email=email, password_hash="x", full_name="Mon", is_active=True, is_verified=True
        )
        db.add(user)
        db.flush()
    account = TradingAccount(
        user_id=user.id, name=f"Paper {email}", environment=Environment.PAPER, base_currency="GBP"
    )
    db.add(account)
    db.flush()
    ledger_service.open_account_with_funding(db, account=account, amount=funding)
    db.flush()
    return account


def seed_instrument(
    db, symbol: str = "MON/T", *, bars: int = 30, age_hours: float = 0.0
) -> Instrument:
    venue = db.query(Venue).first()
    if venue is None:
        venue = Venue(code="MONV", name="Mon venue", asset_class="forex")
        db.add(venue)
        db.flush()
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if instrument is None:
        instrument = Instrument(
            symbol=symbol,
            name=symbol,
            asset_class="forex",
            venue_id=venue.id,
            base_currency="MON",
            quote_currency="T",
            is_tradable=True,
            status="active",
        )
        db.add(instrument)
        db.flush()
    newest = datetime.now(UTC) - timedelta(hours=age_hours)
    for index in range(bars):
        when = newest - timedelta(days=bars - 1 - index)
        db.add(
            MarketCandle(
                instrument_id=instrument.id,
                timeframe=Timeframe.D1,
                open_time=when,
                close_time=when + timedelta(hours=23),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1_000_000.0,
                provider="test",
                ingested_at=datetime.now(UTC),
            )
        )
    db.flush()
    return instrument


# ------------------------------------------------------------ data freshness


def test_fresh_data_passes(db_session) -> None:
    seed_instrument(db_session, "MON/FRESH")
    finding = monitoring.check_data_freshness(db_session)
    assert finding.status == "ok"


def test_stale_data_is_a_critical_finding(db_session) -> None:
    """Stale bars are how an execution engine fills at a price nobody could get."""
    seed_instrument(db_session, "MON/STALE", age_hours=72)
    finding = monitoring.check_data_freshness(db_session)
    assert finding.status in {"warning", "critical"}
    assert finding.detail["instruments"]
    assert finding.detail["instruments"][0]["symbol"] == "MON/STALE"


def test_an_instrument_with_no_bars_is_reported(db_session) -> None:
    instrument = seed_instrument(db_session, "MON/EMPTY", bars=1)
    db_session.query(MarketCandle).filter(MarketCandle.instrument_id == instrument.id).delete()
    db_session.flush()
    finding = monitoring.check_data_freshness(db_session)
    assert finding.detail["instruments"][0]["reason"] == "no bars"


def test_no_instruments_is_a_warning_not_a_pass(db_session) -> None:
    finding = monitoring.check_data_freshness(db_session)
    assert finding.status == "warning"
    assert "No active instruments" in finding.message


# -------------------------------------------------------------- data quality


def test_a_critical_quality_event_is_surfaced(db_session) -> None:
    instrument = seed_instrument(db_session, "MON/QUAL")
    db_session.add(
        DataQualityEvent(
            instrument_id=instrument.id,
            provider="test",
            event_type=QualityEventType.GAP,
            severity=QualitySeverity.CRITICAL,
            message="Gap detected",
        )
    )
    db_session.flush()
    finding = monitoring.check_quality_events(db_session)
    assert finding.status == "critical"
    assert finding.detail["critical"] == 1


def test_no_quality_events_is_ok(db_session) -> None:
    assert monitoring.check_quality_events(db_session).status == "ok"


# ----------------------------------------------------------- order activity


def test_a_normal_order_rate_passes(db_session) -> None:
    account = make_account(db_session, "rate@aurex.ai")
    seed_instrument(db_session, "MON/RATE")
    for index in range(3):
        db_session.add(
            Order(
                trading_account_id=account.id,
                instrument_symbol="MON/RATE",
                client_order_id=f"mon-{index}",
                side=OrderSide.BUY,
                order_type="market",
                status=OrderStatus.FILLED,
                quantity=1,
            )
        )
    db_session.flush()
    finding = monitoring.check_order_activity(db_session, account=account)
    assert finding.status == "ok"
    assert finding.detail["orders_last_hour"] == 3


def test_a_burst_of_orders_is_critical(db_session) -> None:
    """The signal that a loop has run away."""
    account = make_account(db_session, "burst@aurex.ai")
    for index in range(monitoring.ABNORMAL_ORDER_BURST + 5):
        db_session.add(
            Order(
                trading_account_id=account.id,
                instrument_symbol="MON/BURST",
                client_order_id=f"burst-{index}",
                side=OrderSide.BUY,
                order_type="market",
                status=OrderStatus.FILLED,
                quantity=1,
            )
        )
    db_session.flush()
    finding = monitoring.check_order_activity(db_session, account=account)
    assert finding.status == "critical"
    assert "burst" in finding.message.lower()


def test_a_high_rejection_rate_is_a_warning(db_session) -> None:
    """A rejection streak usually means a mis-set limit or broken data, not a bad day."""
    account = make_account(db_session, "reject@aurex.ai")
    for index in range(4):
        db_session.add(
            Order(
                trading_account_id=account.id,
                instrument_symbol="MON/REJ",
                client_order_id=f"rej-{index}",
                side=OrderSide.BUY,
                order_type="market",
                status=OrderStatus.REJECTED if index < 3 else OrderStatus.FILLED,
                quantity=1,
            )
        )
    db_session.flush()
    finding = monitoring.check_order_activity(db_session, account=account)
    assert finding.status == "warning"
    assert finding.detail["rejected"] == 3


# ---------------------------------------------------------------- breakers


def test_an_active_breaker_is_critical(db_session) -> None:
    from app.services.risk import breakers

    account = make_account(db_session, "brk@aurex.ai")
    breakers.emergency_stop(db_session, account=account, reason="Monitoring test.")
    db_session.flush()
    finding = monitoring.check_breakers(db_session)
    assert finding.status == "critical"
    assert finding.detail["active"] == 1


def test_no_breaker_is_ok(db_session) -> None:
    assert monitoring.check_breakers(db_session).status == "ok"


# --------------------------------------------------------------- audit chain


def test_a_broken_audit_chain_is_critical(db_session) -> None:
    from sqlalchemy import select

    from app.core.audit import record_audit
    from app.models.operations import AuditLog

    record_audit(db_session, action="mon.a", actor_email="x@y.z", detail="one")
    db_session.commit()
    entry = db_session.scalars(select(AuditLog)).first()
    entry.detail = "changed"
    db_session.commit()

    finding = monitoring.check_audit_chain(db_session)
    assert finding.status == "critical"
    assert "does not verify" in finding.message


def test_a_clean_chain_passes(db_session) -> None:
    from app.core.audit import record_audit

    record_audit(db_session, action="mon.b", actor_email="x@y.z", detail="fine")
    db_session.commit()
    assert monitoring.check_audit_chain(db_session).status == "ok"


# ------------------------------------------------------------ reconciliation


def test_reconciliation_agreement_passes(db_session) -> None:
    account = make_account(db_session, "recon@aurex.ai")
    assert monitoring.check_execution_reconciliation(db_session, account).status == "ok"


# ----------------------------------------------------------------- the report


def test_the_report_summarises_severity(db_session) -> None:
    account = make_account(db_session, "report@aurex.ai")
    seed_instrument(db_session, "MON/REPORT")
    report = monitoring.health_report(db_session, account=account)
    assert report["status"] in {"ok", "warning", "critical"}
    assert report["checks_run"] == len(report["findings"])
    assert (
        report["counts"]["ok"] + report["counts"]["warning"] + report["counts"]["critical"]
        == report["checks_run"]
    )
    assert {"data_freshness", "order_activity", "audit_chain", "reconciliation"} <= {
        finding["check"] for finding in report["findings"]
    }


def test_the_worst_finding_determines_the_overall_status(db_session) -> None:
    account = make_account(db_session, "worst@aurex.ai")
    seed_instrument(db_session, "MON/WORST", age_hours=100)
    report = monitoring.health_report(db_session, account=account)
    assert report["status"] in {"warning", "critical"}
    assert report["counts"]["ok"] < report["checks_run"]


def test_monitoring_never_mutates_state(db_session) -> None:
    """A monitor that acts is a risk engine. Phase 9 has one; this is not it."""
    account = make_account(db_session, "nomutate@aurex.ai")
    seed_instrument(db_session, "MON/NOMUT", age_hours=100)
    before = db_session.query(Order).count()
    monitoring.health_report(db_session, account=account)
    db_session.flush()
    assert db_session.query(Order).count() == before


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
