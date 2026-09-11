"use client";

import { createBrowserClient } from "@supabase/ssr";
import { AUTH_COOKIE_NAME } from "@/lib/supabase/cookie";

/**
 * Browser Supabase client.
 *
 * Only ever holds the publishable key. Every row it can reach is bounded by RLS
 * on the server side, which is why shipping this key to the browser is safe and
 * shipping the secret key would not be.
 */
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    { cookieOptions: { name: AUTH_COOKIE_NAME } },
  );
}
