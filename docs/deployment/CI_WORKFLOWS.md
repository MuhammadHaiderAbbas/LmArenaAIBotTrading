# CI workflow files (held back from the Contents-only push)

GitHub rejects any change to `.github/workflows/` from a token without the
`workflow` scope, so the workflow below is kept here as the source of truth and
added through the web UI or a push from a machine with that scope. It is written
for Phases 1–6: it typechecks, lints, guards secrets, proves migrations round-trip
and runs the full test suite.

To activate it, create `.github/workflows/ci.yml` with exactly this content.

## `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  backend:
    name: Backend — lint, secrets, tests
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    env:
      ENVIRONMENT: test
      # Test-only values. Nothing here is a real credential and the secret guard
      # below asserts that none are committed.
      SECRET_KEY: ci-test-secret-key-not-for-production-0123456789
      DATABASE_URL: sqlite:///./ci.db
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/requirements.txt

      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -r requirements.txt
          pip install ruff pytest

      - name: Lint
        run: ruff check app tests

      - name: Secret guard
        working-directory: .
        run: |
          # Fail if anything that looks like a live credential is committed.
          if git grep -nIE '(sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC )?PRIVATE KEY-----)' \
              -- . ':!*.lock' ':!docs/**' ':!backend/tests/**'; then
            echo "::error::Possible credential committed"
            exit 1
          fi
          if git ls-files | grep -E '(^|/)\.env$'; then
            echo "::error::A .env file is tracked"
            exit 1
          fi

      - name: Migrations apply cleanly
        run: |
          alembic upgrade head
          alembic downgrade base
          alembic upgrade head

      - name: Tests
        run: pytest -q

  frontend:
    name: Frontend — typecheck, tests, build
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install
        run: npm ci

      - name: Typecheck
        run: npx tsc --noEmit

      - name: Tests
        run: npm run test

      - name: Build
        run: npm run build
```

## What it checks

| Job | Steps |
| --- | --- |
| Backend | `ruff check app tests` · secret guard · `alembic upgrade head` → `downgrade base` → `upgrade head` · `pytest -q` |
| Frontend | `tsc --noEmit` · `npm run test` · `npm run build` |

The secret guard fails the build if anything shaped like a live credential (PAT,
API key, private key) is committed, or if a `.env` file is tracked.
