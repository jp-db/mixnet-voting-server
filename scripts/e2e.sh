#!/usr/bin/env bash
# End-to-end test against a throwaway stack: three mixnet nodes, the ballot
# box, a Celery worker, Redis and the Vite dev server, all on this machine.
# Runs scripts/smoke_test.py (HTTP only), then scripts/browser_test.mjs
# (headless Chromium through the real pages) when the WebAssembly module is
# built and Chromium is found. Stops only what it started.
#
#   scripts/e2e.sh                 # Redis in a Docker container
#   E2E_REDIS_URL=redis://... scripts/e2e.sh
#
# Other knobs: E2E_DATABASE_URL (default: SQLite in the work dir),
# CHROMIUM (browser binary), E2E_BROWSER=0 to skip the browser test,
# E2E_KEEP=1 to keep the work dir (logs, screenshots).
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d "${TMPDIR:-/tmp}/mixnet-e2e.XXXXXX")
PIDS=()
REDIS_CONTAINER=""

cleanup() {
  status=$?
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  [ -n "$REDIS_CONTAINER" ] && docker stop "$REDIS_CONTAINER" >/dev/null 2>&1 || true
  if [ "$status" -ne 0 ] || [ "${E2E_KEEP:-0}" = 1 ]; then
    echo "logs and screenshots: $WORK"
  else
    rm -rf "$WORK"
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

secret() { python3 -c "import secrets; print(secrets.token_urlsafe(48))"; }
kek() { python3 -c "import base64, os; print(base64.b64encode(os.urandom(32)).decode())"; }
wait_http() {  # url, what
  for _ in $(seq 120); do curl -fs -o /dev/null "$1" && return 0; sleep 0.5; done
  echo "$2 did not start; see $WORK" >&2
  return 1
}
start() {  # log name, command...
  local log=$1; shift
  "$@" >"$WORK/$log.log" 2>&1 &
  PIDS+=($!)
}

cd "$ROOT"

if [ -z "${E2E_REDIS_URL:-}" ]; then
  REDIS_CONTAINER=mixnet-e2e-redis-$$
  docker run -d --rm --name "$REDIS_CONTAINER" -p 127.0.0.1:56379:6379 redis:7 >/dev/null
  E2E_REDIS_URL=redis://127.0.0.1:56379/0
fi

nodes="["
for j in 0 1 2; do
  token=$(secret)
  start "node$j" env MIXNET_NODE_NODE_NAME="mixnet-node-$j" MIXNET_NODE_API_TOKEN="$token" \
    MIXNET_NODE_KEY_ENCRYPTION_KEY="$(kek)" MIXNET_NODE_DATA_DIR="$WORK/node$j" \
    uv run --no-sync uvicorn mixnet_node.app:create_app --factory --port "811$j"
  nodes="$nodes{\"name\": \"mixnet-node-$j\", \"url\": \"http://127.0.0.1:811$j\", \"token\": \"$token\"},"
done

export BALLOT_BOX_SECRET_KEY=$(secret)
export BALLOT_BOX_ADMIN_TOKEN=$(secret)
export BALLOT_BOX_DATABASE_URL=${E2E_DATABASE_URL:-sqlite:///$WORK/ballot-box.db}
export BALLOT_BOX_REDIS_URL=$E2E_REDIS_URL
export BALLOT_BOX_BLOB_DIR=$WORK/blobs
export BALLOT_BOX_PUBLIC_URL=http://localhost:5173
export BALLOT_BOX_MIX_POLL_SECONDS=0.5
export BALLOT_BOX_MIXNET_NODES="${nodes%,}]"

uv run --no-sync python -m ballot_box.db
start ballot-box uv run --no-sync uvicorn ballot_box.app:create_app --factory --port 8000
start worker uv run --no-sync celery -A ballot_box.worker worker --loglevel=info --concurrency=2
start vite bash -c "cd frontend && exec npx vite --port 5173 --strictPort"

for j in 0 1 2; do wait_http "http://127.0.0.1:811$j/health" "mixnet node $j"; done
wait_http http://127.0.0.1:8000/health "the ballot box"
wait_http http://localhost:5173/ "vite"
for _ in $(seq 60); do grep -q "ready" "$WORK/worker.log" && break; sleep 0.5; done

echo "== smoke test (HTTP)"
uv run --no-sync python scripts/smoke_test.py http://localhost:5173

browser=${E2E_BROWSER:-auto}
if [ "$browser" = auto ]; then
  if [ -f evmlr-shuffle/wasm/build/evmlr.wasm ] && command -v "${CHROMIUM:-chromium}" >/dev/null; then
    browser=1
  else
    browser=0
    echo "== browser test skipped (needs the WASM build and ${CHROMIUM:-chromium})"
  fi
fi
if [ "$browser" = 1 ]; then
  echo "== browser test (headless Chromium)"
  election=$(uv run --no-sync python scripts/browser_setup.py)
  node scripts/browser_test.mjs "$election" "$WORK/browser"
fi
echo "== e2e OK"
