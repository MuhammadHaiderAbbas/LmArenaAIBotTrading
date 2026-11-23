# Enabling the live demo + CI (two options)

The code is pushed. Two things still need repository-level permissions that a
Contents-only token cannot grant: **GitHub Pages** and the **`workflow` scope**
(for files under `.github/workflows/`).

Pick either option — both end with the same result.

---

## Option A — I hand you a wider token (fastest, ~1 minute)

Create a **fine-grained** token on `LmArenaAIBotTrading` with:

| Permission | Setting |
| --- | --- |
| Contents | Read and write |
| Pages | Read and write |
| Actions | Read and write |
| Workflows | Read and write |

Or a **classic** token with the `repo` + `workflow` scopes (covers everything,
including Pages).

Paste it and I will: enable Pages on `gh-pages`, verify the live URL returns 200,
restore `.github/workflows/{ci,deploy-pages}.yml`, and push.

---

## Option B — three clicks in the browser (no new token)

### 1. Turn on Pages

<https://github.com/MuhammadHaiderAbbas/LmArenaAIBotTrading/settings/pages>

- **Source:** *Deploy from a branch*
- **Branch:** `gh-pages`
- **Folder:** `/ (root)`
- **Save**

The `gh-pages` branch already holds the compiled demo bundle (with `.nojekyll`
and a `404.html` SPA fallback), so the site goes live in about a minute:

**https://muhammadhaiderabbas.github.io/LmArenaAIBotTrading/**

Sign in with `demo@aurex.ai` / `Demo!Aurex2026`, or register to get your own
funded paper account.

### 2. Restore CI (optional)

The two workflow files were held back because GitHub rejects workflow changes
from tokens without the `workflow` scope. They are intact on `main` history in
your local clone and in `CI_WORKFLOWS.md` below.

To restore them, either:

- **Add via the web UI:** <https://github.com/MuhammadHaiderAbbas/LmArenaAIBotTrading/new/main/.github/workflows>
  and paste the contents from `CI_WORKFLOWS.md`, or
- **Push from your machine** (your own credentials have the scope):

  ```bash
  cd aurex-ai
  git checkout main
  # copy the two files back from CI_WORKFLOWS.md, then:
  git add .github/workflows
  git commit -m "build(ci): add CI and Pages deployment workflows"
  git push origin main
  ```

Once the `deploy-pages` workflow exists and Pages source is set to
**GitHub Actions**, you can switch to workflow-based deployment and every push
to `main` will rebuild the demo automatically.

---

## Why the demo is not the real backend

GitHub Pages serves static files only — it cannot run Python, PostgreSQL or
Redis. The demo therefore ships an in-browser implementation of the same API
contracts (registration, login, paper accounts, double-entry ledger, audit
trail, admin views) persisted to `localStorage`.

The real FastAPI service runs with:

```bash
cp .env.example .env && docker compose up --build
# web  → http://localhost:5173
# API  → http://localhost:8000/docs
```

Or deploy the API to Render using `infrastructure/deployment/render.yaml`
(API + managed Postgres, guardrails pinned to `false`).
