/**
 * Client for the backend API (the Supabase Edge Function).
 *
 * Every call carries the caller's Supabase access token. The browser never
 * talks to the engine or to Postgres directly for writes -- the edge function
 * is the only write path, because it is where validation and the service-role
 * privilege live.
 */

import { createClient } from "@/lib/supabase/client";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL!;

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(
  path: string,
  { method = "GET", body }: { method?: string; body?: unknown } = {},
): Promise<T> {
  const supabase = createClient();
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;

  if (!token) throw new ApiError("unauthorized", "your session has expired, please sign in again");

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  const text = await response.text();
  const payload = text ? JSON.parse(text) : {};

  if (!response.ok) {
    const error = payload?.error ?? {};
    throw new ApiError(
      error.code ?? "internal_error",
      error.message ?? `request failed with ${response.status}`,
      error.details,
    );
  }
  return payload as T;
}

export interface BrokerConnection {
  status: "disconnected" | "connected" | "error";
  environment: string | null;
  account_id: string | null;
  account_currency: string | null;
  api_key_hint: string | null;
  last_verified_at: string | null;
  last_error: string | null;
}

export interface AccountSnapshot {
  account_id: string;
  currency: string;
  cash: string;
  equity: string;
  total_value: string;
  broker: string;
  positions: {
    symbol: string;
    quantity: string;
    avg_cost: string;
    price: string;
    market_value: string;
  }[];
}

export const api = {
  connection: () => request<BrokerConnection>("/broker/connection"),
  saveCredentials: (api_key: string, api_secret: string) =>
    request<{ status: string; error?: string }>("/broker/credentials", {
      method: "POST",
      body: { api_key, api_secret },
    }),
  disconnect: () => request<{ status: string }>("/broker/credentials", { method: "DELETE" }),
  verify: () => request<{ status: string; error?: string }>("/broker/verify", { method: "POST" }),
  account: () => request<AccountSnapshot>("/broker/account"),

  listBots: () => request<{ bots: Record<string, unknown>[] }>("/bots"),
  getBot: (id: string) => request<Record<string, unknown>>(`/bots/${id}`),
  createBot: (body: unknown) => request<{ id: string }>("/bots", { method: "POST", body }),
  updateBot: (id: string, body: unknown) =>
    request<{ status: string }>(`/bots/${id}`, { method: "PATCH", body }),
  deleteBot: (id: string) => request<{ status: string }>(`/bots/${id}`, { method: "DELETE" }),
  setStatus: (id: string, action: "start" | "pause" | "stop") =>
    request<{ bot: Record<string, unknown> }>(`/bots/${id}/status`, {
      method: "POST",
      body: { action },
    }),
  rebalanceNow: (id: string) =>
    request<Record<string, unknown>>(`/bots/${id}/rebalance`, { method: "POST" }),
  orders: (id: string) => request<{ orders: Record<string, unknown>[] }>(`/bots/${id}/orders`),
  allocation: (id: string) =>
    request<{ targets: Record<string, unknown>[]; latest_run: Record<string, unknown> | null }>(
      `/bots/${id}/allocation`,
    ),
};
