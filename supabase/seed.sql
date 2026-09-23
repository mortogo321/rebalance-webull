-- =============================================================================
-- seed — runs automatically on `supabase db reset`.
--
-- The assignment asks for no sign-up page, so users are created here. Two of
-- them, not one: a single-user seed cannot demonstrate that RLS isolates
-- anything. Log in as the second user and every screen must be empty.
--
-- These are local development credentials for a throwaway stack. They are not
-- secrets, and this file is only ever loaded by the local development stack,
-- never into uat/production.
-- =============================================================================

create extension if not exists pgcrypto with schema extensions;

-- -----------------------------------------------------------------------------
-- users
-- -----------------------------------------------------------------------------
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password,
  email_confirmed_at, raw_app_meta_data, raw_user_meta_data,
  created_at, updated_at
)
values
  (
    '00000000-0000-0000-0000-000000000000',
    'a0000000-0000-4000-8000-000000000001',
    'authenticated', 'authenticated',
    'demo@rebalance.test',
    extensions.crypt('Password123!', extensions.gen_salt('bf')),
    now(),
    '{"provider":"email","providers":["email"]}',
    '{"display_name":"Demo Trader"}',
    now(), now()
  ),
  (
    '00000000-0000-0000-0000-000000000000',
    'a0000000-0000-4000-8000-000000000002',
    'authenticated', 'authenticated',
    'second@rebalance.test',
    extensions.crypt('Password123!', extensions.gen_salt('bf')),
    now(),
    '{"provider":"email","providers":["email"]}',
    '{"display_name":"Isolation Check"}',
    now(), now()
  )
on conflict (id) do nothing;

-- GoTrue will not authenticate a user without a matching identity row.
insert into auth.identities (
  id, user_id, provider_id, identity_data, provider, last_sign_in_at, created_at, updated_at
)
values
  (
    gen_random_uuid(),
    'a0000000-0000-4000-8000-000000000001',
    'a0000000-0000-4000-8000-000000000001',
    '{"sub":"a0000000-0000-4000-8000-000000000001","email":"demo@rebalance.test","email_verified":true,"phone_verified":false}',
    'email', now(), now(), now()
  ),
  (
    gen_random_uuid(),
    'a0000000-0000-4000-8000-000000000002',
    'a0000000-0000-4000-8000-000000000002',
    '{"sub":"a0000000-0000-4000-8000-000000000002","email":"second@rebalance.test","email_verified":true,"phone_verified":false}',
    'email', now(), now(), now()
  )
on conflict do nothing;

-- -----------------------------------------------------------------------------
-- paper broker: starting cash so the mock broker has something to trade
-- -----------------------------------------------------------------------------
insert into private.paper_accounts (user_id, cash, currency)
values
  ('a0000000-0000-4000-8000-000000000001', 100000.0000, 'USD'),
  ('a0000000-0000-4000-8000-000000000002',  50000.0000, 'USD')
on conflict (user_id) do nothing;

-- Demo user starts deliberately lopsided -- ~78% AAPL against a 40% target --
-- so the very first drift evaluation has real work to do and the UI shows a
-- meaningful before/after instead of a flat, already-balanced portfolio.
insert into private.paper_positions (user_id, symbol, quantity, avg_cost)
values
  ('a0000000-0000-4000-8000-000000000001', 'AAPL', 300.00000000, 190.0000),
  ('a0000000-0000-4000-8000-000000000001', 'MSFT',  20.00000000, 400.0000)
on conflict (user_id, symbol) do nothing;

-- -----------------------------------------------------------------------------
-- a ready-made bot for the demo user (user 2 intentionally gets nothing)
-- -----------------------------------------------------------------------------
insert into public.broker_connections (user_id, status, environment, account_currency)
values ('a0000000-0000-4000-8000-000000000001', 'disconnected', 'mock', 'USD')
on conflict (user_id) do nothing;

insert into public.bots (
  id, user_id, name, status, base_currency, investment_amount,
  trigger_type, interval_minutes, drift_threshold_bps,
  min_order_value, cash_buffer_bps, allow_fractional
)
values (
  'b0000000-0000-4000-8000-000000000001',
  'a0000000-0000-4000-8000-000000000001',
  'Core Three',
  'draft',
  'USD',
  100000.0000,
  'schedule_or_drift',
  60,
  500,          -- rebalance once any holding drifts 5% off target
  25.0000,
  200,          -- hold 2% back in cash
  false
)
on conflict (id) do nothing;

insert into public.bot_targets (bot_id, user_id, symbol, target_weight_bps)
values
  ('b0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'AAPL', 4000),
  ('b0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'MSFT', 3500),
  ('b0000000-0000-4000-8000-000000000001', 'a0000000-0000-4000-8000-000000000001', 'NVDA', 2500)
on conflict (bot_id, symbol) do nothing;
