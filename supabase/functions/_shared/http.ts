/**
 * Response helpers and the error contract.
 *
 * One shape for every failure — `{ error: { code, message, details? } }` — so
 * the web client has a single branch to handle, and so no handler accidentally
 * returns a raw exception message containing a connection string or a key.
 */

import type { Context } from "hono";

export type ErrorCode =
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "invalid_request"
  | "conflict"
  | "broker_error"
  | "upstream_error"
  | "internal_error";

const STATUS: Record<ErrorCode, number> = {
  unauthorized: 401,
  forbidden: 403,
  not_found: 404,
  invalid_request: 422,
  conflict: 409,
  broker_error: 502,
  upstream_error: 502,
  internal_error: 500,
};

export class ApiError extends Error {
  constructor(
    readonly code: ErrorCode,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export function fail(c: Context, error: ApiError) {
  return c.json(
    { error: { code: error.code, message: error.message, details: error.details } },
    STATUS[error.code],
  );
}

export function ok<T>(c: Context, data: T, status = 200) {
  return c.json(data as Record<string, unknown>, status);
}
