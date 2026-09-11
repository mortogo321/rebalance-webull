/**
 * Display formatting.
 *
 * Everything arrives from the API as a string, because the values are Postgres
 * `numeric` and JSON numbers are IEEE doubles -- parsing a share quantity into a
 * float to render it is how a UI starts disagreeing with the ledger. Parsing
 * happens here, at the last possible moment, for display only.
 */

export function money(value: string | number | null | undefined, currency = "USD") {
  const n = Number(value ?? 0);
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(n) ? n : 0);
}

export function quantity(value: string | number | null | undefined) {
  const n = Number(value ?? 0);
  // Trailing zeros stripped: "12" reads better than "12.00000000", but a
  // genuine fraction still shows every place that matters.
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(n);
}

/** Basis points as a percentage: 4000 -> "40.00%". */
export function bps(value: number | null | undefined, fractionDigits = 2) {
  return `${((value ?? 0) / 100).toFixed(fractionDigits)}%`;
}

/** Signed basis points, for drift: -742 -> "-7.42%". */
export function signedBps(value: number | null | undefined) {
  const n = value ?? 0;
  return `${n > 0 ? "+" : ""}${(n / 100).toFixed(2)}%`;
}

export function dateTime(value: string | null | undefined) {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function relative(value: string | null | undefined) {
  if (!value) return "never";
  const diff = Date.now() - new Date(value).getTime();
  const minutes = Math.round(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}
