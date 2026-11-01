#!/usr/bin/env bash
# Apply database migrations (db/migrations/*.sql) without starting the app.
#   scripts/migrate.sh            via docker compose (uses the `migrate` service and the compose database)
#   LOCAL=1 DATABASE_URL=postgres://... scripts/migrate.sh   directly with Node (needs `pnpm install` in web/)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -n "${LOCAL:-}" ]; then cd web && exec pnpm migrate; fi
exec docker compose run --rm migrate
