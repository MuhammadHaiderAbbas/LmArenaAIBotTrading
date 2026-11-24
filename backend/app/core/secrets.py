"""Secrets at rest.

The blueprint asks for two things that meet here: *encryption at rest for sensitive
data* and *a secrets manager — never hardcode secrets*. This module is the small piece
of the first one the platform actually needs today: provider keys, broker credentials
and TOTP secrets must not sit in the database as readable text.

What this is: AES-grade symmetric encryption derived from `SECRET_KEY`… except that the
standard library has no AES, so this uses HMAC-SHA256 as a keystream generator with a
per-value random nonce and a MAC over the ciphertext (encrypt-then-MAC). That is a
legitimate construction — it is what NaCl's `secretbox` lineage does — and it is honest
about being hand-rolled for a stdlib-only dependency set.

What this is *not*: a substitute for a real secrets manager in production. The
production path is documented in `docs/architecture/phase-11.md`: `FILE`-based mount or
the platform's secret store, with this module only ever wrapping values that must be
persisted alongside a row.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from app.core.config import settings

VERSION = "v1"
NONCE_BYTES = 16
TAG_BYTES = 32


@lru_cache(maxsize=8)
def _keys(secret: str) -> tuple[bytes, bytes]:
    """Derive independent encryption and authentication keys from `SECRET_KEY`.

    Separate keys so the keystream and the MAC cannot be played off against each other.
    """
    root = secret.encode()
    enc = hmac.new(root, b"aurex/secrets/encryption", hashlib.sha256).digest()
    mac = hmac.new(root, b"aurex/secrets/authentication", hashlib.sha256).digest()
    return enc, mac


def _keystream(key: bytes, nonce: bytes, length: int) -> bytes:
    blocks = []
    counter = 0
    while sum(len(block) for block in blocks) < length:
        blocks.append(hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    return b"".join(blocks)[:length]


def encrypt(plaintext: str, *, secret: str | None = None) -> str:
    """Return `v1.nonce.ciphertext.tag`, all base64url. Safe to store in a text column."""
    enc_key, mac_key = _keys(secret or settings.SECRET_KEY)
    nonce = os.urandom(NONCE_BYTES)
    data = plaintext.encode()
    ciphertext = bytes(
        a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data)), strict=True)
    )
    tag = hmac.new(mac_key, VERSION.encode() + nonce + ciphertext, hashlib.sha256).digest()
    return ".".join([VERSION, *(_b64(part) for part in (nonce, ciphertext, tag))])


def decrypt(token: str, *, secret: str | None = None) -> str:
    """Inverse of `encrypt`. Raises `ValueError` on any tampering or key mismatch."""
    try:
        version, nonce_b64, ciphertext_b64, tag_b64 = token.split(".")
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError("Malformed encrypted value.") from exc
    if version != VERSION:
        raise ValueError(f"Unsupported secret version {version!r}.")
    enc_key, mac_key = _keys(secret or settings.SECRET_KEY)
    nonce = _unb64(nonce_b64)
    ciphertext = _unb64(ciphertext_b64)
    tag = _unb64(tag_b64)
    expected = hmac.new(mac_key, VERSION.encode() + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Secret failed authentication — wrong key or tampered value.")
    data = bytes(
        a ^ b for a, b in zip(ciphertext, _keystream(enc_key, nonce, len(ciphertext)), strict=True)
    )
    return data.decode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def redact(value: str | None, *, keep: int = 4) -> str:
    """Show the last `keep` characters of a credential and nothing else."""
    if not value:
        return "—"
    if len(value) <= keep:
        return "*" * len(value)
    return "…" + value[-keep:]
