import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { AUTH_COOKIE_NAME } from "@/lib/supabase/cookie";

/**
 * Server-side Supabase base URL.
 *
 * NEXT_PUBLIC_SUPABASE_URL is the browser's view of the gateway — it has to be
 * host-addressable (127.0.0.1:54321), because the browser resolves it. Inside
 * the container that address is the container itself, so server components,
 * route handlers and the proxy must use the compose service name instead or
 * every call fails with a connection error that surfaces as "those credentials
 * did not work".
 *
 * Falls back to the public URL for `bun run dev` on the host, where the two are
 * the same thing.
 */
const SUPABASE_URL = process.env.SUPABASE_INTERNAL_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL!;

/**
 * Server Supabase client, for server components and route handlers.
 *
 * Reads the session from cookies so server-rendered pages see the same user the
 * browser does. Writes are wrapped in try/catch because a server component may
 * not set cookies -- the proxy already refreshed the session by the time a page
 * renders, so a failure to write here is expected and harmless.
 */
export async function createClient() {
  const cookieStore = await cookies();

  return createServerClient(SUPABASE_URL, process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!, {
    cookieOptions: { name: AUTH_COOKIE_NAME },
    cookies: {
      getAll() {
        return cookieStore.getAll();
      },
      setAll(cookiesToSet) {
        try {
          for (const { name, value, options } of cookiesToSet) {
            cookieStore.set(name, value, options);
          }
        } catch {
          // Called from a server component, where cookies are read-only.
        }
      },
    },
  });
}
