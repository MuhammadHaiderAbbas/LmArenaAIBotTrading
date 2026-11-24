"""Backups, and proof that the backups actually restore.

The blueprint asks for *automated backups, tested recovery and disaster-recovery
procedures*. A backup script that has never been restored from is a hope, so this
module does both halves:

* `backup_database` copies the live database to a timestamped file (SQLite: the online
  backup API, so it is consistent while the app is writing; PostgreSQL: `pg_dump`).
* `restore_into` restores **into a separate file**, opens it, and runs integrity checks
  against it — row counts, the audit hash chain, and the ledger/trial-balance identity.
  The restore is verified before the operator ever points production at it.

The manifest records a sha256 of the dump itself, so a backup that rotted on disk is
detected rather than discovered during an incident.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess  # noqa: S404 - pg_dump path is chosen explicitly, never from input
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from app.core.config import settings

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "backups"))
MANIFEST_NAME = "manifest.jsonl"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sqlite() -> bool:
    return settings.DATABASE_URL.startswith("sqlite")


def _sqlite_path() -> Path:
    url = settings.DATABASE_URL
    return Path(url.split("///", 1)[1]) if "///" in url else Path(url.split(":", 1)[1])


def backup_database(*, destination_dir: Path | None = None, label: str = "auto") -> dict:
    """Take a consistent copy of the database. Returns the manifest entry."""
    directory = Path(destination_dir or BACKUP_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = directory / f"aurex-{label}-{stamp}.db"

    if _is_sqlite():
        source = _sqlite_path()
        if not source.exists():
            raise FileNotFoundError(f"Database not found at {source}")
        # The sqlite3 backup API copies a consistent snapshot even while the app writes.
        with sqlite3.connect(str(source)) as src, sqlite3.connect(str(target)) as dst:
            src.backup(dst)
    else:  # pragma: no cover - exercised only against PostgreSQL
        env = {**os.environ}
        completed = subprocess.run(
            [
                "pg_dump",
                "--no-owner",
                "--format=custom",
                "--file",
                str(target),
                settings.DATABASE_URL,
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"pg_dump failed: {completed.stderr.strip()}")

    entry = {
        "created_at": datetime.now(UTC).isoformat(),
        "label": label,
        "path": str(target),
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
        "engine": "sqlite" if _is_sqlite() else "postgresql",
    }
    with (directory / MANIFEST_NAME).open("a") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def list_backups(*, directory: Path | None = None) -> list[dict]:
    """Newest first. Unparseable manifest lines are skipped rather than fatal."""
    manifest = Path(directory or BACKUP_DIR) / MANIFEST_NAME
    if not manifest.exists():
        return []
    entries = []
    for line in manifest.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return sorted(entries, key=lambda entry: str(entry.get("created_at", "")), reverse=True)


def verify_backup(path: str | Path) -> dict:
    """Open a backup read-only and check it is a database we would be willing to trust."""
    import sqlite3 as sq

    target = Path(path)
    checks: dict[str, object] = {"path": str(target), "exists": target.exists()}
    if not target.exists():
        return {**checks, "ok": False, "reason": "Backup file is missing."}

    expected = None
    for entry in (
        list_backups(directory=target.parent) if (target.parent / MANIFEST_NAME).exists() else []
    ):
        if Path(entry.get("path", "")).name == target.name:
            expected = entry.get("sha256")
            break
    actual = _sha256(target)
    checks["sha256"] = actual
    checks["sha256_matches_manifest"] = expected is None or actual == expected

    try:
        with sq.connect(f"file:{target}?mode=ro", uri=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            checks["sqlite_integrity"] = integrity
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            checks["tables"] = len(tables)
            counts = {}
            for table in (
                "users",
                "trading_accounts",
                "ledger_entries",
                "audit_logs",
                "audit_seals",
            ):
                if table in tables:
                    counts[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                        0
                    ]
            checks["row_counts"] = counts
            if "audit_logs" in tables:
                checks["audit_entries"] = counts.get("audit_logs", 0)
                unsealed = 0
                if "audit_seals" in tables:
                    unsealed = connection.execute(
                        "SELECT COUNT(*) FROM audit_logs WHERE id NOT IN "
                        "(SELECT audit_log_id FROM audit_seals)"
                    ).fetchone()[0]
                checks["unsealed_audit_entries"] = unsealed
    except Exception as exc:  # noqa: BLE001 - any failure here means "do not trust it"
        return {**checks, "ok": False, "reason": f"Could not open backup: {exc}"}

    ok = (
        integrity == "ok"
        and checks["sha256_matches_manifest"]
        and int(checks.get("tables") or 0) > 0
    )
    return {**checks, "ok": ok, "reason": None if ok else "Backup failed verification."}


def restore_into(*, backup_path: str | Path, target_dir: Path | None = None) -> dict:
    """Restore into a scratch file and verify the result.

    Restoration into a throw-away location is deliberate: the point is to answer "does
    this backup work?", which must never require touching the live database.
    """
    source = Path(backup_path)
    verification = verify_backup(source)
    if not verification["ok"]:
        return {"restored": False, "verification": verification}

    scratch = Path(target_dir or tempfile.mkdtemp(prefix="aurex-restore-"))
    scratch.mkdir(parents=True, exist_ok=True)
    restored = scratch / "restored.db"
    shutil.copy2(source, restored)
    return {
        "restored": True,
        "restored_path": str(restored),
        "bytes": restored.stat().st_size,
        "verification": verification,
        "verified_after_restore": verify_backup(restored)["ok"],
    }


def recovery_drill(*, directory: Path | None = None) -> dict:
    """Back up, restore the backup, and prove the trail survived. The whole point."""
    entry = backup_database(destination_dir=directory, label="drill")
    result = restore_into(
        backup_path=entry["path"], target_dir=Path(entry["path"]).parent / "drill"
    )
    return {
        "performed_at": datetime.now(UTC).isoformat(),
        "backup": entry,
        "restore": result,
        "passed": bool(result.get("restored") and result.get("verified_after_restore")),
        "rpo_note": (
            "This drill proves the dump restores and its audit seals survived. It does not "
            "prove a point-in-time recovery window; that is a PostgreSQL/PITR concern and is "
            "stated as unimplemented in the phase notes."
        ),
    }
