#!/usr/bin/env bash
# Container entrypoint:
#   1. (optional) wait for Postgres if DATABASE_URL is set
#   2. apply alembic migrations (`upgrade head`) unless skipped
#   3. exec the CMD (uvicorn) under tini so signals propagate cleanly
set -euo pipefail

log() { printf '[entrypoint] %s\n' "$*" >&2; }

if [[ -n "${DATABASE_URL:-}" && "${AEGIS_WAIT_FOR_DB:-1}" == "1" ]]; then
  # Strip driver prefix (postgresql+asyncpg://) and any query string for psql-style host parse.
  host_port=$(python - <<'PY'
import os, re, urllib.parse
url = os.environ["DATABASE_URL"]
url = re.sub(r"^[^:]+\+[^:]+://", "postgresql://", url, count=1)
p = urllib.parse.urlparse(url)
print(f"{p.hostname or 'localhost'} {p.port or 5432}")
PY
)
  host=${host_port% *}
  port=${host_port##* }
  log "Waiting for Postgres at ${host}:${port} ..."
  for _ in {1..60}; do
    if (echo >"/dev/tcp/${host}/${port}") >/dev/null 2>&1; then
      log "Postgres reachable."
      break
    fi
    sleep 1
  done
fi

if [[ "${AEGIS_RUN_MIGRATIONS:-1}" == "1" ]]; then
  log "Running alembic upgrade head ..."
  alembic upgrade head
else
  log "Skipping migrations (AEGIS_RUN_MIGRATIONS=0)."
fi

log "Starting: $*"
exec "$@"
