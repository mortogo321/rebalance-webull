-- =============================================================================
-- 0000 — core schema
--
-- Design notes (the "why", expanded in the README under "Database design"):
--   * Two schemas. `public` holds everything a signed-in user may read and is
--     exposed through PostgREST. `private` holds encrypted broker secrets and
--     paper-broker state; it is NOT listed in config.toml [api].schemas, so it
--     has no HTTP surface at all. That is the primary control keeping API
--     keys away from the browser -- RLS is the second, not the first, line.
--   * Weights are stored as integer basis points, never floats. 33.33% is
--     3333 bps. Summing money weights in binary floating point is how
--     rebalancers develop a slow leak; integers make "must total 100%" exact.
--   * Money is numeric(20,4) and quantities numeric(20,8). No float anywhere.
-- =============================================================================

create schema if not exists private;

-- `private` is defence in depth behind the PostgREST schema allowlist: even if
-- the allowlist were misconfigured, the API roles hold no rights here.
revoke all on schema private from public;
grant usage on schema private to postgres, service_role;

-- -----------------------------------------------------------------------------
-- enums
-- -----------------------------------------------------------------------------
create type public.bot_status          as enum ('draft', 'running', 'paused', 'stopped');
create type public.trigger_type        as enum ('schedule', 'drift', 'schedule_or_drift');
create type public.run_status          as enum ('pending', 'running', 'succeeded', 'failed', 'skipped');
create type public.order_side          as enum ('buy', 'sell');
create type public.order_status        as enum ('pending', 'submitted', 'filled', 'partially_filled', 'rejected', 'cancelled');
create type public.broker_environment  as enum ('mock', 'webull_uat');
create type public.connection_status   as enum ('disconnected', 'connected', 'error');

-- -----------------------------------------------------------------------------
-- shared trigger: keep updated_at honest
-- -----------------------------------------------------------------------------
create or replace function public.set_updated_at()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- -----------------------------------------------------------------------------
-- broker_connections — non-secret connection state, safe for the browser
-- -----------------------------------------------------------------------------
create table public.broker_connections (
  user_id           uuid primary key references auth.users (id) on delete cascade,
  status            public.connection_status     not null default 'disconnected',
  environment       public.broker_environment    not null default 'mock',
  account_id        text,
  account_currency  text,
  -- Last 4 characters of the API key. Enough for a human to confirm *which*
  -- key is attached; useless to an attacker. The key itself never lives here.
  api_key_hint      text,
  last_verified_at  timestamptz,
  last_error        text,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create trigger broker_connections_set_updated_at
  before update on public.broker_connections
  for each row execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- private.broker_credentials — AES-256-GCM ciphertext, service_role only
-- -----------------------------------------------------------------------------
create table private.broker_credentials (
  user_id              uuid primary key references auth.users (id) on delete cascade,
  -- Envelope format: v<n>.<base64 iv>.<base64 ciphertext||tag>
  -- The user_id is bound in as AES-GCM additional authenticated data, so a row
  -- copied onto another user's id fails to decrypt instead of leaking.
  api_key_encrypted    text not null,
  api_secret_encrypted text not null,
  key_version          int  not null default 1,
  created_at           timestamptz not null default now(),
  updated_at           timestamptz not null default now()
);

create trigger broker_credentials_set_updated_at
  before update on private.broker_credentials
  for each row execute function public.set_updated_at();

-- -----------------------------------------------------------------------------
-- bots
-- -----------------------------------------------------------------------------
create table public.bots (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users (id) on delete cascade,
  name                text not null check (length(btrim(name)) between 1 and 60),
  status              public.bot_status not null default 'draft',
  base_currency       text not null default 'USD' check (base_currency ~ '^[A-Z]{3}$'),

  -- Size of the sleeve this bot manages. The bot never deploys more than this,
  -- even if the brokerage account holds more.
  investment_amount   numeric(20,4) not null check (investment_amount > 0),

  trigger_type        public.trigger_type not null default 'schedule_or_drift',
  interval_minutes    int check (interval_minutes between 1 and 525600),
  drift_threshold_bps int check (drift_threshold_bps between 1 and 10000),

  -- Orders smaller than this are dropped: they burn commission to move nothing.
  min_order_value     numeric(20,4) not null default 10 check (min_order_value >= 0),
  -- Share of the sleeve deliberately left in cash, in bps.
  cash_buffer_bps     int not null default 0 check (cash_buffer_bps between 0 and 10000),
  allow_fractional    boolean not null default false,

  last_evaluated_at   timestamptz,
  last_rebalanced_at  timestamptz,
  next_run_at         timestamptz,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  constraint bots_name_unique_per_user unique (user_id, name),

  -- A schedule-driven bot without an interval, or a drift-driven bot without a
  -- threshold, can never fire. Reject at write time rather than silently idling.
  constraint bots_schedule_needs_interval check (
    trigger_type = 'drift' or interval_minutes is not null
  ),
  constraint bots_drift_needs_threshold check (
    trigger_type = 'schedule' or drift_threshold_bps is not null
  )
);

create trigger bots_set_updated_at
  before update on public.bots
  for each row execute function public.set_updated_at();

create index bots_user_status_idx on public.bots (user_id, status);
-- The scheduler's hot path: "which running bots are due?". Partial index keeps
-- it proportional to active bots, not to all bots ever created.
create index bots_due_idx on public.bots (next_run_at)
  where status = 'running';

-- -----------------------------------------------------------------------------
-- bot_targets — the desired portfolio, in basis points
-- -----------------------------------------------------------------------------
create table public.bot_targets (
  id                uuid primary key default gen_random_uuid(),
  bot_id            uuid not null references public.bots (id) on delete cascade,
  -- Denormalised from bots so RLS is a single-column comparison with no
  -- subquery, and so a stray row can never be orphaned onto another user.
  user_id           uuid not null references auth.users (id) on delete cascade,
  symbol            text not null check (symbol = upper(symbol) and symbol ~ '^[A-Z0-9.\-]{1,12}$'),
  target_weight_bps int  not null check (target_weight_bps between 0 and 10000),
  created_at        timestamptz not null default now(),
  unique (bot_id, symbol)
);

create index bot_targets_bot_idx on public.bot_targets (bot_id);

-- Weights must total exactly 100%. This cannot be a CHECK (it spans rows), so
-- it is a DEFERRABLE constraint trigger: a transaction may pass through
-- intermediate states while rewriting a basket, but cannot commit unbalanced.
create or replace function public.assert_bot_targets_sum_to_100()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_bot_id uuid := coalesce(new.bot_id, old.bot_id);
  v_total  int;
  v_count  int;
begin
  -- Bot already gone (cascade delete): nothing left to balance.
  if not exists (select 1 from public.bots where id = v_bot_id) then
    return null;
  end if;

  select coalesce(sum(target_weight_bps), 0), count(*)
    into v_total, v_count
    from public.bot_targets
   where bot_id = v_bot_id;

  -- Zero targets is a legal resting state (a draft bot being assembled).
  if v_count = 0 then
    return null;
  end if;

  if v_total <> 10000 then
    raise exception
      'bot % target weights must total 10000 bps (100%%), got % bps across % holdings',
      v_bot_id, v_total, v_count
      using errcode = 'check_violation';
  end if;

  return null;
end;
$$;

create constraint trigger bot_targets_sum_check
  after insert or update or delete on public.bot_targets
  deferrable initially deferred
  for each row execute function public.assert_bot_targets_sum_to_100();

-- -----------------------------------------------------------------------------
-- rebalance_runs — one row per evaluation, including the ones that did nothing
-- -----------------------------------------------------------------------------
create table public.rebalance_runs (
  id                    uuid primary key default gen_random_uuid(),
  bot_id                uuid not null references public.bots (id) on delete cascade,
  user_id               uuid not null references auth.users (id) on delete cascade,
  status                public.run_status not null default 'pending',
  -- Human-readable: 'schedule due', 'drift 742bps >= 500bps', 'manual'...
  trigger_reason        text not null,
  portfolio_value       numeric(20,4),
  cash_before           numeric(20,4),
  max_drift_bps         int,
  planned_order_count   int not null default 0,
  submitted_order_count int not null default 0,
  -- Full audit snapshot: prices used, holdings seen, per-symbol target vs
  -- actual, and every order considered *including the ones filtered out*.
  -- Without this, "why did the bot do that?" is unanswerable after the fact.
  plan                  jsonb,
  error                 text,
  started_at            timestamptz not null default now(),
  finished_at           timestamptz
);

create index rebalance_runs_bot_idx  on public.rebalance_runs (bot_id, started_at desc);
create index rebalance_runs_user_idx on public.rebalance_runs (user_id, started_at desc);

-- -----------------------------------------------------------------------------
-- orders
-- -----------------------------------------------------------------------------
create table public.orders (
  id               uuid primary key default gen_random_uuid(),
  run_id           uuid not null references public.rebalance_runs (id) on delete cascade,
  bot_id           uuid not null references public.bots (id) on delete cascade,
  user_id          uuid not null references auth.users (id) on delete cascade,
  symbol           text not null,
  side             public.order_side not null,
  quantity         numeric(20,8) not null check (quantity > 0),
  limit_price      numeric(20,4),
  status           public.order_status not null default 'pending',
  filled_quantity  numeric(20,8) not null default 0 check (filled_quantity >= 0),
  avg_fill_price   numeric(20,4),
  requested_value  numeric(20,4),
  broker_order_id  text,
  -- Idempotency key, deterministic from (run, symbol, side). A retried submit
  -- collides here instead of double-trading.
  client_order_id  text not null unique,
  reject_reason    text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  constraint orders_fill_not_over_quantity check (filled_quantity <= quantity)
);

create trigger orders_set_updated_at
  before update on public.orders
  for each row execute function public.set_updated_at();

create index orders_bot_idx  on public.orders (bot_id, created_at desc);
create index orders_run_idx  on public.orders (run_id);
create index orders_user_idx on public.orders (user_id, created_at desc);

-- -----------------------------------------------------------------------------
-- private paper-broker state
--
-- The mock broker persists here rather than in engine memory, so a container
-- restart does not silently reset everyone's portfolio mid-demo.
-- -----------------------------------------------------------------------------
create table private.paper_accounts (
  user_id    uuid primary key references auth.users (id) on delete cascade,
  cash       numeric(20,4) not null check (cash >= 0),
  currency   text not null default 'USD',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table private.paper_positions (
  user_id    uuid not null references auth.users (id) on delete cascade,
  symbol     text not null,
  quantity   numeric(20,8) not null default 0 check (quantity >= 0),
  avg_cost   numeric(20,4) not null default 0 check (avg_cost >= 0),
  updated_at timestamptz not null default now(),
  primary key (user_id, symbol)
);
