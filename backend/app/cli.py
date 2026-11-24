"""Operator entry point for backups and recovery drills.

    python -m app.cli backup                 # take a backup
    python -m app.cli backups                # list them
    python -m app.cli verify <path>          # check a backup is intact and trustworthy
    python -m app.cli drill                  # back up, restore, verify — the real test
    python -m app.cli audit-verify           # walk the audit hash chain
    python -m app.cli seal-audit             # adopt pre-existing audit rows into the chain

Every command prints JSON so it can be dropped into a cron job or a CI step. Exit code
is 1 when a check fails, which is what actually makes an automated drill useful.
"""

from __future__ import annotations

import json
import sys

from app.database.session import SessionLocal
from app.services import backup, integrity


def _out(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help"}:
        _out(
            {
                "commands": [
                    "backup",
                    "backups",
                    "verify <path>",
                    "drill",
                    "audit-verify",
                    "seal-audit",
                ]
            }
        )
        return 0

    command, *rest = args

    if command == "backup":
        _out(backup.backup_database(label="manual"))
        return 0

    if command == "backups":
        _out({"backups": backup.list_backups()})
        return 0

    if command == "verify":
        if not rest:
            _out({"error": "verify needs a path"})
            return 2
        result = backup.verify_backup(rest[0])
        _out(result)
        return 0 if result["ok"] else 1

    if command == "drill":
        result = backup.recovery_drill()
        _out(result)
        return 0 if result["passed"] else 1

    if command == "audit-verify":
        db = SessionLocal()
        try:
            report = integrity.verify_chain(db).as_dict()
        finally:
            db.close()
        _out(report)
        return 0 if report["verified"] else 1

    if command == "seal-audit":
        db = SessionLocal()
        try:
            sealed = integrity.seal_pending(db)
            db.commit()
        finally:
            db.close()
        _out({"sealed": sealed})
        return 0

    _out({"error": f"unknown command {command!r}"})
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
