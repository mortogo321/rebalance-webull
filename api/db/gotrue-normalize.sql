-- =============================================================================
-- Make seeded users loginable.
--
-- seed.sql inserts into auth.users without the token columns, so they are NULL.
-- GoTrue scans those into plain Go strings, not pointers, and a NULL is a hard
-- error -- every password grant fails with:
--
--   error finding user: sql: Scan error on column index 3, name
--   "confirmation_token": converting NULL to string is unsupported
--
-- Empty string is what GoTrue itself writes for "no token outstanding", so this
-- sets NULLs to that. It lives here rather than in seed.sql because seed.sql is
-- the CLI's, and under the CLI these users are created by GoTrue rather than
-- inserted directly; this is compensation for running GoTrue ourselves.
--
-- Driven by the catalog, not a hardcoded list: the exact set of token columns
-- changes between GoTrue versions, and naming one that does not exist yet (or
-- any more) would fail the whole bootstrap.
-- =============================================================================
do $$
declare
  col   text;
  fixed int := 0;
  n     int;
begin
  for col in
    select column_name
      from information_schema.columns
     where table_schema = 'auth'
       and table_name   = 'users'
       and data_type in ('character varying', 'text')
       and is_nullable  = 'YES'
       and (column_name like '%token%' or column_name like '%_change')
  loop
    execute format(
      'update auth.users set %1$I = %2$L where %1$I is null', col, ''
    );
    get diagnostics n = row_count;
    fixed := fixed + n;
  end loop;

  raise notice 'normalised % null token value(s) on auth.users', fixed;
end
$$;
