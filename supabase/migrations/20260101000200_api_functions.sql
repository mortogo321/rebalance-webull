-- =============================================================================
-- 0200 — transactional write API
--
-- A bot and its target basket must appear together or not at all. Doing that
-- from the edge function would mean two HTTP round trips with no shared
-- transaction: a failure between them leaves a bot with no holdings, or a
-- basket that never reaches 100%. These functions make each write one
-- statement, one transaction.
--
-- All are SECURITY DEFINER (they write tables that `authenticated` has no
-- privileges on) and all take p_user_id explicitly. The caller is the edge
-- function using the service role, which has already verified the JWT; these
-- functions re-check ownership on every row they touch so a wrong id fails
-- closed rather than editing someone else's bot.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- helpers
-- -----------------------------------------------------------------------------
create or replace function private.assert_owns_bot(p_user_id uuid, p_bot_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  if not exists (
    select 1 from public.bots where id = p_bot_id and user_id = p_user_id
  ) then
    -- Same error for "not yours" and "does not exist": distinguishing them
    -- would confirm the existence of another user's bot.
    raise exception 'bot not found' using errcode = 'no_data_found';
  end if;
end;
$$;

create or replace function private.write_targets(
  p_user_id uuid, p_bot_id uuid, p_targets jsonb
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  delete from public.bot_targets where bot_id = p_bot_id;

  insert into public.bot_targets (bot_id, user_id, symbol, target_weight_bps)
  select p_bot_id, p_user_id, upper(t->>'symbol'), (t->>'weight_bps')::int
    from jsonb_array_elements(p_targets) as t;
  -- The deferred bot_targets_sum_check trigger fires at commit, so the delete
  -- and re-insert above may pass through an unbalanced intermediate state
  -- without the basket ever being committed unbalanced.
end;
$$;

-- -----------------------------------------------------------------------------
-- create
-- -----------------------------------------------------------------------------
create or replace function public.create_bot(
  p_user_id uuid, p_bot jsonb, p_targets jsonb
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_bot_id uuid;
begin
  insert into public.bots (
    user_id, name, base_currency, investment_amount, trigger_type,
    interval_minutes, drift_threshold_bps, min_order_value,
    cash_buffer_bps, allow_fractional, status
  )
  values (
    p_user_id,
    p_bot->>'name',
    coalesce(p_bot->>'base_currency', 'USD'),
    (p_bot->>'investment_amount')::numeric,
    (p_bot->>'trigger_type')::public.trigger_type,
    nullif(p_bot->>'interval_minutes', '')::int,
    nullif(p_bot->>'drift_threshold_bps', '')::int,
    coalesce(nullif(p_bot->>'min_order_value', '')::numeric, 10),
    coalesce(nullif(p_bot->>'cash_buffer_bps', '')::int, 0),
    coalesce((p_bot->>'allow_fractional')::boolean, false),
    'draft'
  )
  returning id into v_bot_id;

  perform private.write_targets(p_user_id, v_bot_id, p_targets);
  return v_bot_id;
end;
$$;

-- -----------------------------------------------------------------------------
-- update
-- -----------------------------------------------------------------------------
create or replace function public.update_bot(
  p_user_id uuid, p_bot_id uuid, p_patch jsonb, p_targets jsonb default null
)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.assert_owns_bot(p_user_id, p_bot_id);

  -- A running bot may be paused but not re-specified: changing the basket
  -- mid-flight would let a rebalance start against one target set and finish
  -- against another.
  if exists (select 1 from public.bots where id = p_bot_id and status = 'running') then
    raise exception 'pause the bot before changing its configuration'
      using errcode = 'object_not_in_prerequisite_state';
  end if;

  update public.bots set
    name                = coalesce(p_patch->>'name', name),
    base_currency       = coalesce(p_patch->>'base_currency', base_currency),
    investment_amount   = coalesce(nullif(p_patch->>'investment_amount', '')::numeric, investment_amount),
    trigger_type        = coalesce(nullif(p_patch->>'trigger_type', '')::public.trigger_type, trigger_type),
    interval_minutes    = case when p_patch ? 'interval_minutes'
                               then nullif(p_patch->>'interval_minutes', '')::int else interval_minutes end,
    drift_threshold_bps = case when p_patch ? 'drift_threshold_bps'
                               then nullif(p_patch->>'drift_threshold_bps', '')::int else drift_threshold_bps end,
    min_order_value     = coalesce(nullif(p_patch->>'min_order_value', '')::numeric, min_order_value),
    cash_buffer_bps     = coalesce(nullif(p_patch->>'cash_buffer_bps', '')::int, cash_buffer_bps),
    allow_fractional    = coalesce((p_patch->>'allow_fractional')::boolean, allow_fractional)
  where id = p_bot_id and user_id = p_user_id;

  if p_targets is not null then
    perform private.write_targets(p_user_id, p_bot_id, p_targets);
  end if;
end;
$$;

-- -----------------------------------------------------------------------------
-- lifecycle
-- -----------------------------------------------------------------------------
create or replace function public.set_bot_status(
  p_user_id uuid, p_bot_id uuid, p_status public.bot_status
)
returns public.bots
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_bot public.bots;
  v_targets int;
begin
  perform private.assert_owns_bot(p_user_id, p_bot_id);

  if p_status = 'running' then
    select count(*) into v_targets from public.bot_targets where bot_id = p_bot_id;
    if v_targets = 0 then
      raise exception 'add target holdings before starting this bot'
        using errcode = 'check_violation';
    end if;
  end if;

  update public.bots
     set status = p_status,
         -- Starting a bot makes it due immediately. Waiting a full interval
         -- before the first run makes a freshly started bot look broken.
         next_run_at = case
           when p_status = 'running' then now()
           else next_run_at
         end
   where id = p_bot_id and user_id = p_user_id
   returning * into v_bot;

  return v_bot;
end;
$$;

create or replace function public.delete_bot(p_user_id uuid, p_bot_id uuid)
returns void
language plpgsql
security definer
set search_path = ''
as $$
begin
  perform private.assert_owns_bot(p_user_id, p_bot_id);
  -- Runs, orders and targets cascade. The history goes with the bot on purpose:
  -- orphaned fills that no longer belong to anything are worse than no history.
  delete from public.bots where id = p_bot_id and user_id = p_user_id;
end;
$$;

-- -----------------------------------------------------------------------------
-- These are the service role's write API. `authenticated` must not reach them
-- directly: the edge function is what verifies the JWT and supplies p_user_id,
-- and a client able to call them could pass any user id it liked.
-- -----------------------------------------------------------------------------
revoke all on function public.create_bot(uuid, jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.update_bot(uuid, uuid, jsonb, jsonb) from public, anon, authenticated;
revoke all on function public.set_bot_status(uuid, uuid, public.bot_status) from public, anon, authenticated;
revoke all on function public.delete_bot(uuid, uuid) from public, anon, authenticated;

grant execute on function public.create_bot(uuid, jsonb, jsonb) to service_role;
grant execute on function public.update_bot(uuid, uuid, jsonb, jsonb) to service_role;
grant execute on function public.set_bot_status(uuid, uuid, public.bot_status) to service_role;
grant execute on function public.delete_bot(uuid, uuid) to service_role;
