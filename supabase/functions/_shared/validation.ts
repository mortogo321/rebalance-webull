/**
 * Request validation.
 *
 * Hand-written rather than schema-library based. The edge function is
 * cold-start sensitive and this is the only place it would need a validator,
 * so a dependency here buys expressiveness we do not use and costs startup time
 * plus a supply-chain surface on the one service that handles plaintext API
 * keys.
 *
 * Errors accumulate rather than short-circuit: a form with three bad fields
 * should report three problems, not one per round trip.
 */

export interface Issue {
  path: string;
  message: string;
}

export class ValidationResult<T> {
  constructor(
    readonly value: T | null,
    readonly issues: Issue[],
  ) {}

  get ok(): boolean {
    return this.issues.length === 0 && this.value !== null;
  }
}

class Check {
  readonly issues: Issue[] = [];

  constructor(private readonly input: Record<string, unknown>) {}

  private fail(path: string, message: string): void {
    this.issues.push({ path, message });
  }

  private raw(path: string): unknown {
    return this.input?.[path];
  }

  string(path: string, { min = 1, max = 255, pattern, upper = false }: {
    min?: number;
    max?: number;
    pattern?: RegExp;
    upper?: boolean;
  } = {}): string {
    const value = this.raw(path);
    if (typeof value !== "string") {
      this.fail(path, "must be text");
      return "";
    }
    let trimmed = value.trim();
    if (upper) trimmed = trimmed.toUpperCase();
    if (trimmed.length < min) this.fail(path, `must be at least ${min} character(s)`);
    if (trimmed.length > max) this.fail(path, `must be at most ${max} characters`);
    if (pattern && !pattern.test(trimmed)) this.fail(path, `is not in the expected format`);
    return trimmed;
  }

  number(path: string, { min, max, integer = false }: {
    min?: number;
    max?: number;
    integer?: boolean;
  } = {}): number {
    const value = this.raw(path);
    const n = typeof value === "number" ? value : Number(value);
    if (typeof value !== "number" || !Number.isFinite(n)) {
      this.fail(path, "must be a number");
      return 0;
    }
    if (integer && !Number.isInteger(n)) this.fail(path, "must be a whole number");
    if (min !== undefined && n < min) this.fail(path, `must be at least ${min}`);
    if (max !== undefined && n > max) this.fail(path, `must be at most ${max}`);
    return n;
  }

  /** A number that is allowed to be explicitly null (e.g. an unused trigger field). */
  nullableNumber(path: string, opts: { min?: number; max?: number; integer?: boolean } = {}):
    | number
    | null {
    const value = this.raw(path);
    if (value === null || value === undefined || value === "") return null;
    return this.number(path, opts);
  }

  boolean(path: string, fallback = false): boolean {
    const value = this.raw(path);
    if (value === undefined || value === null) return fallback;
    if (typeof value !== "boolean") {
      this.fail(path, "must be true or false");
      return fallback;
    }
    return value;
  }

  oneOf<T extends string>(path: string, allowed: readonly T[]): T {
    const value = this.raw(path);
    if (typeof value !== "string" || !allowed.includes(value as T)) {
      this.fail(path, `must be one of: ${allowed.join(", ")}`);
      return allowed[0];
    }
    return value as T;
  }

  has(path: string): boolean {
    return this.input !== null && typeof this.input === "object" && path in this.input;
  }

  add(path: string, message: string): void {
    this.fail(path, message);
  }
}

export const SYMBOL_PATTERN = /^[A-Z0-9.\-]{1,12}$/;
export const CURRENCY_PATTERN = /^[A-Z]{3}$/;

export interface Target {
  symbol: string;
  weight_bps: number;
}

function readTargets(input: unknown, check: Check): Target[] {
  if (!Array.isArray(input)) {
    check.add("targets", "must be a list of holdings");
    return [];
  }
  if (input.length === 0) check.add("targets", "add at least one holding");
  if (input.length > 50) check.add("targets", "at most 50 holdings");

  const targets: Target[] = [];
  const seen = new Set<string>();

  input.forEach((row, index) => {
    const item = (row ?? {}) as Record<string, unknown>;
    const symbol = String(item.symbol ?? "").trim().toUpperCase();
    const weight = Number(item.weight_bps);

    if (!SYMBOL_PATTERN.test(symbol)) {
      check.add(`targets[${index}].symbol`, "1-12 characters of A-Z, 0-9, . or -");
    }
    if (!Number.isInteger(weight) || weight < 0 || weight > 10000) {
      check.add(`targets[${index}].weight_bps`, "must be a whole number of basis points, 0-10000");
    }
    if (seen.has(symbol)) check.add(`targets[${index}].symbol`, `duplicate symbol ${symbol}`);
    seen.add(symbol);

    targets.push({ symbol, weight_bps: weight });
  });

  const total = targets.reduce((sum, t) => sum + (Number.isFinite(t.weight_bps) ? t.weight_bps : 0), 0);
  if (targets.length > 0 && total !== 10000) {
    check.add(
      "targets",
      `weights must total 100% (10000 bps), got ${(total / 100).toFixed(2)}%`,
    );
  }
  return targets;
}

export const TRIGGER_TYPES = ["schedule", "drift", "schedule_or_drift"] as const;
export type TriggerType = (typeof TRIGGER_TYPES)[number];

export interface BotInput {
  name: string;
  base_currency: string;
  investment_amount: number;
  trigger_type: TriggerType;
  interval_minutes: number | null;
  drift_threshold_bps: number | null;
  min_order_value: number;
  cash_buffer_bps: number;
  allow_fractional: boolean;
}

function readBotCore(check: Check, input: Record<string, unknown>): BotInput {
  const bot: BotInput = {
    name: check.string("name", { min: 1, max: 60 }),
    base_currency: check.has("base_currency")
      ? check.string("base_currency", { min: 3, max: 3, pattern: CURRENCY_PATTERN, upper: true })
      : "USD",
    investment_amount: check.number("investment_amount", { min: 0.01, max: 1_000_000_000 }),
    trigger_type: check.oneOf("trigger_type", TRIGGER_TYPES),
    interval_minutes: check.nullableNumber("interval_minutes", { min: 1, max: 525600, integer: true }),
    drift_threshold_bps: check.nullableNumber("drift_threshold_bps", { min: 1, max: 10000, integer: true }),
    min_order_value: check.has("min_order_value")
      ? check.number("min_order_value", { min: 0, max: 1_000_000 })
      : 10,
    cash_buffer_bps: check.has("cash_buffer_bps")
      ? check.number("cash_buffer_bps", { min: 0, max: 10000, integer: true })
      : 0,
    allow_fractional: check.boolean("allow_fractional", false),
  };

  // A schedule bot without an interval, or a drift bot without a threshold, can
  // never fire. Reject at creation rather than letting it idle forever looking
  // like it works.
  if (bot.trigger_type !== "drift" && bot.interval_minutes === null) {
    check.add("interval_minutes", "a schedule-driven bot needs an interval");
  }
  if (bot.trigger_type !== "schedule" && bot.drift_threshold_bps === null) {
    check.add("drift_threshold_bps", "a drift-driven bot needs a threshold");
  }
  void input;
  return bot;
}

export function parseCreateBot(
  body: unknown,
): ValidationResult<BotInput & { targets: Target[] }> {
  const input = (body ?? {}) as Record<string, unknown>;
  const check = new Check(input);
  const bot = readBotCore(check, input);
  const targets = readTargets(input.targets, check);
  return new ValidationResult(
    check.issues.length ? null : { ...bot, targets },
    check.issues,
  );
}

export function parseUpdateBot(
  body: unknown,
): ValidationResult<Partial<BotInput> & { targets?: Target[] }> {
  const input = (body ?? {}) as Record<string, unknown>;
  const check = new Check(input);
  const patch: Partial<BotInput> & { targets?: Target[] } = {};

  if (check.has("name")) patch.name = check.string("name", { min: 1, max: 60 });
  if (check.has("base_currency")) {
    patch.base_currency = check.string("base_currency", {
      min: 3, max: 3, pattern: CURRENCY_PATTERN, upper: true,
    });
  }
  if (check.has("investment_amount")) {
    patch.investment_amount = check.number("investment_amount", { min: 0.01, max: 1_000_000_000 });
  }
  if (check.has("trigger_type")) patch.trigger_type = check.oneOf("trigger_type", TRIGGER_TYPES);
  if (check.has("interval_minutes")) {
    patch.interval_minutes = check.nullableNumber("interval_minutes", {
      min: 1, max: 525600, integer: true,
    });
  }
  if (check.has("drift_threshold_bps")) {
    patch.drift_threshold_bps = check.nullableNumber("drift_threshold_bps", {
      min: 1, max: 10000, integer: true,
    });
  }
  if (check.has("min_order_value")) {
    patch.min_order_value = check.number("min_order_value", { min: 0, max: 1_000_000 });
  }
  if (check.has("cash_buffer_bps")) {
    patch.cash_buffer_bps = check.number("cash_buffer_bps", { min: 0, max: 10000, integer: true });
  }
  if (check.has("allow_fractional")) patch.allow_fractional = check.boolean("allow_fractional");
  if (check.has("targets")) patch.targets = readTargets(input.targets, check);

  return new ValidationResult(check.issues.length ? null : patch, check.issues);
}

export function parseCredentials(
  body: unknown,
): ValidationResult<{ api_key: string; api_secret: string }> {
  const check = new Check((body ?? {}) as Record<string, unknown>);
  // Bounded so a caller cannot push megabytes through the encryption path.
  const api_key = check.string("api_key", { min: 4, max: 512 });
  const api_secret = check.string("api_secret", { min: 4, max: 512 });
  return new ValidationResult(check.issues.length ? null : { api_key, api_secret }, check.issues);
}

export function parseStatusAction(
  body: unknown,
): ValidationResult<{ action: "start" | "pause" | "stop" }> {
  const check = new Check((body ?? {}) as Record<string, unknown>);
  const action = check.oneOf("action", ["start", "pause", "stop"] as const);
  return new ValidationResult(check.issues.length ? null : { action }, check.issues);
}
