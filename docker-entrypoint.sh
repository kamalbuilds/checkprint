#!/usr/bin/env bash
# Start ClickHouse locally unless an external host (e.g. ClickHouse Cloud) is configured,
# then apply the schema and start the API.
set -euo pipefail

if [ "${CLICKHOUSE_HOST:-localhost}" = "localhost" ]; then
  echo "[entrypoint] starting local ClickHouse"
  mkdir -p /var/lib/clickhouse-data
  clickhouse server > /var/log/clickhouse.log 2>&1 &

  for i in $(seq 1 60); do
    if clickhouse client -q "SELECT 1" >/dev/null 2>&1; then
      echo "[entrypoint] ClickHouse ready after ${i}s"
      break
    fi
    sleep 1
  done

  if ! clickhouse client -q "SELECT 1" >/dev/null 2>&1; then
    echo "[entrypoint] FATAL: ClickHouse did not become ready" >&2
    tail -40 /var/log/clickhouse.log >&2
    exit 1
  fi

  clickhouse client --multiquery < qc/schema.sql
  echo "[entrypoint] schema applied"
else
  echo "[entrypoint] using external ClickHouse at ${CLICKHOUSE_HOST}"
  python - <<'PY'
from qc import store
store.apply_schema()
print("[entrypoint] schema applied to external ClickHouse")
PY
fi

exec python web/server.py
