#!/usr/bin/env bash
# =============================================================================
# End-to-end check against a running stack.
#
#   docker compose up -d --build && ./infra/scripts/e2e.sh
#
# Exercises the paths unit tests structurally cannot: a real password grant from
# GoTrue, RLS enforced by Postgres against a real JWT, the schema allowlist, and
# the three services answering on their published ports. Exits non-zero on the
# first failure, so it is usable as a CI gate.
#
# The isolation case is the one that matters most: it is the only check here
# that would catch an RLS policy being dropped, and a stack where every other
# assertion passes can still be leaking every user's portfolio to every other.
# =============================================================================
set -uo pipefail

GATEWAY="${GATEWAY:-http://127.0.0.1:54321}"
API="${API:-http://127.0.0.1:54331/api}"
WEB="${WEB:-http://127.0.0.1:3000}"
ENGINE="${ENGINE:-http://127.0.0.1:8000}"
PASSWORD="${SEED_PASSWORD:-Password123!}"

# The anon key is the committed development one; it carries `role: anon` and is
# meaningless against any database but this local stack.
ANON="${ANON_KEY:-eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1sb2NhbCIsInJvbGUiOiJhbm9uIiwiaWF0IjoxNzY3MjI1NjAwLCJleHAiOjIwODI3NTg0MDB9.-klD3_pvFOioB6hf2pRPA5AVn4fI3oy-yQ7-iDZVNOg}"

# Wait for the slowest thing to bind before asserting anything. Compose's
# depends_on gates container start, not application readiness: `web` is the
# Next.js dev server, which compiles on first request and refuses connections
# for several seconds after its container is "Up". Without this the suite
# reports connection-refused as a test failure, which is a lie about the stack.
wait_for() {
  local name=$1 url=$2 i=0
  until curl -fsS -o /dev/null --max-time 3 "$url" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -ge "${E2E_WAIT_TRIES:-60}" ]; then
      echo "error: $name never became ready at $url" >&2
      exit 1
    fi
    sleep 2
  done
}

echo "==> waiting for the stack"
wait_for "gateway" "$GATEWAY/rest/v1/"
wait_for "engine"  "$ENGINE/health"
wait_for "api"     "$API/health"
wait_for "web"     "$WEB/login"

pass=0 fail=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; pass=$((pass + 1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n        expected: %s\n        actual:   %s\n' "$1" "$2" "$3"; fail=$((fail + 1)); }
check(){ [ "$2" = "$3" ] && ok "$1" || bad "$1" "$2" "$3"; }

login() {
  curl -fsS -X POST "$GATEWAY/auth/v1/token?grant_type=password" \
    -H "apikey: $ANON" -H 'content-type: application/json' \
    -d "{\"email\":\"$1\",\"password\":\"$PASSWORD\"}" 2>/dev/null \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
}

rest()   { curl -fsS "$GATEWAY/rest/v1/$1" -H "apikey: $ANON" ${2:+-H "authorization: Bearer $2"} 2>/dev/null; }
status() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

echo "==> auth"
DEMO=$(login demo@rebalance.test)
SECOND=$(login second@rebalance.test)
[ -n "$DEMO" ]   && ok "demo@rebalance.test signs in"   || bad "demo@rebalance.test signs in" "a JWT" "empty"
[ -n "$SECOND" ] && ok "second@rebalance.test signs in" || bad "second@rebalance.test signs in" "a JWT" "empty"
check "a wrong password is rejected" "400" \
  "$(status -X POST "$GATEWAY/auth/v1/token?grant_type=password" -H "apikey: $ANON" \
       -H 'content-type: application/json' -d '{"email":"demo@rebalance.test","password":"wrong"}')"

echo "==> row level security"
check "the seeded owner sees their bot" "Core Three" \
  "$(rest 'bot_overview?select=name' "$DEMO" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')"
check "a second user sees none of it" "[]" "$(rest 'bot_overview?select=name' "$SECOND")"
check "signed out reads nothing" "42501" \
  "$(curl -s "$GATEWAY/rest/v1/bot_overview?select=name" -H "apikey: $ANON" | sed -n 's/.*"code":"\([^"]*\)".*/\1/p')"

echo "==> schema allowlist"
# `private` holds encrypted broker credentials. PostgREST is configured with
# `public` only, so this must 404 as "no such table" rather than 401/403 —
# anything else means the schema is reachable and only access control is denying.
check "the private schema has no HTTP surface" "404" \
  "$(status "$GATEWAY/rest/v1/broker_credentials" -H "apikey: $ANON" -H "authorization: Bearer $DEMO")"

echo "==> web client boot"
# Every check above passes against a web app that renders on the server and then
# never hydrates — which is exactly what happened: the dashboard came back 200
# with the right HTML while no button worked and the portfolio panel spun
# forever. These two assert the dev-resource channel the client needs.
#
# Next 16 blocks cross-origin access to dev resources, and inside a container it
# does not recognise the host's 127.0.0.1 as its own origin unless
# allowedDevOrigins says so. When it blocks, the HMR handshake fails, the
# Turbopack client never boots, and nothing hydrates.
check "the HMR endpoint completes a websocket handshake" "101" \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
       -H 'Connection: Upgrade' -H 'Upgrade: websocket' -H 'Sec-WebSocket-Version: 13' \
       -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' "$WEB/_next/hmr?id=e2e")"
check "dev resources are not cross-origin blocked" "0" \
  "$(docker compose logs web --since 60s 2>/dev/null | grep -c 'Blocked cross-origin request')"

echo "==> services"
check "engine reports a reachable database" "up" \
  "$(curl -fsS "$ENGINE/ready" 2>/dev/null | sed -n 's/.*"database":"\([^"]*\)".*/\1/p')"
check "engine holds the scheduler lock" "true" \
  "$(curl -fsS "$ENGINE/ready" 2>/dev/null | sed -n 's/.*"leader":\([a-z]*\).*/\1/p')"
check "backend api answers an authenticated call" "200" \
  "$(status "$API/broker/connection" -H "authorization: Bearer $DEMO")"
check "backend api rejects an unauthenticated call" "401" "$(status "$API/broker/connection")"
check "web serves the login page" "200" "$(status "$WEB/login")"
check "web redirects an unauthenticated app route" "307" "$(status "$WEB/dashboard")"

printf '\n%s passed, %s failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
