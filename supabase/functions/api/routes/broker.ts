/**
 * Broker connection routes.
 *
 * The one rule this file exists to enforce: an API key or secret travels in
 * exactly one direction. It arrives in a POST body, goes straight to the engine
 * to be encrypted, and is never selected, logged, or returned. The only thing
 * the browser ever learns is a 4-character hint and a connection status.
 */

import { Hono } from "hono";
import { currentUser } from "../../_shared/auth.ts";
import { engine } from "../../_shared/engine.ts";
import { ApiError, ok } from "../../_shared/http.ts";
import { userClient } from "../../_shared/supabase.ts";
import { parseCredentials } from "../../_shared/validation.ts";

export const broker = new Hono();

/** Connection status. Reads through RLS, so it can only ever be the caller's. */
broker.get("/connection", async (c) => {
  const user = currentUser(c);
  const { data, error } = await userClient(user.token)
    .from("broker_connections")
    .select("status, environment, account_id, account_currency, api_key_hint, last_verified_at, last_error")
    .maybeSingle();

  if (error) throw new ApiError("internal_error", "could not read the connection");

  return ok(c, data ?? {
    status: "disconnected",
    environment: null,
    account_id: null,
    account_currency: null,
    api_key_hint: null,
    last_verified_at: null,
    last_error: null,
  });
});

/** Attach or replace credentials. Doubles as the "rotate key" path. */
broker.post("/credentials", async (c) => {
  const user = currentUser(c);
  const parsed = parseCredentials(await c.req.json().catch(() => ({})));
  if (!parsed.ok || !parsed.value) {
    throw new ApiError("invalid_request", "invalid credentials", parsed.issues);
  }

  const result = await engine.storeCredentials(
    user.id,
    parsed.value.api_key,
    parsed.value.api_secret,
  );

  // A rejected key is the user's problem to fix, not a server fault: report it
  // as a normal result with an error field rather than a 5xx.
  return ok(c, result);
});

broker.delete("/credentials", async (c) => {
  const user = currentUser(c);
  return ok(c, await engine.removeCredentials(user.id));
});

/** Re-probe the venue and refresh the stored connection state. */
broker.post("/verify", async (c) => {
  const user = currentUser(c);
  return ok(c, await engine.verifyConnection(user.id));
});

/** Cash, positions and live quotes, straight from the broker. */
broker.get("/account", async (c) => {
  const user = currentUser(c);
  return ok(c, await engine.account(user.id));
});
