/**
 * Supabase clients.
 *
 * Two of them, and the distinction is the whole security model:
 *
 *   userClient(jwt)  acts AS the signed-in user. RLS applies. Used for every
 *                    read, so a query for someone else's bot returns nothing
 *                    even if a handler forgets to filter.
 *
 *   adminClient()    acts as the service role. RLS does NOT apply. Used only
 *                    for writes that must enforce invariants a policy cannot
 *                    express, and every such query filters on user_id by hand.
 *
 * A handler that reaches for adminClient() on a read path is a bug.
 */

import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { env } from "./env.ts";

export function userClient(accessToken: string): SupabaseClient {
  // The publishable key, NOT the secret key. Pairing the secret key with a user
  // JWT leaves the effective role dependent on how PostgREST resolves the two;
  // the publishable key can only ever reach `authenticated`, so RLS is the
  // floor rather than a behaviour we are relying on.
  return createClient(env.supabaseUrl, env.supabasePublishableKey, {
    global: { headers: { Authorization: `Bearer ${accessToken}` } },
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

let admin: SupabaseClient | null = null;

export function adminClient(): SupabaseClient {
  admin ??= createClient(env.supabaseUrl, env.supabaseSecretKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
  return admin;
}
