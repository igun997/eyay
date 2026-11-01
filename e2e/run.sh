#!/usr/bin/env bash
# Local / CI end-to-end run with ONE stream.
#   e2e/run.sh            build images and run
#   NO_BUILD=1 e2e/run.sh reuse already built eyeai-app / eyeai-worker images
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
export FEED_STREAMS=dock2 COMPOSE_PROJECT_NAME=eyeai-e2e
# E2E_CLASSIFIER=1 (default) also starts Ollama + the d1 model and asserts server-side classification
E2E_CLASSIFIER=${E2E_CLASSIFIER:-1}
PROFILES=(--profile dev)
if [ "$E2E_CLASSIFIER" = 1 ]; then PROFILES+=(--profile classifier); export CLASSIFIER_URL=http://classifier:11434 E2E_CLASSIFIER; fi
scripts/fetch_assets.sh e2e
cleanup() { docker compose "${PROFILES[@]}" logs --no-color --tail=120 worker app classifier > "$ROOT/e2e/out/stack.log" 2>&1 || true; docker compose "${PROFILES[@]}" down -v >/dev/null 2>&1; }
mkdir -p "$ROOT/e2e/out"
trap cleanup EXIT
UP=(up -d --build); [ -z "${NO_BUILD:-}" ] || UP=(up -d --no-build)
docker compose "${PROFILES[@]}" "${UP[@]}"
cd e2e
# SKIP_NPM=1: use a playwright that is already installed globally (symlinked in)
if [ -n "${SKIP_NPM:-}" ] && [ ! -e node_modules/playwright ]; then mkdir -p node_modules; ln -s "$(npm root -g)/playwright" node_modules/playwright; fi
[ -d node_modules ] || [ -n "${SKIP_NPM:-}" ] || npm install --no-audit --no-fund
E2E_OUT=out node smoke.mjs
