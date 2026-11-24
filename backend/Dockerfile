# ---- AUREX AI backend image ----
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Backup directory must exist and be writable before the non-root user starts, or the
# recovery drill in /operations/backups fails on a permissions error nobody reads.
RUN mkdir -p /app/backups \
    && useradd --create-home --uid 10001 aurex \
    && chown -R aurex:aurex /app \
    && chmod +x /app/entrypoint.sh
USER aurex

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/operations/health || exit 1

# Migrate -> seed -> serve. See entrypoint.sh for why the order matters.
ENTRYPOINT ["/app/entrypoint.sh"]
