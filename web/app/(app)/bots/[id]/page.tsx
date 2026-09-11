import Link from "next/link";
import { notFound } from "next/navigation";
import { AllocationTable } from "@/components/allocation-table";
import { BotControls } from "@/components/bot-controls";
import { Badge, statusTone } from "@/components/ui/badge";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { bps, dateTime, money, quantity } from "@/lib/format";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function BotDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const supabase = await createClient();

  // RLS makes "someone else's bot" and "no such bot" the same result, which is
  // exactly the behaviour we want: a 404 either way, leaking nothing.
  const { data: bot } = await supabase.from("bots").select("*").eq("id", id).maybeSingle();
  if (!bot) notFound();

  const [{ data: targets }, { data: runs }, { data: orders }] = await Promise.all([
    supabase
      .from("bot_targets")
      .select("symbol, target_weight_bps")
      .eq("bot_id", id)
      .order("symbol"),
    supabase
      .from("rebalance_runs")
      .select(
        "id, status, trigger_reason, portfolio_value, max_drift_bps, planned_order_count, submitted_order_count, error, plan, started_at",
      )
      .eq("bot_id", id)
      .order("started_at", { ascending: false })
      .limit(15),
    supabase
      .from("orders")
      .select(
        "id, symbol, side, quantity, filled_quantity, avg_fill_price, requested_value, status, reject_reason, created_at",
      )
      .eq("bot_id", id)
      .order("created_at", { ascending: false })
      .limit(50),
  ]);

  const latest = runs?.[0] ?? null;
  const currency = String(bot.base_currency);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Link href="/bots" className="text-xs text-muted hover:text-ink">
            &larr; Bots
          </Link>
          <h1 className="mt-1 flex items-center gap-3 text-lg font-semibold tracking-tight">
            {String(bot.name)}
            <Badge tone={statusTone(String(bot.status))}>{String(bot.status)}</Badge>
          </h1>
          <p className="mt-0.5 text-sm text-muted">
            {money(bot.investment_amount as string, currency)} allocated
            {bot.drift_threshold_bps
              ? ` · ${bps(Number(bot.drift_threshold_bps), 1)} drift band`
              : ""}
            {bot.interval_minutes ? ` · every ${bot.interval_minutes}m` : ""}
            {bot.allow_fractional ? " · fractional" : " · whole shares"}
          </p>
        </div>

        <BotControls botId={id} status={String(bot.status)} />
      </div>

      <AllocationTable
        targets={(targets ?? []).map((t) => ({
          symbol: String(t.symbol),
          target_weight_bps: Number(t.target_weight_bps),
        }))}
        plan={(latest?.plan as Record<string, unknown> | null) ?? null}
        currency={currency}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Recent runs</CardTitle>
          </CardHeader>
          <CardBody className="p-0">
            {!runs?.length ? (
              <p className="px-5 py-8 text-center text-sm text-muted">
                No evaluations yet. Start the bot, or run one now.
              </p>
            ) : (
              <ul className="divide-y divide-border">
                {runs.map((run) => (
                  <li key={String(run.id)} className="px-5 py-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm">{String(run.trigger_reason)}</p>
                        <p className="mt-0.5 text-xs text-muted tabular">
                          {dateTime(run.started_at as string)}
                          {run.max_drift_bps !== null
                            ? ` · drift ${bps(Number(run.max_drift_bps))}`
                            : ""}
                          {` · ${run.submitted_order_count}/${run.planned_order_count} orders`}
                        </p>
                        {run.error ? (
                          <p className="mt-1 text-xs text-negative">{String(run.error)}</p>
                        ) : null}
                      </div>
                      <Badge tone={statusTone(String(run.status))}>{String(run.status)}</Badge>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Order history</CardTitle>
          </CardHeader>
          <CardBody className="p-0">
            {!orders?.length ? (
              <p className="px-5 py-8 text-center text-sm text-muted">No orders yet.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-border text-left text-xs text-muted">
                    <tr>
                      <th className="px-5 py-2 font-medium">Symbol</th>
                      <th className="px-5 py-2 font-medium">Side</th>
                      <th className="px-5 py-2 text-right font-medium">Qty</th>
                      <th className="px-5 py-2 text-right font-medium">Fill</th>
                      <th className="px-5 py-2 text-right font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((order) => (
                      <tr key={String(order.id)} className="border-b border-border last:border-0">
                        <td className="px-5 py-2 font-medium">{String(order.symbol)}</td>
                        <td className="px-5 py-2">
                          <span
                            className={order.side === "buy" ? "text-positive" : "text-negative"}
                          >
                            {String(order.side)}
                          </span>
                        </td>
                        <td className="px-5 py-2 text-right tabular">
                          {quantity(order.quantity as string)}
                        </td>
                        <td className="px-5 py-2 text-right tabular">
                          {order.avg_fill_price
                            ? money(order.avg_fill_price as string, currency)
                            : "—"}
                        </td>
                        <td className="px-5 py-2 text-right">
                          <Badge tone={statusTone(String(order.status))}>
                            {String(order.status)}
                          </Badge>
                          {order.reject_reason ? (
                            <p className="mt-0.5 text-xs text-muted">
                              {String(order.reject_reason)}
                            </p>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  );
}
