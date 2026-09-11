# =============================================================================
# Rebalance engine — Python 3.12
#
# Stages:
#   base         runtime OS + interpreter, shared by everything
#   deps         dependency install, cached on pyproject.toml alone
#   development  hot reload; source arrives from a bind mount, not a COPY
#   test         dev deps + source, used by CI
#   runtime      slim, non-root, no build tools, no dev dependencies
#
# Build the one you want:  docker build --target runtime --build-arg APP_ENV=uat .
# =============================================================================
ARG PYTHON_VERSION=3.12

# -----------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src

WORKDIR /app

# curl is here for the container HEALTHCHECK; the blue/green deploy gate reads
# its status to decide whether a new colour is safe to route traffic to.
# postgresql-client is for start.sh, which applies the schema before uvicorn
# binds a port -- see that file for why the engine owns migrations.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl postgresql-client \
 && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
FROM base AS deps

# Dependencies resolve from pyproject.toml alone. The stub package satisfies the
# build backend without pulling in real source, so editing engine code does not
# invalidate this layer and a rebuild stays in the seconds.
COPY pyproject.toml ./
RUN mkdir -p src/engine && touch src/engine/__init__.py \
 && pip install --no-cache-dir -e .

# -----------------------------------------------------------------------------
FROM deps AS development

RUN pip install --no-cache-dir -e ".[dev]"

# APP_ENV selects which committed defaults file becomes the container's .env.
# Real environment variables still win over it at runtime, which is how UAT
# secrets override these baked-in defaults.
ARG APP_ENV=development
COPY .env.${APP_ENV} .env

COPY . .

EXPOSE 8000

# Migrates, then execs the CMD below. Not set on the `test` stage: pytest builds
# its own database and must not wait on GoTrue.
ENTRYPOINT ["/app/start.sh"]

# --reload watches the bind-mounted source; editing a file on the host restarts
# the worker in place, with no rebuild.
CMD ["uvicorn", "engine.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/src", \
     "--log-level", "debug"]

# -----------------------------------------------------------------------------
FROM deps AS test

RUN pip install --no-cache-dir -e ".[dev]"
ARG APP_ENV=development
COPY .env.${APP_ENV} .env
COPY . .
CMD ["pytest", "-q"]

# -----------------------------------------------------------------------------
FROM base AS runtime

# Least privilege: the engine holds decrypted broker credentials in memory, so
# a process escape should not also be root inside the container.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin engine

COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin /usr/local/bin

ARG APP_ENV=uat
COPY .env.${APP_ENV} .env
COPY src/ ./src/
COPY pyproject.toml ./
COPY start.sh ./
# Travels with the image; the migrations and seed it complements are mounted,
# because they live in supabase/ and are outside this build context.
COPY db/ ./db/

# Recorded in the image so /ready can report exactly what is deployed, and so
# a rollback can be confirmed by inspecting the running container.
ARG GIT_SHA=unknown
ARG BUILD_TIME=unknown
ENV GIT_SHA=${GIT_SHA} BUILD_TIME=${BUILD_TIME} APP_ENV=${APP_ENV}
LABEL org.opencontainers.image.revision=${GIT_SHA} \
      org.opencontainers.image.created=${BUILD_TIME} \
      org.opencontainers.image.title="rebalance-engine"

RUN chown -R engine:engine /app
USER engine

EXPOSE 8000

ENTRYPOINT ["/app/start.sh"]

# start-period covers pool warm-up so a slow first connection is not counted as
# a failure; after that three strikes marks the container unhealthy and the
# deploy gate refuses to promote it.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# One worker: the scheduler loop must not run in N processes at once, or N
# containers would each place the same rebalance. Horizontal scale would need a
# leader lock; scaling web separately from the engine is the reason they are
# separate services.
CMD ["uvicorn", "engine.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
