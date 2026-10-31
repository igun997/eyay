#!/usr/bin/env bash
# Local / CI end-to-end run with ONE stream.
#   e2e/run.sh            build images and run
#   NO_BUILD=1 e2e/run.sh reuse already built eyeai-app / eyeai-worker images
set -euo pipefail
cd "$(dirname "$0")/.."
export FEED_STREAMS=dock2 COMPOSE_PROJECT_NAME=eyeai-e2e
scripts/fetch_assets.sh e2e
cleanup() { docker compose --profile dev logs --no-color --tail=80 worker app > e2e/out/stack.log 2>&1 || true; docker compose --profile dev down -v >/dev/null 2>&1; }
mkdir -p e2e/out
trap cleanup EXIT
UP=(up -d --build); [ -z "${NO_BUILD:-}" ] || UP=(up -d --no-build)
docker compose --profile dev "${UP[@]}"
cd e2e
# SKIP_NPM=1 when playwright is already resolvable (e.g. NODE_PATH to a global install)
[ -d node_modules ] || [ -n "${SKIP_NPM:-}" ] || npm install --no-audit --no-fund
E2E_OUT=out node smoke.mjs
