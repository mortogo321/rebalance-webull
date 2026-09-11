-- =============================================================================
-- 0100 — row level security
--
-- Policy model, stated once so every table below is read the same way:
--
--   READS  a signed-in user may SELECT exactly the rows where user_id matches
--          their auth.uid(). Nothing else. No cross-user read path exists.
--
--   WRITES no INSERT / UPDATE / DELETE policy is granted to `authenticated`.
--          Every mutation goes through the Edge Function API (service_role),
--          which owns the invariants a row-level policy cannot express:
--            - target weights totalling exactly 100%
--            - "you may not edit a bot that is mid-run"
--            - encrypting credentials before they touch a disk
--            - order rows only ever written from a broker response
--          A client that could INSERT into public.orders directly could invent
--          a fill history. So it cannot.
--
-- `(select auth.uid())` rather than bare `auth.uid()` is deliberate: wrapping it
-- lets Postgres hoist the call into an InitPlan and evaluate it once per query
-- instead of once per row.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- public tables: owner-only reads
-- -----------------------------------------------------------------------------
alter table public.broker_connections enable row level security;
alter table public.bots               enable row level security;
alter table public.bot_targets        enable row level security;
alter table public.rebalance_runs     enable row level security;
alter table public.orders             enable row level security;

create policy "own connection is readable"
  on public.broker_connections for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "own bots are readable"
  on public.bots for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "own bot targets are readable"
  on public.bot_targets for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "own runs are readable"
  on public.rebalance_runs for select to authenticated
  using ((select auth.uid()) = user_id);

create policy "own orders are readable"
  on public.orders for select to authenticated
  using ((select auth.uid()) = user_id);

-- Make the read-only intent explicit in the grant table too, so `\dp` tells the
-- same story as the policies. RLS already denies these; this removes the
-- privilege outright, which is the thing a reviewer actually greps for.
revoke insert, update, delete on public.broker_connections from anon, authenticated;
revoke insert, update, delete on public.bots               from anon, authenticated;
revoke insert, update, delete on public.bot_targets        from anon, authenticated;
revoke insert, update, delete on public.rebalance_runs     from anon, authenticated;
revoke insert, update, delete on public.orders             from anon, authenticated;

-- Anonymous callers get nothing anywhere.
revoke all on public.broker_connections from anon;
revoke all on public.bots               from anon;
revoke all on public.bot_targets        from anon;
revoke all on public.rebalance_runs     from anon;
revoke all on public.orders             from anon;

-- -----------------------------------------------------------------------------
-- private tables: deny-all, by having zero policies
--
-- These are already unreachable (schema not exposed to PostgREST, no USAGE
-- granted to the API roles). RLS with no policies is the third lock: if a
-- future migration ever exposed the schema by mistake, the tables stay shut.
-- service_role holds BYPASSRLS and is unaffected.
-- -----------------------------------------------------------------------------
alter table private.broker_credentials enable row level security;
alter table private.paper_accounts     enable row level security;
alter table private.paper_positions    enable row level security;

alter table private.broker_credentials force row level security;
alter table private.paper_accounts     force row level security;
alter table private.paper_positions    force row level security;

grant select, insert, update, delete on all tables in schema private to service_role;
alter default privileges in schema private
  grant select, insert, update, delete on tables to service_role;

-- -----------------------------------------------------------------------------
-- bot_overview — one row per bot with its latest run, for the list screen
--
-- security_invoker = on makes the view run as the caller, so the underlying
-- policies apply. Without it a view is a hole straight through RLS.
-- -----------------------------------------------------------------------------
create view public.bot_overview
with (security_invoker = on) as
select
  b.id,
  b.user_id,
  b.name,
  b.status,
  b.base_currency,
  b.investment_amount,
  b.trigger_type,
  b.interval_minutes,
  b.drift_threshold_bps,
  b.next_run_at,
  b.last_rebalanced_at,
  (select count(*) from public.bot_targets t where t.bot_id = b.id)   as target_count,
  r.id            as latest_run_id,
  r.status        as latest_run_status,
  r.max_drift_bps as latest_max_drift_bps,
  r.portfolio_value as latest_portfolio_value,
  r.started_at    as latest_run_at
from public.bots b
left join lateral (
  select id, status, max_drift_bps, portfolio_value, started_at
    from public.rebalance_runs
   where bot_id = b.id
   order by started_at desc
   limit 1
) r on true;

grant select on public.bot_overview to authenticated;
