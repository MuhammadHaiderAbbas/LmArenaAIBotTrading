"""Prevent committing secrets: blocks .env files, keys and large binaries."""

from __future__ import annotations

import re
import sys

BLOCKED_PATTERNS = [
    (r"^\.env$", ".env files are not committed — use .env.example"),
    (r"^\.env\..+$", ".env files are not committed — use .env.example"),
    (r".*\.(pem|key|p12|pfx)$", "Private keys must never be committed"),
    (r".*\.(db|sqlite3)$", "Local databases must not be committed"),
]

SECRET_CONTENT = re.compile(
    r"(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|sk-[A-Za-z0-9]{24,}|ghp_[A-Za-z0-9]{30,})"
)


def main() -> int:
    failures: list[str] = []
    for path in sys.argv[1:]:
        for pattern, reason in BLOCKED_PATTERNS:
            if path == ".env.example":
                continue
            if re.match(pattern, path):
                failures.append(f"{path}: {reason}")
                break
        else:
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    if SECRET_CONTENT.search(handle.read(200_000)):
                        failures.append(f"{path}: looks like it contains a credential")
            except (OSError, UnicodeDecodeError):
                continue

    if failures:
        print("Commit blocked by AUREX AI secret guard:\n  " + "\n  ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
