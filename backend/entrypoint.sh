#!/bin/sh
# AUREX AI backend entrypoint.
#
# Order matters, and it used to be wrong: the image ran `python -m app.seed` directly,
# which reaches for `Base.metadata.create_all`. That works on a blank database and
# silently diverges on an existing one — the app boots, the tables exist, and the schema
# is whatever the models happened to say rather than what the migration history says.
#
# So: migrate, then seed, then serve. Each step is switchable for environments that run
# migrations out of band (a deploy pipeline, a Kubernetes init job):
#
#   AUREX_RUN_MIGRATIONS=0   skip alembic
#   AUREX_RUN_SEED=0         skip seeding
set -eu

echo "[entrypoint] environment=${ENVIRONMENT:-development}"

if [ "${AUREX_RUN_MIGRATIONS:-1}" = "1" ]; then
    echo "[entrypoint] applying migrations"
    # Retry: a container can start before PostgreSQL finishes accepting connections even
    # with a healthcheck, and a crash-loop on first boot is a bad first impression.
    attempt=1
    while [ "$attempt" -le 10 ]; do
        if alembic upgrade head; then
            echo "[entrypoint] migrations at head"
            break
        fi
        if [ "$attempt" -eq 10 ]; then
            echo "[entrypoint] migrations failed after $attempt attempts" >&2
            exit 1
        fi
        echo "[entrypoint] database not ready (attempt $attempt/10), retrying in 3s"
        attempt=$((attempt + 1))
        sleep 3
    done
else
    echo "[entrypoint] migrations skipped (AUREX_RUN_MIGRATIONS=0)"
fi

if [ "${AUREX_RUN_SEED:-1}" = "1" ]; then
    echo "[entrypoint] seeding"
    # Seeding no longer needs create_all now that migrations have run.
    AUREX_AUTO_CREATE_TABLES="${AUREX_AUTO_CREATE_TABLES:-0}" python -m app.seed
else
    echo "[entrypoint] seed skipped (AUREX_RUN_SEED=0)"
fi

echo "[entrypoint] starting API"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
