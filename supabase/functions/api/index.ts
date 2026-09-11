/**
 * Rebalance Webull — backend API.
 *
 * A single Hono app served as one Supabase Edge Function. One function rather
 * than one per endpoint because they share auth, error handling and the engine
 * client; splitting them would duplicate all three and multiply cold starts.
 *
 * Mounted at /functions/v1/api, so every route below is reached as
 * `<supabase-url>/functions/v1/api/<path>`.
 *
 * The same file runs in two places unchanged:
 *   - local + production: `supabase functions serve` / `supabase functions deploy`
 *   - UAT on EC2:         the Deno container in ../Dockerfile
 *
 * UAT deliberately runs the container rather than a hosted edge function. A
 * hosted function deploys on Supabase's schedule, so it cannot take part in the
 * single nginx upstream switch that moves web, api and engine together -- a
 * rollback would leave the API ahead of the other two. It would also have to
 * reach the engine over the public internet, which means exposing /internal/*,
 * the one thing this design refuses to do.
 */

import { Hono } from "hono";
import { cors } from "hono/cors";
import { logger } from "hono/logger";
import { requireUser } from "../_shared/auth.ts";
import { env } from "../_shared/env.ts";
import { ApiError, fail } from "../_shared/http.ts";
import { bots } from "./routes/bots.ts";
import { broker } from "./routes/broker.ts";

const app = new Hono().basePath("/api");

app.use("*", logger());

// The browser sends the user's JWT, so credentials must be allowed and the
// origin cannot be "*". Allowed origins come from configuration rather than
// being reflected back from the request.
const allowedOrigins = (Deno.env.get("CORS_ALLOWED_ORIGINS") ?? "http://127.0.0.1:3000,http://localhost:3000")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);

app.use(
  "*",
  cors({
    origin: (origin) => (allowedOrigins.includes(origin) ? origin : allowedOrigins[0]),
    allowHeaders: ["authorization", "content-type", "apikey"],
    allowMethods: ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    credentials: true,
    maxAge: 3600,
  }),
);

// ---------------------------------------------------------------------------
// public
// ---------------------------------------------------------------------------
app.get("/health", (c) => c.json({ status: "ok", env: env.appEnv }));

// ---------------------------------------------------------------------------
// authenticated
// ---------------------------------------------------------------------------
app.use("/broker/*", requireUser);
app.use("/bots", requireUser);
app.use("/bots/*", requireUser);
app.use("/me", requireUser);

app.get("/me", (c) => {
  const user = c.get("user");
  return c.json({ id: user.id, email: user.email });
});

app.route("/broker", broker);
app.route("/bots", bots);

// ---------------------------------------------------------------------------
// errors
//
// One handler so no route can leak a stack trace or a driver message. Anything
// that is not a deliberate ApiError becomes a generic 500, and the detail goes
// to the logs instead of to the client.
// ---------------------------------------------------------------------------
app.notFound((c) => fail(c, new ApiError("not_found", `no route for ${c.req.path}`)));

app.onError((error, c) => {
  if (error instanceof ApiError) return fail(c, error);
  console.error("unhandled error", { path: c.req.path, error: String(error) });
  return fail(c, new ApiError("internal_error", "something went wrong"));
});

Deno.serve(app.fetch);
