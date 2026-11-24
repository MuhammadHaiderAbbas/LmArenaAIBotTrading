"""Tamper-evident audit chain.

The blueprint asks for "tamper-evident audit logs". A log anyone with database access
can edit is not evidence — it is a suggestion. This module makes editing *detectable*
by chaining each entry to its predecessor:

```
entry_hash = sha256(prev_hash | actor | action | entity | outcome | detail | created_at)
```

Three consequences, each deliberate:

1. **Editing any row breaks every subsequent link**, so `verify_chain` reports the
   first broken entry and the count of entries after it.
2. **Deleting a row breaks the chain** at the next entry, because its `prev_hash`
   points at something that no longer verifies.
3. **Re-sealing the whole tail is possible for someone with write access** — this is a
   hash chain, not a Merkle tree anchored to an external timestamp. That limitation is
   stated rather than hidden, and `export_chain` produces a digest an operator can
   publish elsewhere to make wholesale rewriting detectable too.

Verification is read-only and returns a report; it never "repairs" a chain, because a
verifier that silently fixes what it checks is not a verifier.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.integrity import AuditSeal
from app.models.operations import AuditLog

#: Fields covered by the hash. Adding a field to AuditLog means bumping `SCHEMA_VERSION`
#: so old seals are not silently re-interpreted under new rules.
HASHED_FIELDS = (
    "actor_id",
    "actor_email",
    "action",
    "entity_type",
    "entity_id",
    "outcome",
    "detail",
    "created_at",
)
SCHEMA_VERSION = 1
GENESIS = "0" * 64


def _canonical(payload: dict) -> str:
    """Stable JSON: sorted keys, no whitespace, values coerced to strings.

    Determinism matters more than readability — the same entry must hash identically
    in two processes or the chain cannot be verified at all.
    """
    normalised = {
        key: ("" if payload.get(key) is None else str(payload[key])) for key in HASHED_FIELDS
    }
    return json.dumps(normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_hash(payload: dict, prev_hash: str) -> str:
    body = f"{prev_hash}|{_canonical(payload)}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def seal_entry(db: Session, entry: AuditLog) -> AuditSeal:
    """Attach a seal to a freshly recorded audit entry.

    Called by `record_audit`, so an unsealed entry can only exist if something wrote
    the audit table directly — which is itself worth detecting, and `verify_chain`
    does.
    """
    previous = db.scalar(select(AuditSeal).order_by(AuditSeal.sequence.desc()).limit(1))
    prev_hash = previous.entry_hash if previous else GENESIS
    sequence = (previous.sequence + 1) if previous else 1

    payload = {field_name: getattr(entry, field_name, None) for field_name in HASHED_FIELDS}
    payload["id"] = entry.id
    entry_hash = compute_hash(payload, prev_hash)

    seal = AuditSeal(
        audit_log_id=entry.id,
        sequence=sequence,
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        schema_version=SCHEMA_VERSION,
        sealed_at=datetime.now(UTC),
    )
    db.add(seal)
    db.flush()
    return seal


@dataclass
class ChainVerification:
    verified: bool
    entries: int
    sealed: int
    first_broken_at: str | None = None
    reason: str = ""
    unsealed_entries: list[str] = field(default_factory=list)
    digest: str = ""

    def as_dict(self) -> dict:
        return {
            "verified": self.verified,
            "entries": self.entries,
            "sealed": self.sealed,
            "first_broken_at": self.first_broken_at,
            "reason": self.reason,
            "unsealed_entries": self.unsealed_entries[:20],
            "digest": self.digest,
            "schema_version": SCHEMA_VERSION,
            "limitations": (
                "A hash chain detects edits and deletions. It cannot prevent someone with "
                "write access from re-sealing the whole tail; publishing `digest` elsewhere "
                "is what makes that detectable too."
            ),
        }


def verify_chain(db: Session) -> ChainVerification:
    """Walk the chain and report the first break.

    A missing seal, a broken link, or a hash that no longer matches its entry all fail
    the report. The chain is never repaired here.
    """
    seals = list(db.scalars(select(AuditSeal).order_by(AuditSeal.sequence.asc())))
    entries = {entry.id: entry for entry in db.scalars(select(AuditLog))}
    unsealed = [
        entry_id for entry_id in entries if entry_id not in {seal.audit_log_id for seal in seals}
    ]

    if not seals:
        return ChainVerification(
            verified=not entries,
            entries=len(entries),
            sealed=0,
            reason="" if not entries else "Audit entries exist but none is sealed.",
            unsealed_entries=unsealed,
            digest=GENESIS,
        )

    prev_hash = GENESIS
    for index, seal in enumerate(seals, start=1):
        if seal.sequence != index:
            return ChainVerification(
                verified=False,
                entries=len(entries),
                sealed=len(seals),
                first_broken_at=seal.audit_log_id,
                reason=f"Sequence gap: expected {index}, found {seal.sequence} — entries were removed.",
                unsealed_entries=unsealed,
                digest=seals[-1].entry_hash,
            )
        if seal.prev_hash != prev_hash:
            return ChainVerification(
                verified=False,
                entries=len(entries),
                sealed=len(seals),
                first_broken_at=seal.audit_log_id,
                reason=f"Broken link at sequence {seal.sequence}: prev_hash does not match the previous seal.",
                unsealed_entries=unsealed,
                digest=seals[-1].entry_hash,
            )

        entry = entries.get(seal.audit_log_id)
        if entry is None:
            return ChainVerification(
                verified=False,
                entries=len(entries),
                sealed=len(seals),
                first_broken_at=seal.audit_log_id,
                reason=f"Sealed entry missing at sequence {seal.sequence}: the audit row was deleted.",
                unsealed_entries=unsealed,
                digest=seals[-1].entry_hash,
            )

        payload = {field_name: getattr(entry, field_name, None) for field_name in HASHED_FIELDS}
        payload["id"] = entry.id
        if compute_hash(payload, prev_hash) != seal.entry_hash:
            return ChainVerification(
                verified=False,
                entries=len(entries),
                sealed=len(seals),
                first_broken_at=seal.audit_log_id,
                reason=f"Content changed at sequence {seal.sequence}: the entry no longer hashes to its seal.",
                unsealed_entries=unsealed,
                digest=seals[-1].entry_hash,
            )
        prev_hash = seal.entry_hash

    return ChainVerification(
        verified=not unsealed,
        entries=len(entries),
        sealed=len(seals),
        reason=""
        if not unsealed
        else f"{len(unsealed)} audit entr(y/ies) were written without a seal.",
        unsealed_entries=unsealed,
        digest=seals[-1].entry_hash,
    )


def export_chain(db: Session, *, limit: int = 1000) -> dict:
    """A publishable digest of the chain.

    An operator can post `digest` somewhere the database owner does not control. After
    that, re-sealing the tail is detectable by comparing the new digest to the old one.
    """
    seals = list(db.scalars(select(AuditSeal).order_by(AuditSeal.sequence.desc()).limit(limit)))
    if not seals:
        return {"digest": GENESIS, "head_sequence": 0, "head_time": None}
    head = seals[0]
    return {
        "digest": head.entry_hash,
        "head_sequence": head.sequence,
        "head_time": head.sealed_at.isoformat(),
    }


def seal_pending(db: Session, *, limit: int = 500) -> int:
    """Seal audit entries written before the chain existed or by a direct write.

    Used once at startup so a pre-existing trail is not reported as tampered when it was
    merely older than the mechanism. Entries are sealed in `created_at` order so the
    chain's timeline matches the log's.
    """
    sealed_ids = {seal.audit_log_id for seal in db.scalars(select(AuditSeal))}
    pending = [
        entry
        for entry in db.scalars(select(AuditLog).order_by(AuditLog.created_at.asc()).limit(limit))
        if entry.id not in sealed_ids
    ]
    for entry in pending:
        seal_entry(db, entry)
    if pending:
        db.flush()
    return len(pending)
