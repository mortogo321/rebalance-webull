-- =============================================================================
-- GoTrue-shaped shim for the compose-only Postgres.
--
-- `supabase/migrations/*.sql` and `seed.sql` are written against a database the
-- Supabase platform has already prepared: the API roles exist, `extensions`
-- holds pgcrypto, `public` has the platform's default grants, and `auth.uid()`
-- resolves the current user. None of that is true of a stock postgres image,
-- so this file creates it. Runs from docker-entrypoint-initdb.d, i.e. once,
-- before anything else can connect.
--
-- What is NOT here: `auth.users` and `auth.identities`. GoTrue owns those and
-- creates them with its own migrations at boot, so this file must not, or the
-- two definitions would diverge the first time GoTrue is upgraded. That
-- ordering is the reason migrations moved out of initdb into the one-shot
-- `migrate` service -- they carry foreign keys to auth.users, which does not
-- exist until GoTrue has started.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- roles
--
-- NOLOGIN: these are privilege sets that PostgREST switches into per request,
-- never accounts anything connects with directly.
-- -----------------------------------------------------------------------------
do $$
begin
  if not exists (select from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;

  -- The one role PostgREST actually logs in as. It holds no rights of its own;
  -- every request immediately SET ROLEs into anon or authenticated based on the
  -- JWT. Granting it the three above is what makes that switch legal.
  if not exists (select from pg_roles where rolname = 'authenticator') then
    create role authenticator login noinherit password 'postgres';
  end if;
end
$$;

grant anon, authenticated, service_role to authenticator;

-- -----------------------------------------------------------------------------
-- extensions
--
-- Supabase keeps extensions out of `public` so the PostgREST-exposed schema
-- contains only application objects. seed.sql calls extensions.crypt/gen_salt
-- by that qualified name, so the schema placement is load-bearing, not taste.
-- -----------------------------------------------------------------------------
create schema if not exists extensions;
grant usage on schema extensions to anon, authenticated, service_role;
create extension if not exists pgcrypto with schema extensions;

-- -----------------------------------------------------------------------------
-- default privileges
--
-- The platform grants the API roles everything on new `public` objects, and
-- 20260101000100_rls.sql then revokes what they must not have. Without these
-- defaults those revokes would be no-ops against grants that never existed,
-- and the RLS migration would silently stop meaning what it says.
-- Set before the migrations run, so it applies to the objects they create.
-- -----------------------------------------------------------------------------
grant usage on schema public to anon, authenticated, service_role;

alter default privileges in schema public
  grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public
  grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public
  grant all on sequences to anon, authenticated, service_role;

-- -----------------------------------------------------------------------------
-- auth schema
--
-- Created empty so GoTrue migrates into a schema that already has the right
-- grants. GoTrue adds users, identities, sessions, refresh_tokens and the rest;
-- `auth.uid()` is Supabase platform SQL rather than GoTrue's, so it is ours.
-- -----------------------------------------------------------------------------
create schema if not exists auth;
grant usage on schema auth to anon, authenticated, service_role;

create or replace function auth.uid()
  returns uuid
  language sql
  stable
as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

grant execute on function auth.uid() to anon, authenticated, service_role;
