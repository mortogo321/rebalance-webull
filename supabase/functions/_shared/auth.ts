/**
 * Authentication middleware.
 *
 * `verify_jwt` is off at the gateway (see supabase/config.toml) so this app can
 * return a structured JSON error and vary auth per route. That makes this file
 * the only thing standing between an anonymous request and user data, so it
 * validates the token against the auth server rather than merely decoding it —
 * a locally-decoded JWT proves nothing about revocation or signature.
 */

import type { Context, Next } from "hono";
import { ApiError, fail } from "./http.ts";
import { userClient } from "./supabase.ts";

export interface AuthedUser {
  id: string;
  email: string | null;
  token: string;
}

declare module "hono" {
  interface ContextVariableMap {
    user: AuthedUser;
  }
}

export async function requireUser(c: Context, next: Next) {
  const header = c.req.header("Authorization") ?? "";
  const token = header.toLowerCase().startsWith("bearer ")
    ? header.slice(7).trim()
    : "";

  if (!token) {
    return fail(c, new ApiError("unauthorized", "missing bearer token"));
  }

  const client = userClient(token);
  const { data, error } = await client.auth.getUser(token);

  if (error || !data.user) {
    return fail(c, new ApiError("unauthorized", "invalid or expired session"));
  }

  c.set("user", { id: data.user.id, email: data.user.email ?? null, token });
  await next();
}

/** The authenticated user. Only safe to call behind `requireUser`. */
export function currentUser(c: Context): AuthedUser {
  return c.get("user");
}
