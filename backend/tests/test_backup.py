"""Backups and the recovery drill — the part of DR that can actually be tested here.

A backup nobody restored is a guess. These tests take a real backup of the live test
database, verify its checksum against the manifest, restore it into a scratch copy, and
confirm the audit seals survived the round trip.
"""

from __future__ import annotations

import pytest

from app.services import backup


@pytest.fixture
def backup_dir(tmp_path):
    return tmp_path / "backups"


def test_a_backup_produces_a_file_and_a_manifest_entry(backup_dir) -> None:
    entry = backup.backup_database(destination_dir=backup_dir)
    assert entry["bytes"] > 0
    assert len(entry["sha256"]) == 64
    assert entry["engine"] == "sqlite"
    listed = backup.list_backups(directory=backup_dir)
    assert listed[0]["sha256"] == entry["sha256"]


def test_a_fresh_backup_verifies(backup_dir) -> None:
    entry = backup.backup_database(destination_dir=backup_dir)
    result = backup.verify_backup(entry["path"])
    assert result["ok"] is True
    assert result["sqlite_integrity"] == "ok"
    assert result["tables"] > 40


def test_the_backup_carries_the_audit_trail(
    client, auth_headers, admin_headers, backup_dir
) -> None:
    """Log in so there is something in the trail, then prove the backup kept it."""
    auth_headers()
    entry = backup.backup_database(destination_dir=backup_dir, label="trail")
    result = backup.verify_backup(entry["path"])
    assert result["ok"] is True
    assert result["row_counts"]["audit_logs"] > 0
    assert result["unsealed_audit_entries"] == 0


def test_a_corrupted_backup_fails_verification(backup_dir) -> None:
    """Rot on disk must be caught before an incident, not during one."""
    entry = backup.backup_database(destination_dir=backup_dir)
    from pathlib import Path

    target = Path(entry["path"])
    with target.open("ab") as handle:
        handle.write(b"corruption")
    assert backup.verify_backup(target)["ok"] is False


def test_a_missing_backup_fails_cleanly(backup_dir) -> None:
    result = backup.verify_backup(backup_dir / "does-not-exist.db")
    assert result["ok"] is False
    assert result["reason"] == "Backup file is missing."


def test_the_drill_restores_and_verifies(backup_dir) -> None:
    result = backup.recovery_drill(directory=backup_dir)
    assert result["passed"] is True
    assert result["restore"]["restored"] is True
    assert result["restore"]["verified_after_restore"] is True


def test_the_drill_states_what_it_does_not_prove(backup_dir) -> None:
    """Honesty requirement: a SQLite dump test is not a PITR guarantee."""
    note = backup.recovery_drill(directory=backup_dir)["rpo_note"]
    assert "does not prove a point-in-time recovery window" in note


def test_an_empty_manifest_lists_nothing(backup_dir) -> None:
    backup_dir.mkdir(parents=True)
    assert backup.list_backups(directory=backup_dir) == []


def test_a_corrupt_manifest_line_is_skipped(backup_dir) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "manifest.jsonl").write_text('{"created_at": "bad"\nnot json at all\n')
    assert backup.list_backups(directory=backup_dir) == []


# ------------------------------------------------------------------- over HTTP


def test_only_staff_can_list_backups(client, auth_headers) -> None:
    assert client.get("/api/v1/operations/backups", headers=auth_headers()).status_code == 403


def test_only_staff_can_run_a_drill(client, auth_headers) -> None:
    assert (
        client.post("/api/v1/operations/backups/drill", headers=auth_headers()).status_code == 403
    )


def test_staff_can_create_a_backup_over_http(client, admin_headers, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "bk")
    response = client.post("/api/v1/operations/backups", headers=admin_headers())
    assert response.status_code == 200, response.text
    assert response.json()["bytes"] > 0
    entries = client.get("/api/v1/operations/backups", headers=admin_headers()).json()
    assert entries["count"] == 1


def test_verify_requires_a_path(client, admin_headers) -> None:
    assert (
        client.post(
            "/api/v1/operations/backups/verify", headers=admin_headers(), json={}
        ).status_code
        == 422
    )


def test_the_drill_is_audited(client, admin_headers, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "bk")
    assert client.post("/api/v1/operations/backups/drill", headers=admin_headers()).json()["passed"]
    trail = client.get(
        "/api/v1/operations/audit", headers=admin_headers(), params={"limit": 50}
    ).json()
    assert "backup.drill" in {entry["action"] for entry in trail["entries"]}
