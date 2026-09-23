# Rebalance Webull

![CI](https://github.com/mortogo321/rebalance-webull/actions/workflows/ci.yml/badge.svg)

Sign in, connect a Webull account with an API key, and set a bot that holds a
target portfolio mix by trading the difference automatically.

Everything runs on Docker on one machine. No order ever reaches a production
venue — the default broker is a deterministic paper broker, and the Webull
adapter is pinned to a UAT host by a startup guard that refuses to boot
otherwise.

```sh
docker compose up -d --build
```

Then open <http://127.0.0.1:3000> and sign in as `demo@rebalance.test` /
`Password123!`.

---

## Contents

- [Quick start](#quick-start) · [Architecture](#architecture) · [Database design](#database-design)
- [Rebalance logic](#rebalance-logic) · [When a bot runs](#when-a-bot-runs) · [Security](#security)
- [Decisions](#decisions-i-made) · [Limitations](#limitations) · [Tests](#tests) · [Layout](#layout)

---

## Quick start

Requires Docker Desktop. Bun is needed only to run the web test suite on the
host; the app itself does not need it installed.

```sh
git clone <repo> && cd rebalance-webull
docker compose up -d --build   # start everything
docker compose ps              # what is running
docker compose logs -f engine  # follow a service
docker compose down            # stop; add -v to drop the database too
```

`--build` matters after any change under `api/`, `web/` or `supabase/`: a plain
`up` reuses the cached image and your change silently will not be running.

The whole stack is defined in `compose.yml`, including Postgres, GoTrue and
PostgREST. There is no `supabase start` step and no external network — the
Supabase CLI is optional tooling here, not a prerequisite.

| | |
|---|---|
| Web app | <http://127.0.0.1:3000> |
| Backend API | <http://127.0.0.1:54331/api/health> |
| Engine | <http://127.0.0.1:8000/ready> |
| Supabase gateway | <http://127.0.0.1:54321> (`/auth/v1`, `/rest/v1`) |
| Postgres | `postgres://postgres:postgres@127.0.0.1:54322/postgres` |

Studio and Inbucket are not part of this stack — they come with the Supabase
CLI, which `compose.yml` deliberately does not depend on. `psql` against the
port above covers what Studio was used for.

| Seeded user | Password | Starts with |
|---|---|---|
| `demo@rebalance.test` | `Password123!` | $100k cash, 300 AAPL, 20 MSFT, one draft bot |
| `second@rebalance.test` | `Password123!` | $50k cash, nothing else |

The second user exists to make tenant isolation checkable rather than claimed:
sign in as them and every screen is empty.

Start order is handled by `compose.yml`, not by you: Postgres comes up, GoTrue
migrates the `auth` schema, the engine's entrypoint applies
`supabase/migrations/*.sql` and `seed.sql`, and only then do PostgREST, the
gateway, the backend API and the web app start. Every step is a `depends_on`
condition, so a failure stops the stack rather than half-starting it.

### Three things containerising this stack breaks

All three are fixed; they are written down because each one presents as
something other than what it is, and the next person to move a service will
meet them again.

- **`docker compose up` without `--build` reuses a stale image.** After any
  change under `api/`, `web/` or `supabase/`, a plain `up` silently runs the old
  code. It first showed up as `relation "public.bots" does not exist` — from an
  engine image built before the entrypoint existed.
- **Next's dev server blocks cross-origin dev resources.** In a container it
  does not recognise the host's `127.0.0.1:3000` as its own origin, so it
  refuses `/_next/hmr` and `/__nextjs_font/*`. The HMR handshake then fails, the
  Turbopack client never boots, and the page server-renders correctly and never
  hydrates — every button dead, every panel stuck on its initial state, and not
  one HTTP error anywhere. `allowedDevOrigins` in `next.config.ts` is the fix,
  and `e2e.sh` now asserts it.
- **A server-side URL is not a client-side URL.** The browser must reach the
  gateway at `127.0.0.1:54321`; inside the container that address is the
  container itself, so server components and `proxy.ts` use
  `SUPABASE_INTERNAL_URL`. Because `@supabase/ssr` derives its cookie name from
  whichever URL it was given, the two would otherwise store the session under
  different names — sign-in appears to work, redirects, and then every
  client-side call reports no session. `lib/supabase/cookie.ts` pins one name.

---

## Architecture

Three application services plus a self-hosted Supabase platform, all in one
`compose.yml`. The split is not decoration: each boundary exists to keep one
specific thing in one place.

```mermaid
flowchart TB
    B["Browser"]

    subgraph web["web/ — Next.js 16 · React 19"]
        RSC["Server Components<br/>read via RLS"]
        CC["Client Components<br/>write via the API"]
        PX["proxy.ts<br/>session refresh + route guard"]
    end

    subgraph sb["supabase/ — Deno · Hono"]
        API["Edge Function <b>api</b><br/>the only write path"]
    end

    subgraph api["api/ — Python 3.12 · FastAPI"]
        ENG["Rebalance engine"]
        SCH["Scheduler<br/>leader-elected"]
        PLAN["Planner — pure"]
    end

    subgraph pg["Supabase platform — self-hosted in compose.yml"]
        GW["gateway (nginx)<br/>one origin: /auth/v1 + /rest/v1"]
        AUTH["GoTrue<br/>owns the auth schema"]
        REST["PostgREST<br/>public only, RLS enforced"]
        DB[("Postgres 17<br/>public + private")]
    end

    BRK["Broker<br/>mock · Webull UAT"]

    B --> PX --> RSC & CC
    RSC -- "anon key + user JWT<br/>RLS applies" --> GW
    CC  -- "bearer JWT" --> API
    B -- "sign in" --> GW
    GW --> AUTH & REST
    AUTH --> DB
    REST -- "SET ROLE from the JWT" --> DB
    API -- "reads: user JWT, RLS applies" --> GW
    API -- "writes: service role<br/>SECURITY DEFINER fns" --> DB
    API -- "shared-token calls" --> ENG
    SCH --> ENG --> PLAN
    ENG -- "service role" --> DB
    ENG --> BRK

    classDef pure fill:#eef7ee,stroke:#4a7
    class PLAN pure
```

**Why the browser never reaches the engine.** Credentials are decrypted in
exactly one process. The engine holds the encryption key and the venue
connection; nothing else does, and nothing else can.

**Why reads and writes take different paths.** Reads go through the signed-in
user's JWT so Postgres RLS answers "whose row is this?" — a handler that forgets
a filter still returns nothing. Writes go through the edge function because the
invariants involved (weights totalling exactly 100%, "you may not re-specify a
running bot", "an order row may only be written from a broker response") span
rows and cannot be expressed as a row-level policy. `authenticated` holds no
INSERT/UPDATE/DELETE privilege anywhere.

**Why a separate Python engine.** The rebalancing calculation is the part worth
testing exhaustively, and it is the part that must not be rewritten in a second
language. Keeping it in one service with a pure core means the arithmetic is
exercised by 101 tests that need neither a database nor a broker.

Service names and directory names do not all match, which is worth knowing once:
service `engine` builds `./api`, and service `api` builds `./supabase` (the Hono
backend lives in `supabase/functions/api`, where the Supabase CLI requires it).

### One rebalance, end to end

```mermaid
sequenceDiagram
    participant S as Scheduler
    participant E as Engine
    participant DB as Postgres
    participant BK as Broker

    S->>DB: pg_try_advisory_lock — am I the leader?
    DB-->>S: yes
    S->>DB: running bots + their baskets (2 queries, no N+1)
    loop each bot
        E->>BK: cash, positions, quotes
        E->>E: build_plan() — pure, no I/O
        E->>E: evaluate_trigger(plan.max_drift_bps)
        alt not due
            E->>DB: rebalance_runs (status=skipped, reason)
        else due
            E->>DB: rebalance_runs (status=running, full plan snapshot)
            loop each planned order, sells first
                E->>DB: orders (status=pending, client_order_id)
                E->>BK: place_order(client_order_id)
                BK-->>E: filled / rejected
                E->>DB: orders (fill or reject reason)
            end
            E->>DB: finish_run + advance the bot's schedule
        end
    end
```

A skipped evaluation is still written as a run. "Why didn't my bot trade?" is
the question a rebalancer has to answer most often, and a bot that goes quiet
without a record cannot answer it.

---

## Database design

```mermaid
erDiagram
    users ||--o| broker_connections : "has"
    users ||--o| broker_credentials : "has"
    users ||--o{ bots : owns
    bots  ||--o{ bot_targets : "target basket"
    bots  ||--o{ rebalance_runs : "evaluations"
    rebalance_runs ||--o{ orders : "placed"
    users ||--o| paper_accounts : "mock broker"
    users ||--o{ paper_positions : "mock broker"

    users {
        uuid id PK
        text email
    }
    broker_connections {
        uuid user_id PK
        enum status "disconnected|connected|error"
        enum environment "mock|webull_uat"
        text api_key_hint "last 4 chars only"
        text last_error
    }
    broker_credentials {
        uuid user_id PK
        text api_key_encrypted "AES-256-GCM envelope"
        text api_secret_encrypted "AES-256-GCM envelope"
        int key_version
    }
    bots {
        uuid id PK
        uuid user_id FK
        enum status "draft|running|paused|stopped"
        numeric investment_amount "the sleeve"
        enum trigger_type "schedule|drift|schedule_or_drift"
        int interval_minutes
        int drift_threshold_bps
        numeric min_order_value
        int cash_buffer_bps
        bool allow_fractional
        timestamptz next_run_at
    }
    bot_targets {
        uuid bot_id FK
        uuid user_id FK "denormalised for RLS"
        text symbol
        int target_weight_bps "must total 10000"
    }
    rebalance_runs {
        uuid id PK
        enum status "succeeded|failed|skipped|..."
        text trigger_reason "human readable"
        int max_drift_bps
        jsonb plan "full audit snapshot"
    }
    orders {
        uuid id PK
        enum side "buy|sell"
        numeric quantity
        numeric filled_quantity
        text client_order_id UK "idempotency key"
        text reject_reason
    }
```

Tables in **`private`** — `broker_credentials`, `paper_accounts`,
`paper_positions` — are shaded off the API entirely: that schema is not listed in
`config.toml [api].schemas`, so PostgREST has no route to it at all.

### The choices worth defending

| Choice | Why |
|---|---|
| **Two schemas, `public` and `private`** | Encrypted credentials are unreachable over HTTP by *construction*, not by policy. The schema allowlist is the first lock; RLS with zero policies is the second; no grants to the API roles is the third. A future migration that mistakenly exposes the schema still finds the tables shut. |
| **Weights as integer basis points, never floats** | 33.33% is `3333`. "Must total 100%" becomes an exact integer comparison. Summing weights in binary floating point is how a rebalancer develops a slow leak. |
| **`numeric(20,4)` money, `numeric(20,8)` quantities** | No float anywhere, in Postgres or in Python. `0.1 + 0.2 != 0.3` is not a curiosity in a trading system; it is a position that stops matching the broker's books. |
| **`user_id` denormalised onto `bot_targets`, `orders`, `rebalance_runs`** | RLS becomes a single-column comparison with no subquery per row, and a stray row cannot be orphaned onto another user. |
| **Weights enforced by a *deferrable* constraint trigger** | It spans rows, so it cannot be a `CHECK`. Deferred to commit, a transaction may pass through an unbalanced intermediate state while rewriting a basket, but can never *commit* unbalanced. |
| **The whole plan stored as `jsonb` on every run** | The prices used, the holdings seen, per-symbol target vs actual, and every order considered *including the ones filtered out*. Without it, "why did the bot do that?" is unanswerable a day later. |
| **`client_order_id` unique, derived from `(run, index, symbol, side)`** | A retried submit collides on the index instead of double-trading. The same key is passed to the venue as its idempotency key. |
| **A run row even when nothing traded** | Status `skipped` plus a human-readable reason. Silence is the worst possible audit trail. |
| **`bot_overview` view with `security_invoker = on`** | The list screen needs each bot's latest run; a lateral join does it in one query. Without `security_invoker`, a view is a hole straight through RLS. |
| **Paper broker state in Postgres, not in memory** | Restarting the engine mid-demo does not silently reset everyone's portfolio. |

---

## Rebalance logic

The planner (`api/src/engine/rebalance.py`) is a **pure function**: no database,
no network, no clock, no logging. Given cash, holdings, a price map and the
bot's settings it returns the orders that move the portfolio toward its target
weights — or explains, per symbol, why it declined to.

Purity is the point. Trading logic that can only be exercised by standing up
Postgres and a broker does not get tested at the edges, and the edges — rounding
to whole shares, a sell that fails to raise the cash the buys assumed, a holding
that has fallen out of the basket — are exactly where a rebalancer loses money.

```mermaid
flowchart TD
    A["portfolio_value = cash + Σ qty × price"] --> B["investable = min(investment_amount, portfolio_value)"]
    B --> C["deployable = investable − cash_buffer"]
    C --> D["target_value(s) = deployable × weight(s)<br/>held but not in the basket → 0"]
    D --> E["compute per-symbol drift in bps"]
    E --> F["plan SELLS<br/>overweight, and full exits"]
    F --> G["budget = cash + expected proceeds − buffer"]
    G --> H["plan BUYS from the budget<br/>most underweight first"]
    H --> I["drop dust, unaffordable<br/>and zero-share orders → skipped[]"]
    I --> J["cap at max_orders<br/>sells lead, they fund the buys"]
```

**Weights are measured against `deployable`, not `investable`.** Otherwise every
holding reads as permanently short by the size of the cash buffer, and a bot at
rest re-triggers a drift rebalance forever.

**The bot manages a sleeve.** It never deploys more than `investment_amount`,
and if the account holds less than that it works with what is actually there.

**Sells are planned first and buys are funded from their expected proceeds.** In
exact arithmetic the budget always covers the buys. It stops being exact the
moment a sell rounds down to whole shares or is dropped for being under the
minimum — so the budget is *enforced*, not assumed.

**Quantities always round down** (`money.py`). Rounding a share count up invents
shares the cash may not cover, which surfaces as a broker rejection. A full exit
is the one exception: a position leaving the basket is sold whole, because
rounding there would strand a fractional remainder forever.

### Worked example

Cash $10,000 · holdings 300 AAPL @ $200, 20 MSFT @ $400 · targets AAPL 40%,
MSFT 35%, NVDA 25% (@ $125) · `investment_amount` $100,000 · 2% cash buffer ·
whole shares only.

```
portfolio_value  10,000 + 60,000 + 8,000      = 78,000
investable       min(100,000, 78,000)         = 78,000   ← account is the binding constraint
deployable       78,000 − 2%                  = 76,440
```

| Symbol | Held | Value | Weight | Target | Target value | Drift |
|---|---:|---:|---:|---:|---:|---:|
| AAPL | 300 | 60,000 | 78.49% | 40% | 30,576 | **+3849 bps** |
| MSFT | 20 | 8,000 | 10.47% | 35% | 26,754 | −2453 bps |
| NVDA | 0 | 0 | 0.00% | 25% | 19,110 | −2500 bps |

```
SELL 147 AAPL @ 200 = 29,400     29,424 ÷ 200 = 147.12 → 147 whole shares
budget = 10,000 + 29,400 − 1,560 = 37,840
BUY  152 NVDA @ 125 = 19,000     most underweight first (−2500 bps)
BUY   46 MSFT @ 400 = 18,400     then −2453 bps; 18,754 affordable → 46 shares
                                 projected cash after: 2,000
```

`max_drift_bps` for the run is **3849** — the number the drift trigger compares
against the bot's threshold.

---

## When a bot runs

Deciding *whether* to trade is separated from deciding *what* to trade
(`api/src/engine/triggers.py`): the two answer different questions and fail in
different ways, and both are pure and tested independently.

| Trigger | Fires when |
|---|---|
| `schedule` | `now >= next_run_at`, every `interval_minutes` |
| `drift` | any holding's `\|drift\|` reaches `drift_threshold_bps` |
| `schedule_or_drift` | whichever comes first — and the stored reason names which |

The plan is built *before* the trigger is evaluated, because the drift trigger
needs a plan to know how far off target the portfolio is — and a plan is cheap,
because it is pure.

A bot that has never run is due immediately; otherwise starting a bot appears to
do nothing for a full interval. A naive datetime is read as UTC, never as
machine-local time — a scheduler that gets that wrong fires at the wrong hour on
any server whose clock is not UTC.

The scheduler is a plain asyncio loop that elects a single leader with a
Postgres session-level advisory lock. Two engines on one database would each
plan the same rebalance and place it twice; the standby ticks, fails to take the
lock, and does nothing. A crashed leader's lock is released by Postgres, so
takeover needs no intervention.

---

## Security

**API keys are encrypted before storage and never returned to the browser.**

```
envelope := v<key_version>.<base64url(iv)>.<base64url(ciphertext || tag)>
```

AES-256-GCM, a fresh 96-bit IV per encryption, and **the owning `user_id` bound
in as additional authenticated data** — a ciphertext lifted from one user's row
and pasted onto another fails authentication instead of decrypting. The format
is a cross-runtime contract (Deno writes, Python reads), so it is pinned by a
test that runs the *real other runtime* rather than a copied-in constant
(`api/tests/test_crypto_interop.py`).

The browser only ever learns a 4-character hint (`···3344`) and a connection
status. `private.broker_credentials` has no HTTP route, no grants to the API
roles, and RLS forced on with zero policies.

| Control | Where |
|---|---|
| Tenant isolation on reads | RLS policies keyed on `(select auth.uid()) = user_id` |
| No client writes at all | `INSERT/UPDATE/DELETE` revoked from `anon` and `authenticated` on every table |
| Writes re-check ownership | `SECURITY DEFINER` functions with `set search_path = ''`, each taking `p_user_id` and asserting it |
| Engine has no session | `/internal/*` gated by a shared bearer token compared with `secrets.compare_digest` |
| Service-role queries | every statement in `repo.py` carries an explicit `user_id` predicate — the database is not filtering on that path, so the code must |
| Production venue unreachable | `WEBULL_ALLOWED_HOSTS` checked at startup *and* per request; a bad host aborts boot |
| Development can't reach a venue | `APP_ENV=development` with `BROKER_MODE != mock` refuses to start |
| Login enumeration | one message for "no such user" and "wrong password" |
| Open redirect | `?next=` accepted only when same-origin |
| Error leakage | one error contract; raw Postgres and driver text never reaches a response |

Not-yours and does-not-exist return the same error everywhere, so no endpoint
confirms the existence of another user's bot.

---

## Decisions I made

The brief left database, API and code structure open. These are the calls, and
what each one cost.

1. **The mock broker is the default in every environment, including production.**
   The brief forbids production trading. Rather than relying on remembering a
   flag, `WEBULL_ALLOWED_HOSTS` contains no production host anywhere in the repo,
   and a mismatch aborts startup. Flipping to Webull UAT is one variable.

2. **A pure planner, separated from the service that executes it.** Buys the
   ability to test rounding, budget exhaustion, liquidation and order-cap
   behaviour exhaustively without Postgres or a venue. Costs one extra layer.

3. **Weights in basis points, money in `Decimal`/`numeric`.** Exactness over
   convenience, everywhere, with no float in the system at all.

4. **All writes through the edge function; RLS is read-only.** The alternative —
   client-side inserts guarded by write policies — cannot express the invariants
   that actually matter here, and would let a client invent a fill history.

5. **Credentials encrypted in the engine, not in the edge function.** One process
   holds the key. The Deno mirror implementation exists and is contract-tested,
   but is not on the live path.

6. **Skipped evaluations are recorded as runs.** Costs rows; buys an audit trail
   that explains its own inactivity.

7. **Leader election via advisory lock rather than a job queue.** A queue would
   be the right answer at scale; for a POC it is a dependency and a second source
   of truth about timing, and the lock gets the safety property that matters.

8. **Deterministic mock prices** — a pure function of `(symbol, date, seed)`.
   Prices move day to day so drift is demonstrable, and are identical on every
   machine and re-run so tests stay deterministic.

9. **No root `package.json`.** `web/` is the only stack with a lockfile; it lives
   there because `web/Dockerfile` builds with `./web` as its context, and a root
   lockfile outside that context makes `bun install --frozen-lockfile` silently
   fall back to an unpinned install.

10. **Each stack declares its own compose variables.** There is no shared compose
    env file: `ENGINE_PORT` lives in `api/.env.<env>`, `WEB_PORT` in
    `web/.env.<env>`, the Supabase project and network in
    `supabase/functions/.env.<env>`, and all three are passed together.

---

## Limitations

Stated plainly, because a POC that hides its edges is worse than one that names
them.

- **The Webull adapter has never run against a live endpoint.** Signing is
  implemented from the published specification and asserted against the worked
  example in the vendor docs, but the resource paths are documented shapes, not
  verified responses. Treat `BROKER_MODE=webull_uat` as ready-to-integrate, not
  as proven. `mock` is the default for exactly this reason.
- **Market orders only.** No limit prices, no time-in-force beyond `DAY`, no
  partial-fill reconciliation loop — a partial fill is recorded and the next run
  simply re-plans from the new position.
- **No market-hours awareness.** The scheduler will happily evaluate at 3am. A
  real system needs a trading calendar.
- **No commission or slippage model.** `min_order_value` is the blunt stand-in
  for "this trade is not worth making".
- **Prices come from the broker at plan time and are not re-checked at submit
  time.** Between planning and filling, a fast market can move; the recorded
  plan shows the prices the decision was made on.
- **One currency per bot.** `base_currency` exists but no FX conversion does.
- **The engine trusts `user_id` on `/internal/*`.** That is safe only because
  nginx does not route to those paths and the shared token gates them; it is not
  a boundary that would survive being exposed.
- **No rate limiting** on the edge function.
- **The seed is applied once, then recorded.** `applied_migrations` makes a
  restart a no-op, so edits made in the UI survive `docker compose restart`.
  Re-seeding means `docker compose down -v`, which drops the database.
- **The compose stack is not the Supabase platform.** GoTrue and PostgREST are
  real, but Studio, Realtime and Storage are absent, and the dev keys are plain
  HS256 JWTs rather than the CLI's `sb_publishable_`/`sb_secret_` format, which
  only Kong understands. `supabase/` is untouched, so the CLI still works for
  anything needing the full platform.

---

## Tests

CI (`.github/workflows/ci.yml`) runs the static checks and both unit suites on
every push and pull request: `ruff check`, `ruff format --check`, `mypy` and
`pytest` for the engine; `biome`, `tsc`, `vitest` and `next build` for the web
app; `deno lint`, `deno fmt --check` and `deno check` for the edge function;
plus `docker compose config` validation. Dependabot checks weekly.

```sh
# Python engine — inside the image, so no host Python is needed
docker build --target test -t rebalance-api-test api/ && docker run --rm rebalance-api-test

# Web — on the host
cd web && bun run test && bun run typecheck && bun run lint

# End to end, against a running stack
docker compose up -d --build && ./infra/scripts/e2e.sh
```

**101 passing, 3 skipped** across 6 files in `api/tests`, **29 passing** across
2 files in `web/tests`, and **15 end-to-end checks**. The 3 skips are the
Deno↔Python crypto interop checks, which run only where `deno` is on PATH — CI
installs it, so the contract is enforced there.

`e2e.sh` covers what neither unit suite structurally can: a real password grant
from GoTrue, RLS enforced by Postgres against a real JWT, and the schema
allowlist keeping `private` off HTTP. The isolation check earns its place — it
is the only assertion here that fails if an RLS policy is dropped, and a stack
that passes everything else can still be showing every user every portfolio.

Two of the checks assert the web client *boots*, not merely that pages return
200. They exist because an earlier version of this suite went fully green
against an app that server-rendered correctly and then never hydrated: every
page looked right, and no button worked. An HTTP status cannot see that; the
HMR handshake and the dev server's own cross-origin log can.

| File | Covers |
|---|---|
| `web/tests/targets.test.ts` | basket validation: exact-100% in basis points, duplicate symbols, float-error cases |
| `web/tests/format.test.ts` | display formatting of `numeric` strings, null handling, relative times (TZ-pinned) |

| `test_rebalance.py` | the planner: rounding, liquidation, budget exhaustion, order caps, buffer handling, weight validation |
| `test_triggers.py` | schedule / drift / combined, naive-datetime handling, never-run bots |
| `test_pricing.py` | determinism of the mock quote generator |
| `test_crypto.py` | envelope format, AAD binding, tamper and wrong-key rejection |
| `test_crypto_interop.py` | the same envelope written by Deno and read by Python, and back |
| `test_webull_signing.py` | the canonical string and HMAC against the vendor's worked example |

The Python tests run inside the image rather than a host virtualenv, so a stale
local `.venv` cannot shadow the container's dependencies. The `test` stage
deliberately does not inherit the `ENTRYPOINT` the other stages use, so pytest
starts immediately instead of waiting for a database it does not need.

---

## Layout

```
compose.yml            the entire system: database, auth, data API, gateway, three app services
api/                   Python 3.12 rebalance engine        → service `engine`
api/start.sh           engine entrypoint — applies the schema, then serves
api/db/                gotrue-normalize.sql, shipped in the engine image
supabase/              migrations, seed, Hono backend      → service `api`
web/                   Next.js 16 / React 19               → service `web`
infra/postgres/        auth-shim.sql — what the Supabase platform would pre-create
infra/gateway/         nginx routing /auth/v1 and /rest/v1 onto one origin
infra/scripts/         e2e.sh, gen-keys.sh
infra/nginx/           the UAT blue/green edge proxy (unrelated to the gateway above)
```

Each stack is a self-contained container: its own `Dockerfile`, `.dockerignore`,
`.env.*` and dependency state all live at its root, and nothing about a stack
lives outside its directory. Every `Dockerfile` is multi-stage, so the same file
produces the dev container, the test container and the slim runtime image:

| Stack | Stages |
|---|---|
| `api/` | `base` → `deps` → `development` · `test` · `runtime` |
| `web/` | `base` → `deps` → `development` · `builder` → `runtime` (Node, `output: "standalone"`) |
| `supabase/` | `base` → `cache` → `development` · `runtime` |

There is no build step outside a container for the app itself: `docker build
--target test api/` runs pytest inside the image, so a stale local `.venv`
cannot shadow its dependencies. Only the web test suite runs on the host, and
only because Vitest is faster there than a container round trip.

This documentation lives in the README rather than a `docs/` tree on purpose:
one file that is read is worth more than four that drift.

`supabase/` cannot be renamed or moved: the CLI discovers the project by walking
up for `supabase/config.toml`, and deploys each subdirectory of `functions/` as a
function named after it — so `functions/api/` *is* the `api` edge function.
`supabase/Dockerfile` runs that same Hono source as a plain Deno container so UAT
can put the API behind the same nginx switch as web and engine; it must stay out
of `functions/`, or the CLI tries to load it as a function.

### Environments

| | `development` | `uat` / `production` |
|---|---|---|
| Supabase | self-hosted in `compose.yml` | hosted project |
| Auth keys | committed HS256 JWTs | `sb_*` keys from the hosted project |
| Broker | mock | mock, or Webull UAT |
| Secrets | committed non-secret defaults | injected from CI, overriding the blanks |
| Schema | engine entrypoint, on start | a deploy step, before the new image serves |

```sh
docker compose up -d --build                             # development

# uat / production layer an overlay over the same base, and pass the env files
# for the environment being targeted:
docker compose -f compose.yml -f compose.uat.yml \
  --env-file api/.env.uat --env-file web/.env.uat \
  --env-file supabase/functions/.env.uat up -d
```

The overlay files are not written yet — `compose.yml` alone is development, and
deploying to a hosted Supabase project is the part this POC stops short of.

**There is no `.env.example`.** The committed `.env.<env>` files in each stack
are the example — they carry every variable the stack reads, in place, with
comments. A separate template would be a fourth copy of the same list and would
drift from the three that are actually loaded. Start from:

| Stack | File |
|---|---|
| Rebalance engine | `api/.env.development` · `.env.uat` · `.env.production` |
| Next.js | `web/.env.development` · `.env.uat` · `.env.production` |
| Hono backend | `supabase/functions/.env.development` · `.env.uat` · `.env.production` |

Every committed `.env.*` contains non-secret defaults only, so a clean clone
runs with no editing. Real secrets are injected as environment variables and
override the committed blanks — the same precedence in CI as locally. Generate a
fresh key pair with `./infra/scripts/gen-keys.sh`.

> `APP_ENCRYPTION_KEY` must be byte-identical for the engine and the edge
> functions, and rotating it without re-encrypting stored rows makes every saved
> broker credential permanently unreadable.
