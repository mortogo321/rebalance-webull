/**
 * Bot routes.
 *
 * Reads go through `userClient` so RLS answers "whose bot is this?" for us.
 * Writes go through `adminClient` into SECURITY DEFINER functions that take the
 * user id explicitly and re-check ownership — because the invariants involved
 * (weights totalling 100%, "don't re-specify a running bot") span rows and
 * cannot be expressed as a row-level policy.
 */

import { Hono } from "hono";
import { currentUser } from "../../_shared/auth.ts";
import { engine } from "../../_shared/engine.ts";
import { ApiError, ok } from "../../_shared/http.ts";
import { adminClient, userClient } from "../../_shared/supabase.ts";
import { parseCreateBot, parseStatusAction, parseUpdateBot } from "../../_shared/validation.ts";

export const bots = new Hono();

/** Map a Postgres error onto the API's error contract. */
function fromPostgres(error: { message: string; code?: string }): ApiError {
  const message = error.message ?? "database error";
  if (message.includes("bot not found")) return new ApiError("not_found", "bot not found");
  if (message.includes("pause the bot")) {
    return new ApiError("conflict", "pause the bot before changing its configuration");
  }
  if (message.includes("target weights must total")) {
    return new ApiError("invalid_request", message);
  }
  if (message.includes("add target holdings")) return new ApiError("invalid_request", message);
  if (error.code === "23505") return new ApiError("conflict", "a bot with that name already exists");
  // Anything unrecognised is reported generically: raw Postgres text can carry
  // schema details that do not belong in an HTTP response.
  return new ApiError("internal_error", "could not complete the request");
}

bots.get("/", async (c) => {
  const user = currentUser(c);
  const { data, error } = await userClient(user.token)
    .from("bot_overview")
    .select("*")
    .order("name");

  if (error) throw new ApiError("internal_error", "could not list bots");
  return ok(c, { bots: data ?? [] });
});

bots.get("/:id", async (c) => {
  const user = currentUser(c);
  const client = userClient(user.token);
  const id = c.req.param("id");

  const { data: bot } = await client.from("bots").select("*").eq("id", id).maybeSingle();
  if (!bot) throw new ApiError("not_found", "bot not found");

  const [{ data: targets }, { data: runs }] = await Promise.all([
    client.from("bot_targets").select("symbol, target_weight_bps").eq("bot_id", id).order("symbol"),
    client
      .from("rebalance_runs")
      .select("id, status, trigger_reason, portfolio_value, max_drift_bps, planned_order_count, submitted_order_count, error, started_at, finished_at")
      .eq("bot_id", id)
      .order("started_at", { ascending: false })
      .limit(20),
  ]);

  return ok(c, { bot, targets: targets ?? [], runs: runs ?? [] });
});

bots.post("/", async (c) => {
  const user = currentUser(c);
  const parsed = parseCreateBot(await c.req.json().catch(() => ({})));
  if (!parsed.ok || !parsed.value) {
    throw new ApiError("invalid_request", "invalid bot configuration", parsed.issues);
  }
  const { targets, ...bot } = parsed.value;

  const { data, error } = await adminClient().rpc("create_bot", {
    p_user_id: user.id,
    p_bot: bot,
    p_targets: targets,
  });
  if (error) throw fromPostgres(error);

  return ok(c, { id: data }, 201);
});

bots.patch("/:id", async (c) => {
  const user = currentUser(c);
  const parsed = parseUpdateBot(await c.req.json().catch(() => ({})));
  if (!parsed.ok || !parsed.value) {
    throw new ApiError("invalid_request", "invalid bot configuration", parsed.issues);
  }
  const { targets, ...patch } = parsed.value;

  const { error } = await adminClient().rpc("update_bot", {
    p_user_id: user.id,
    p_bot_id: c.req.param("id"),
    p_patch: patch,
    p_targets: targets ?? null,
  });
  if (error) throw fromPostgres(error);

  return ok(c, { status: "updated" });
});

bots.delete("/:id", async (c) => {
  const user = currentUser(c);
  const { error } = await adminClient().rpc("delete_bot", {
    p_user_id: user.id,
    p_bot_id: c.req.param("id"),
  });
  if (error) throw fromPostgres(error);
  return ok(c, { status: "deleted" });
});

/** start / pause / stop */
bots.post("/:id/status", async (c) => {
  const user = currentUser(c);
  const parsed = parseStatusAction(await c.req.json().catch(() => ({})));
  if (!parsed.ok || !parsed.value) {
    throw new ApiError("invalid_request", "action must be start, pause or stop");
  }
  const status = { start: "running", pause: "paused", stop: "stopped" }[parsed.value.action];

  const { data, error } = await adminClient().rpc("set_bot_status", {
    p_user_id: user.id,
    p_bot_id: c.req.param("id"),
    p_status: status,
  });
  if (error) throw fromPostgres(error);

  return ok(c, { bot: data });
});

/** Run a rebalance immediately, bypassing the trigger but not the planner. */
bots.post("/:id/rebalance", async (c) => {
  const user = currentUser(c);
  return ok(c, await engine.rebalance(user.id, c.req.param("id"), true));
});

/** Order history. Paginated because a busy bot accumulates fills quickly. */
bots.get("/:id/orders", async (c) => {
  const user = currentUser(c);
  const limit = Math.min(Number(c.req.query("limit") ?? 100), 200);
  const { data, error } = await userClient(user.token)
    .from("orders")
    .select("id, run_id, symbol, side, quantity, filled_quantity, avg_fill_price, requested_value, status, reject_reason, created_at")
    .eq("bot_id", c.req.param("id"))
    .order("created_at", { ascending: false })
    .limit(limit);

  if (error) throw new ApiError("internal_error", "could not list orders");
  return ok(c, { orders: data ?? [] });
});

/** The latest run's full plan — what the allocation view renders. */
bots.get("/:id/allocation", async (c) => {
  const user = currentUser(c);
  const client = userClient(user.token);
  const id = c.req.param("id");

  const [{ data: targets }, { data: run }] = await Promise.all([
    client.from("bot_targets").select("symbol, target_weight_bps").eq("bot_id", id).order("symbol"),
    client
      .from("rebalance_runs")
      .select("id, status, plan, max_drift_bps, portfolio_value, started_at")
      .eq("bot_id", id)
      .order("started_at", { ascending: false })
      .limit(1)
      .maybeSingle(),
  ]);

  return ok(c, { targets: targets ?? [], latest_run: run ?? null });
});
