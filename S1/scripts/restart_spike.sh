#!/usr/bin/env bash
# Restart the spike stack. Dev tooling for the real-provider work.
#
# SPIKE_KEEP_DB=1 restarts the API and keeps everything — that is the mode a half-finished
# Deployment Run needs, because re-running the seed from scratch would spend the design line
# again just to reach the stage that failed.
set -euo pipefail
cd "$(dirname "$0")/.."
DB="${1:-damf_spike}"
PORT="${SPIKE_PORT:-8201}"

LISTENER=$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2 || true)
if [ -n "$LISTENER" ]; then
  kill "$LISTENER"
  sleep 3
fi

if [ "${SPIKE_KEEP_DB:-0}" != "1" ]; then
  psql -U ubuntu -h /var/run/postgresql -d postgres -q -c "DROP DATABASE IF EXISTS $DB" 2>/dev/null || true
  psql -U ubuntu -h /var/run/postgresql -d postgres -q -c "CREATE DATABASE $DB"
  rm -rf "/tmp/damf-spike-objects" && mkdir -p "/tmp/damf-spike-objects"
fi

DATABASE_URL="postgresql://ubuntu@/$DB?host=/var/run/postgresql" \
OBJECT_STORE_URL="file:///tmp/damf-spike-objects" \
SECRET_BACKEND=env \
FACTORY_API_HOST=127.0.0.1 \
FACTORY_API_PORT="$PORT" \
FACTORY_MIGRATIONS_MODE=apply \
FACTORY_WORKFLOW_ENGINE=inprocess \
FACTORY_PROVIDER_MODE=auto \
FACTORY_AUTH_MODE=off \
FACTORY_HTTPS_ONLY=false \
FACTORY_CREDENTIAL_MASTER_KEY=62e33604a87fac244356437674d5ea6ccd4d8ecac2c0ff6a9b67cf92938a8ad4 \
  nohup .venv/bin/factory-api > "/tmp/spike-api-$PORT.log" 2>&1 &

for _ in $(seq 1 90); do
  if curl -s -m 3 "http://127.0.0.1:$PORT/healthz" | grep -q '"S2":"real"'; then
    echo "real-mode api up on :$PORT (db $DB)"
    exit 0
  fi
  sleep 2
done
echo "api did not come up real; see /tmp/spike-api-$PORT.log" >&2
tail -30 "/tmp/spike-api-$PORT.log" >&2
exit 1
