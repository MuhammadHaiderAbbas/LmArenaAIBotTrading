"""Tamper-evident audit chain.

These tests are adversarial on purpose: each one performs the attack a database
administrator could perform and asserts it is *detected*, not prevented. That is the
honest promise of a hash chain, and the tests should describe it precisely.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.audit import record_audit
from app.models.integrity import AuditSeal
from app.models.operations import AuditLog
from app.services import integrity


def write_entries(db, count: int = 3, prefix: str = "test") -> None:
    for index in range(count):
        record_audit(
            db,
            action=f"{prefix}.action{index}",
            actor_email="operator@aurex.ai",
            entity_type="order",
            entity_id=f"ord-{index}",
            detail=f"entry {index}",
        )
    db.commit()


def test_every_audit_entry_is_sealed_on_write(db_session) -> None:
    write_entries(db_session, 3)
    seals = list(db_session.scalars(select(AuditSeal).order_by(AuditSeal.sequence)))
    entries = list(db_session.scalars(select(AuditLog)))
    assert len(seals) == len(entries) == 3
    assert [seal.sequence for seal in seals] == [1, 2, 3]


def test_the_first_seal_points_at_genesis(db_session) -> None:
    write_entries(db_session, 1)
    seal = db_session.scalar(select(AuditSeal).order_by(AuditSeal.sequence))
    assert seal.prev_hash == integrity.GENESIS


def test_each_seal_links_to_its_predecessor(db_session) -> None:
    write_entries(db_session, 4)
    seals = list(db_session.scalars(select(AuditSeal).order_by(AuditSeal.sequence)))
    for previous, current in zip(seals, seals[1:], strict=False):
        assert current.prev_hash == previous.entry_hash


def test_a_clean_chain_verifies(db_session) -> None:
    write_entries(db_session, 5)
    report = integrity.verify_chain(db_session)
    assert report.verified is True
    assert report.entries == 5
    assert report.sealed == 5
    assert report.first_broken_at is None


def test_editing_an_entry_breaks_the_chain(db_session) -> None:
    """The attack a database administrator would actually attempt."""
    write_entries(db_session, 3)
    entry = db_session.scalars(select(AuditLog).order_by(AuditLog.created_at)).first()
    entry.detail = "something else entirely"
    db_session.commit()

    report = integrity.verify_chain(db_session)
    assert report.verified is False
    assert report.first_broken_at == entry.id
    assert "no longer hashes" in report.reason


def test_changing_the_actor_breaks_the_chain(db_session) -> None:
    write_entries(db_session, 2)
    entry = db_session.scalars(select(AuditLog).order_by(AuditLog.created_at)).first()
    entry.actor_email = "someone.else@aurex.ai"
    db_session.commit()
    assert integrity.verify_chain(db_session).verified is False


def test_deleting_a_middle_entry_breaks_the_chain(db_session) -> None:
    """A deletion is caught by the orphaned seal, not by the row count."""
    write_entries(db_session, 3)
    victim = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "test.action1")
    ).one()
    db_session.delete(victim)
    db_session.commit()
    # The seal cascades with the row, so the sequence now has a gap.
    report = integrity.verify_chain(db_session)
    assert report.verified is False
    assert "gap" in report.reason.lower() or "removed" in report.reason.lower()


def test_a_restored_entry_verifies_again(db_session) -> None:
    """Detectability, not prevention: an attacker who reverses their edit is undetectable.

    Stated plainly because the mechanism cannot do better — which is exactly why
    `export_chain` exists.
    """
    write_entries(db_session, 2)
    entry = db_session.scalars(select(AuditLog).order_by(AuditLog.created_at)).first()
    original = entry.detail
    entry.detail = "tampered"
    db_session.commit()
    assert integrity.verify_chain(db_session).verified is False

    entry.detail = original
    db_session.commit()
    assert integrity.verify_chain(db_session).verified is True


def test_an_unsealed_entry_is_reported(db_session) -> None:
    """A direct write to the audit table bypasses `record_audit` — and is visible."""
    write_entries(db_session, 2)
    db_session.add(
        AuditLog(
            action="direct.write",
            actor_email="attacker@example.com",
            outcome="success",
            detail="written past the service",
            created_at="2026-01-01T00:00:00+00:00",
        )
    )
    db_session.commit()

    report = integrity.verify_chain(db_session)
    assert report.verified is False
    assert report.unsealed_entries
    assert "without a seal" in report.reason


def test_sealing_pending_entries_adopts_pre_existing_ones(db_session) -> None:
    write_entries(db_session, 2)
    # Simulate legacy rows: remove their seals.
    db_session.query(AuditSeal).delete()
    db_session.commit()
    assert integrity.verify_chain(db_session).verified is False

    sealed = integrity.seal_pending(db_session)
    db_session.commit()
    assert sealed == 2
    assert integrity.verify_chain(db_session).verified is True


def test_hashes_are_deterministic_across_processes(db_session) -> None:
    """Same content, same chain position, same hash — or nothing can be verified."""
    payload = {"actor_id": None, "actor_email": "a@b.c", "action": "x", "entity_type": None,
               "entity_id": None, "outcome": "success", "detail": "d", "created_at": "2026-01-01T00:00:00+00:00",
               "id": "fixed-id"}
    first = integrity.compute_hash(payload, integrity.GENESIS)
    second = integrity.compute_hash(dict(payload), integrity.GENESIS)
    assert first == second
    assert len(first) == 64


def test_a_different_previous_hash_changes_the_hash(db_session) -> None:
    payload = {"action": "x", "detail": "d", "created_at": "t", "id": "i"}
    assert integrity.compute_hash(payload, "a" * 64) != integrity.compute_hash(payload, "b" * 64)


def test_the_digest_is_publishable(db_session) -> None:
    write_entries(db_session, 3)
    digest = integrity.export_chain(db_session)
    assert digest["head_sequence"] == 3
    assert len(digest["digest"]) == 64
    assert digest["head_time"]


def test_the_report_states_its_own_limitation(db_session) -> None:
    """A verifier that overstates its guarantee is worse than none."""
    write_entries(db_session, 1)
    report = integrity.verify_chain(db_session).as_dict()
    assert "cannot prevent someone with write access" in report["limitations"]
    assert report["verified"] is True


def test_an_empty_log_verifies_vacuously(db_session) -> None:
    report = integrity.verify_chain(db_session)
    assert report.verified is True
    assert report.entries == 0


@pytest.fixture
def db_session(client):
    from app.database.session import SessionLocal

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
