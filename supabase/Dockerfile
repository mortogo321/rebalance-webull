# =============================================================================
# Backend API as a standalone Deno container.
#
# Why this exists alongside `supabase functions deploy`:
# UAT runs on a single EC2 box with a hosted Supabase project behind it. Hosted
# edge functions deploy on Supabase's schedule, not ours, which breaks the one
# property the UAT pipeline is built around — an atomic blue/green swap that can
# be rolled back in seconds. Running the identical Hono app as a container puts
# the API under the same nginx switch as the web and engine.
#
# The source is byte-identical to what `supabase functions deploy` ships, so the
# two runtimes cannot drift.
#
# This file lives at supabase/docker/ and must stay out of supabase/functions/:
# the CLI treats every entry under functions/ as a function and tries to load an
# index.ts from it, so a Dockerfile there breaks `supabase start`.
#
# Stages: base -> cache (dependency warm-up) -> development (watch) / runtime
# =============================================================================
ARG DENO_VERSION=2.9.6

# -----------------------------------------------------------------------------
FROM denoland/deno:${DENO_VERSION} AS base
WORKDIR /app
ENV DENO_DIR=/deno-dir

# -----------------------------------------------------------------------------
FROM base AS cache

# Resolve and compile dependencies against the import map alone, so editing a
# route does not re-download hono and supabase-js on every build.
COPY functions/deno.json ./functions/deno.json
COPY functions/ ./functions/
RUN deno cache --config functions/deno.json functions/api/index.ts

# -----------------------------------------------------------------------------
FROM cache AS development

ARG APP_ENV=development
COPY functions/.env.${APP_ENV} .env

EXPOSE 8000
# --watch restarts on change; the source is bind-mounted by compose, so an edit
# on the host is live without a rebuild.
CMD ["deno", "run", "--watch", "--allow-net", "--allow-env", "--allow-read", \
     "--config", "functions/deno.json", "functions/api/index.ts"]

# -----------------------------------------------------------------------------
FROM base AS runtime

COPY --from=cache /deno-dir /deno-dir
COPY functions/ ./functions/

ARG APP_ENV=uat
COPY functions/.env.${APP_ENV} .env

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} APP_ENV=${APP_ENV}
LABEL org.opencontainers.image.revision=${GIT_SHA} \
      org.opencontainers.image.created=${BUILD_TIME} \
      org.opencontainers.image.title="rebalance-api"

# The base image ships a non-root `deno` user; use it.
USER deno

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=3 \
  CMD ["deno", "eval", "--quiet", \
       "const r = await fetch('http://127.0.0.1:8000/api/health'); Deno.exit(r.ok ? 0 : 1)"]

# Permissions are enumerated, not blanket: this process handles decrypted
# credentials in transit, so it gets network and env and nothing else it does
# not need.
CMD ["deno", "run", "--allow-net", "--allow-env", "--allow-read=/app", \
     "--config", "functions/deno.json", "functions/api/index.ts"]
