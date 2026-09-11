/**
 * Client for the Python rebalance engine.
 *
 * The engine owns everything that touches a broker: decrypting credentials,
 * fetching quotes, placing orders. The edge function never does, which keeps
 * the decryption key and the venue connection in exactly one service.
 *
 * Internal calls carry a shared bearer token and a `user_id` in the body. The
 * engine trusts that `user_id` — it has no session of its own — so every call
 * site here MUST pass the id from `currentUser(c)`, never one read from a
 * request body.
 */

import { env } from "./env.ts";
import { ApiError } from "./http.ts";

async function call<T>(
  path: string,
  { method = "POST", body }: { method?: string; body?: unknown } = {},
): Promise<T> {
  const controller = new AbortController();
  // Without a deadline a hung engine holds the edge function open until the
  // platform kills it, and the user sees a spinner rather than an error.
  const timer = setTimeout(() => controller.abort(), env.engineTimeoutMs);

  try {
    const response = await fetch(`${env.engineUrl}${path}`, {
      method,
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.engineToken}`,
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });

    const text = await response.text();
    const payload = text ? JSON.parse(text) : {};

    if (!response.ok) {
      const detail = typeof payload?.detail === "string"
        ? payload.detail
        : `engine returned ${response.status}`;
      throw new ApiError(
        response.status === 404 ? "not_found" : "upstream_error",
        detail,
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError("upstream_error", "the rebalance engine did not respond in time");
    }
    throw new ApiError("upstream_error", "the rebalance engine is unreachable");
  } finally {
    clearTimeout(timer);
  }
}

export const engine = {
  storeCredentials: (userId: string, apiKey: string, apiSecret: string) =>
    call<{ status: string; error?: string }>("/internal/credentials", {
      body: { user_id: userId, api_key: apiKey, api_secret: apiSecret },
    }),

  removeCredentials: (userId: string) =>
    call<{ status: string }>("/internal/credentials", {
      method: "DELETE",
      body: { user_id: userId },
    }),

  verifyConnection: (userId: string) =>
    call<Record<string, unknown>>("/internal/connection/verify", {
      body: { user_id: userId },
    }),

  account: (userId: string) =>
    call<Record<string, unknown>>("/internal/account", { body: { user_id: userId } }),

  rebalance: (userId: string, botId: string, force = false) =>
    call<Record<string, unknown>>(`/internal/bots/${botId}/rebalance`, {
      body: { user_id: userId, force },
    }),
};
