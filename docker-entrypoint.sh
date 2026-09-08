#!/usr/bin/env bash
# Start uvicorn FIRST (binds PORT for Cloud Run health probe), then boot
# ClickHouse in the background.  /api/health reports "warming" until CH is up.
set -euo pipefail

if [ "${CLICKHOUSE_HOST:-localhost}" = "localhost" ]; then
  echo "[entrypoint] will start local ClickHouse in background"

  # Cloud Run filesystem is read-only except /tmp
  mkdir -p /tmp/clickhouse/{data,tmp,user_files,format_schemas,log}

  # Start ClickHouse with our /tmp-based config only (skip /etc/clickhouse-server/).
  # --daemon fails inside Cloud Run (no pidfile path), so background with &.
  clickhouse-server --config-file=/app/clickhouse-local.xml \
    -- --path /tmp/clickhouse/data/ 2>&1 &

  # Apply schema in a background subshell so uvicorn starts immediately
  (
    for i in $(seq 1 90); do
      if clickhouse-client --port 9000 -q "SELECT 1" >/dev/null 2>&1; then
        echo "[entrypoint] ClickHouse ready after ${i}s"
        clickhouse-client --port 9000 --multiquery < /app/qc/schema.sql
        echo "[entrypoint] schema applied"
        exit 0
      fi
      sleep 1
    done
    echo "[entrypoint] WARNING: ClickHouse did not become ready in 90s" >&2
    tail -40 /tmp/clickhouse/log/clickhouse-server.err.log >&2 || true
  ) &
else
  echo "[entrypoint] using external ClickHouse at ${CLICKHOUSE_HOST}"
  python - <<'PY'
from qc import store
store.apply_schema()
print("[entrypoint] schema applied to external ClickHouse")
PY
fi

echo "[entrypoint] starting uvicorn NOW (port ${PORT:-8080})"
exec python web/server.py
