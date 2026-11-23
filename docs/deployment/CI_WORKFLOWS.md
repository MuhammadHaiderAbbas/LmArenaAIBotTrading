# CI workflow files (held back from the Contents-only push)

GitHub rejects any change to `.github/workflows/` from a token without the
`workflow` scope. These files are the current intent; add them via the web UI
or push from a machine with the scope. See HOSTING.md.

## `.github/workflows/ci.yml`

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  backend:
    name: Backend tests & lint
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: backend/requirements.txt
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Lint
        run: ruff check app tests
      - name: Tests
        run: pytest -q

  frontend:
    name: Frontend typecheck, tests & build
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
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
      - run: npm run build

  secret-guard:
    name: Secret guard
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 2
      - name: Block committed secrets
        run: |
          python3 scripts/check_secrets.py $(git diff --name-only HEAD~1 HEAD 2>/dev/null || echo "")
```

## `.github/workflows/deploy-pages.yml`

```yaml
name: Deploy demo to GitHub Pages

on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: pages
  cancel-in-progress: true

jobs:
  build:
    name: Build static demo
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: frontend/package-lock.json

      - name: Install dependencies
        working-directory: frontend
        run: npm ci

      - name: Type check
        working-directory: frontend
        run: npm run typecheck

      - name: Build demo bundle
        working-directory: frontend
        env:
          GITHUB_REPOSITORY: ${{ github.repository }}
        run: npm run build:demo

      - name: Add SPA fallback and nojekyll
        working-directory: frontend
        run: |
          cp dist/index.html dist/404.html
          touch dist/.nojekyll

      - uses: actions/configure-pages@v5

      - uses: actions/upload-pages-artifact@v3
        with:
          path: frontend/dist

  deploy:
    name: Deploy
    needs: build
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```
