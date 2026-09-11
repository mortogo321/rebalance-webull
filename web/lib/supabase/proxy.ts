import { createServerClient } from "@supabase/ssr";
import { type NextRequest, NextResponse } from "next/server";
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
 * Session refresh and route protection, run on every request.
 *
 * Two jobs:
 *   1. Refresh the auth token and write the rotated cookies onto the response.
 *      Without this, a user is silently logged out when their token expires.
 *   2. Redirect unauthenticated requests away from application routes.
 *
 * The redirect here is a usability guard, not the security boundary -- data is
 * protected by RLS in the database. A bypass of this function leaks nothing; it
 * just renders an empty page.
 */
const PUBLIC_PATHS = ["/login", "/auth"];

export async function updateSession(request: NextRequest) {
  let response = NextResponse.next({ request });

  const supabase = createServerClient(
    SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY!,
    {
      cookieOptions: { name: AUTH_COOKIE_NAME },
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          for (const { name, value } of cookiesToSet) {
            request.cookies.set(name, value);
          }
          response = NextResponse.next({ request });
          for (const { name, value, options } of cookiesToSet) {
            response.cookies.set(name, value, options);
          }
        },
      },
    },
  );

  // getClaims() validates the token rather than trusting the cookie's contents.
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims);

  const { pathname } = request.nextUrl;
  const isPublic = PUBLIC_PATHS.some((p) => pathname.startsWith(p));

  if (!signedIn && !isPublic) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    // Come back to the page they actually asked for after signing in.
    url.searchParams.set("next", pathname);
    return NextResponse.redirect(url);
  }

  if (signedIn && pathname === "/login") {
    const url = request.nextUrl.clone();
    url.pathname = "/dashboard";
    url.search = "";
    return NextResponse.redirect(url);
  }

  return response;
}
