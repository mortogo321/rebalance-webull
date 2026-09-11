import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { bps, money, quantity, signedBps } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Target {
  symbol: string;
  target_weight_bps: number;
}

interface PlanDrift {
  symbol: string;
  price: string;
  current_quantity: string;
  current_value: string;
  target_value: string;
  current_weight_bps: number;
  target_weight_bps: number;
  drift_bps: number;
}

/**
 * Current allocation against target.
 *
 * Reads from the stored plan of the most recent run rather than recomputing:
 * the plan is the exact snapshot the bot acted on, including the prices it
 * used. Recomputing here with fresh prices would show a picture that never
 * existed, and quietly disagree with the orders listed below it.
 */
export function AllocationTable({
  targets,
  plan,
  currency,
}: {
  targets: Target[];
  plan: Record<string, unknown> | null;
  currency: string;
}) {
  const drifts = (plan?.drifts as PlanDrift[] | undefined) ?? [];
  const skipped =
    (plan?.skipped as { symbol: string; side: string; reason: string }[] | undefined) ?? [];

  // No run yet: show the intended basket so the page is still useful.
  const rows: PlanDrift[] = drifts.length
    ? drifts
    : targets.map((t) => ({
        symbol: t.symbol,
        price: "0",
        current_quantity: "0",
        current_value: "0",
        target_value: "0",
        current_weight_bps: 0,
        target_weight_bps: t.target_weight_bps,
        drift_bps: -t.target_weight_bps,
      }));

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>Allocation</CardTitle>
        {plan ? (
          <span className="text-xs text-muted tabular">
            portfolio {money(plan.portfolio_value as string, currency)} &middot; deployable{" "}
            {money(plan.deployable_value as string, currency)}
          </span>
        ) : (
          <span className="text-xs text-muted">target basket — no run yet</span>
        )}
      </CardHeader>

      <CardBody className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="border-b border-border text-left text-xs text-muted">
              <tr>
                <th className="px-5 py-2.5 font-medium">Symbol</th>
                <th className="px-5 py-2.5 text-right font-medium">Qty</th>
                <th className="px-5 py-2.5 text-right font-medium">Value</th>
                <th className="px-5 py-2.5 text-right font-medium">Current</th>
                <th className="px-5 py-2.5 text-right font-medium">Target</th>
                <th className="px-5 py-2.5 text-right font-medium">Drift</th>
                <th className="w-32 px-5 py-2.5 font-medium">&nbsp;</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const over = row.drift_bps > 0;
                return (
                  <tr key={row.symbol} className="border-b border-border last:border-0">
                    <td className="px-5 py-3 font-medium">
                      {row.symbol}
                      {row.target_weight_bps === 0 ? (
                        <span className="ml-2 text-xs text-negative">liquidating</span>
                      ) : null}
                    </td>
                    <td className="px-5 py-3 text-right tabular">
                      {quantity(row.current_quantity)}
                    </td>
                    <td className="px-5 py-3 text-right tabular">
                      {money(row.current_value, currency)}
                    </td>
                    <td className="px-5 py-3 text-right tabular">{bps(row.current_weight_bps)}</td>
                    <td className="px-5 py-3 text-right text-muted tabular">
                      {bps(row.target_weight_bps)}
                    </td>
                    <td
                      className={cn(
                        "px-5 py-3 text-right tabular",
                        Math.abs(row.drift_bps) < 50
                          ? "text-muted"
                          : over
                            ? "text-negative"
                            : "text-accent",
                      )}
                    >
                      {signedBps(row.drift_bps)}
                    </td>
                    <td className="px-5 py-3">
                      {/* Target is the baseline; the bar shows how far current
                          sits above or below it, so over- and underweight are
                          distinguishable at a glance. */}
                      <div className="relative h-1.5 w-full rounded-full bg-border">
                        <div
                          className={cn(
                            "absolute top-0 h-1.5 rounded-full",
                            over ? "bg-negative" : "bg-accent",
                          )}
                          style={{
                            left: `${Math.min(row.current_weight_bps, row.target_weight_bps) / 100}%`,
                            width: `${Math.max(Math.abs(row.drift_bps) / 100, 0.5)}%`,
                          }}
                        />
                        <div
                          className="absolute top-[-2px] h-2.5 w-0.5 bg-ink"
                          style={{ left: `${row.target_weight_bps / 100}%` }}
                          title={`target ${bps(row.target_weight_bps)}`}
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {skipped.length ? (
          <div className="border-t border-border px-5 py-3">
            <p className="text-xs font-medium text-muted">Orders the last run declined to place</p>
            <ul className="mt-1.5 space-y-1">
              {skipped.map((s) => (
                <li key={`${s.symbol}-${s.side}`} className="text-xs text-muted">
                  <span className="font-medium text-ink">
                    {s.side} {s.symbol}
                  </span>{" "}
                  — {s.reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </CardBody>
    </Card>
  );
}
