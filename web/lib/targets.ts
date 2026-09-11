/**
 * Target-basket validation.
 *
 * Weights are entered as decimal percentages ("33.33") but validated as
 * integer basis points, so three equal holdings can total exactly 10000
 * instead of a float that never quite reaches 100.
 */

export interface TargetInput {
  symbol: string;
  percent: string;
}

export interface TargetValidation {
  valid: boolean;
  /** Sum of every row's basis points, even when the basket is invalid. */
  totalBps: number;
  /** Why the basket is invalid, or null when it is valid. */
  error: string | null;
}

/** Round a decimal percent ("33.33") to integer basis points (3333). */
export function percentToBps(percent: string): number {
  return Math.round(Number(percent || 0) * 100);
}

/** Sum of every row's basis points, before any validation. */
export function totalBps(targets: TargetInput[]): number {
  return targets.reduce((sum, t) => sum + percentToBps(t.percent), 0);
}

/**
 * A target basket is valid when it holds at least one row, every symbol is
 * unique (case-insensitive, trimmed), and the weights total exactly 10000
 * basis points (100%).
 */
export function validateTargets(targets: TargetInput[]): TargetValidation {
  const sum = totalBps(targets);

  if (targets.length === 0) {
    return { valid: false, totalBps: sum, error: "Add at least one holding." };
  }

  const symbols = targets.map((t) => t.symbol.trim().toUpperCase());
  if (new Set(symbols).size !== symbols.length) {
    return {
      valid: false,
      totalBps: sum,
      error: "Each symbol can only appear once.",
    };
  }

  if (sum !== 10000) {
    return {
      valid: false,
      totalBps: sum,
      error: "Weights must total exactly 100%.",
    };
  }

  return { valid: true, totalBps: sum, error: null };
}
