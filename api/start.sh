#!/bin/sh
# =============================================================================
# Engine entrypoint: bring the schema up to date, then exec the real command.
#
# ENTRYPOINT rather than a replacement CMD, so `docker run ... pytest` and the
# compose CMD both still work -- this wraps whatever it is handed.
#
# Why the engine and not a one-shot `migrate` service: it was one, and the
# container count is the cost this removes. Why not the `api` service, which is
# the one that actually owns supabase/: compose rejects the dependency cycles
# that would create (postgrest -> api -> gateway -> postgrest, and
# engine -> api -> engine). The engine is the only service nothing else must
# start before.
#
# Ordering guarantee: this runs to completion BEFORE uvicorn binds a port, so
# the container's healthcheck cannot pass until the schema is ready. Services
# waiting on `engine: service_healthy` are therefore waiting on migrations too.
# =============================================================================
set -eu

MIGRATIONS_DIR="${MIGRATIONS_DIR:-/opt/bootstrap/migrations}"
SEED_FILE="${SEED_FILE:-/opt/bootstrap/seed.sql}"
NORMALIZE_FILE="${NORMALIZE_FILE:-/app/db/gotrue-normalize.sql}"

# uat/production apply schema as a deploy step against a database this container
# has no rights to migrate, so the directory is simply not mounted there.
if [ ! -d "$MIGRATIONS_DIR" ]; then
  echo "==> no migrations mounted at $MIGRATIONS_DIR; starting without migrating"
  exec "$@"
fi

PSQL="psql -v ON_ERROR_STOP=1 --no-psqlrc -d $DATABASE_URL"

# GoTrue owns auth.users and creates it with its own migrations at boot. Every
# table in 20260101000000_init.sql carries a foreign key to it, so there is
# nothing to do until it exists.
echo "==> waiting for GoTrue to finish creating auth.users"
i=0
until $PSQL -tAc "select to_regclass('auth.users') is not null" 2>/dev/null | grep -q '^t$'; do
  i=$((i + 1))
  [ "$i" -lt 60 ] || { echo "error: auth.users never appeared; is gotrue healthy?" >&2; exit 1; }
  sleep 2
done

# One migrator at a time. Session-scoped, so psql exiting -- badly included --
# releases it. Matters because the scheduler's leader lock means more than one
# engine replica is a supported configuration.
echo "==> acquiring migration lock"
$PSQL -tAc "select pg_advisory_lock(4172619)" >/dev/null

$PSQL <<'EOSQL'
create table if not exists public.applied_migrations (
  filename    text primary key,
  applied_at  timestamptz not null default now()
);
EOSQL

apply() {
  name=$(basename "$1")
  if [ "$($PSQL -tAc "select count(*) from public.applied_migrations where filename = '$name'")" = "1" ]; then
    echo "    $name (already applied)"
    return 0
  fi
  echo "    $name"
  # One transaction per file: a migration that fails halfway leaves nothing
  # behind and is not recorded, so the next start retries it from clean.
  $PSQL --single-transaction -f "$1" \
    -c "insert into public.applied_migrations (filename) values ('$name')"
}

echo "==> migrations"
for f in "$MIGRATIONS_DIR"/*.sql; do
  [ -e "$f" ] || { echo "error: no migrations found in $MIGRATIONS_DIR" >&2; exit 1; }
  apply "$f"
done

# Development only; a non-development overlay does not mount it.
if [ -f "$SEED_FILE" ]; then
  echo "==> seed"
  apply "$SEED_FILE"
fi

# Unconditional and idempotent, so it also repairs users added after the first
# run (Studio, a manual insert) rather than only the seeded pair.
if [ -f "$NORMALIZE_FILE" ]; then
  echo "==> normalising seeded users for GoTrue"
  $PSQL -f "$NORMALIZE_FILE"
fi

echo "==> schema ready; starting $1"
exec "$@"
