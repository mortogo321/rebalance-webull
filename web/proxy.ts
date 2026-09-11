/**
 * Next.js 16 renamed Middleware to Proxy. This file is the same convention as
 * the old middleware.ts and must live at the project root.
 */
import type { NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase/proxy";

export async function proxy(request: NextRequest) {
  return await updateSession(request);
}

export const config = {
  // Skip everything under `_next`, not just static and image.
  //
  // `_next/hmr` is a WebSocket upgrade. Matching it means this proxy answers
  // the upgrade with an ordinary HTTP response, the handshake fails with
  // ERR_INVALID_HTTP_RESPONSE, and under Turbopack the client chunks that
  // arrive over that channel never execute — so the page renders from the
  // server, never hydrates, and every client component sits at its initial
  // state forever. The Portfolio panel spinning on "Loading…" was this.
  //
  // Nothing under `_next` needs an auth check: RSC requests for app routes are
  // made against the route's own path with `?_rsc=`, so they still match below
  // and are still protected.
  matcher: ["/((?!_next/|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico)$).*)"],
};
