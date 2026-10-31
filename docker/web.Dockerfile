# syntax=docker/dockerfile:1
FROM node:22-slim AS deps
WORKDIR /app
COPY web/package.json web/pnpm-lock.yaml ./
# Optional corporate/proxy CA, never stored in the image:
#   docker build --secret id=ca,src=/path/ca-bundle.crt ...
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/ca; fi; \
    corepack enable && corepack prepare pnpm@10.28.0 --activate && pnpm install --frozen-lockfile

FROM deps AS build
COPY web/ ./
# bundle the migration/seed script so the runtime image needs no dev dependencies
RUN pnpm exec esbuild scripts/migrate.ts --bundle --platform=node --format=cjs --outfile=migrate.cjs \
 && pnpm build

FROM node:22-slim AS run
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 MIGRATIONS_DIR=/app/migrations
WORKDIR /app
RUN useradd -r -u 1001 eyeai
COPY --from=build --chown=1001 /app/.next/standalone ./
COPY --from=build --chown=1001 /app/.next/static ./.next/static
COPY --from=build --chown=1001 /app/migrate.cjs ./migrate.cjs
COPY db/migrations /app/migrations
USER eyeai
EXPOSE 3000
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD node -e "fetch('http://127.0.0.1:3000/api/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
CMD ["sh", "-c", "node migrate.cjs && node server.js"]
