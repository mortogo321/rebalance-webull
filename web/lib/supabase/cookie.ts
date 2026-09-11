/**
 * The auth cookie's name, pinned rather than derived.
 *
 * `@supabase/ssr` defaults to `sb-<host>-auth-token`, taking the host from the
 * URL each client was constructed with. In this stack those URLs differ by
 * design: the browser must reach the gateway at 127.0.0.1:54321, while server
 * components and the proxy run inside a container where that address is the
 * container itself and must use the compose service name. Left to the default,
 * the server would write `sb-gateway-auth-token` and the browser would look for
 * `sb-127.0.0.1-auth-token` — so a sign-in would appear to work, redirect, and
 * then every client-side call would report no session.
 *
 * One explicit name removes the coupling between where a client points and
 * where its session is stored.
 */
export const AUTH_COOKIE_NAME = "sb-rebalance-auth-token";
