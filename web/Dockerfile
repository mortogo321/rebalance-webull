# =============================================================================
# Web — Next.js 16 / React 19, built with Bun
#
# Stages:
#   base         bun runtime, shared
#   deps         node_modules, cached on the lockfile alone
#   development  next dev --turbopack, source bind-mounted for hot reload
#   builder      production build (standalone output)
#   runtime      node + the standalone bundle only, non-root
#
# Bun installs and builds; Node runs the server. Bun's installer is the fast
# part and Next.js `output: "standalone"` emits a Node server, so running it on
# Node is the supported path rather than a second runtime to debug in production.
#
#   docker build --target runtime --build-arg APP_ENV=uat .
# =============================================================================
ARG BUN_VERSION=1.4.2
ARG NODE_VERSION=24-alpine

# -----------------------------------------------------------------------------
FROM oven/bun:${BUN_VERSION}-alpine AS base
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1

# -----------------------------------------------------------------------------
FROM base AS deps

COPY package.json bun.lock* ./
# --frozen-lockfile when one is committed, so CI installs are reproducible.
RUN if [ -f bun.lock ]; then bun install --frozen-lockfile; else bun install; fi

# -----------------------------------------------------------------------------
FROM base AS development

COPY --from=deps /app/node_modules ./node_modules

# APP_ENV selects which committed defaults file becomes the container's .env.
# Real environment variables still win over it at runtime, which is how UAT
# secrets override these baked-in defaults.
ARG APP_ENV=development
COPY .env.${APP_ENV} .env

COPY . .

EXPOSE 3000
# Polling: file-change events do not cross the Docker Desktop VM boundary
# reliably on macOS, and a dev server that silently stops reloading is worse
# than one that costs a little CPU.
ENV WATCHPACK_POLLING=true CHOKIDAR_USEPOLLING=true
CMD ["bun", "run", "dev"]

# -----------------------------------------------------------------------------
FROM base AS builder

COPY --from=deps /app/node_modules ./node_modules
COPY . .

# NEXT_PUBLIC_* must exist HERE, not at `docker run`: Next.js inlines them into
# the client bundle at build time, so a value supplied only at runtime arrives
# too late and the browser sees undefined.
ARG APP_ENV=uat
COPY .env.${APP_ENV} .env

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} APP_ENV=${APP_ENV}

RUN bun run build

# -----------------------------------------------------------------------------
FROM node:${NODE_VERSION} AS runtime

WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0 NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 10001 -S nodejs && adduser -u 10001 -S nextjs -G nodejs

# `output: "standalone"` emits a server plus only the modules it actually
# imports, so the runtime image carries no build toolchain and no dev deps.
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME}
LABEL org.opencontainers.image.revision=${GIT_SHA} \
      org.opencontainers.image.created=${BUILD_TIME} \
      org.opencontainers.image.title="rebalance-web"

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]
