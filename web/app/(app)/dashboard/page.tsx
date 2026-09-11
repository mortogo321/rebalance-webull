import Link from "next/link";
import { AccountPanel } from "@/components/account-panel";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { bps, dateTime, money, relative } from "@/lib/format";
import { createClient } from "@/lib/supabase/server";

// Portfolio values change under the reader; never serve a cached page.
export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const supabase = await createClient();

  // Both reads go through RLS as the signed-in user. No user_id filter appears
  // anywhere in this file -- the database applies it, which is the point.
  const [{ data: connection }, { data: bots }] = await Promise.all([
    supabase.from("broker_connections").select("*").maybeSingle(),
    supabase.from("bot_overview").select("*").order("name"),
  ]);

  const running = (bots ?? []).filter((b) => b.status === "running").length;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-0.5 text-sm text-muted">
            {running} of {bots?.length ?? 0} bot{bots?.length === 1 ? "" : "s"} running
          </p>
        </div>
        <Button asChild size="sm">
          <Link href="/bots/new">New bot</Link>
        </Button>
      </div>

      {connection?.status !== "connected" ? (
        <Card className="border-warning/40 bg-warning/8">
          <CardBody className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">No broker connected</p>
              <p className="mt-0.5 text-sm text-muted">
                Bots evaluate but cannot trade until an account is attached.
              </p>
            </div>
            <Button asChild variant="outline" size="sm">
              <Link href="/connection">Connect account</Link>
            </Button>
          </CardBody>
        </Card>
      ) : null}

      <AccountPanel />

      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Bots</CardTitle>
          <Link href="/bots" className="text-xs text-accent hover:underline">
            View all
          </Link>
        </CardHeader>
        <CardBody className="p-0">
          {!bots?.length ? (
            <p className="px-5 py-8 text-center text-sm text-muted">
              No bots yet.{" "}
              <Link href="/bots/new" className="text-accent hover:underline">
                Create your first
              </Link>
              .
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="border-b border-border text-left text-xs text-muted">
                  <tr>
                    <th className="px-5 py-2.5 font-medium">Name</th>
                    <th className="px-5 py-2.5 font-medium">Status</th>
                    <th className="px-5 py-2.5 text-right font-medium">Allocated</th>
                    <th className="px-5 py-2.5 text-right font-medium">Max drift</th>
                    <th className="px-5 py-2.5 text-right font-medium">Last run</th>
                  </tr>
                </thead>
                <tbody>
                  {bots.map((bot) => (
                    <tr key={String(bot.id)} className="border-b border-border last:border-0">
                      <td className="px-5 py-3">
                        <Link
                          href={`/bots/${bot.id}`}
                          className="font-medium hover:text-accent hover:underline"
                        >
                          {String(bot.name)}
                        </Link>
                        <span className="ml-2 text-xs text-muted">
                          {String(bot.target_count)} holdings
                        </span>
                      </td>
                      <td className="px-5 py-3">
                        <Badge tone={statusTone(String(bot.status))}>{String(bot.status)}</Badge>
                      </td>
                      <td className="px-5 py-3 text-right tabular">
                        {money(bot.investment_amount as string, String(bot.base_currency))}
                      </td>
                      <td className="px-5 py-3 text-right tabular">
                        {bot.latest_max_drift_bps === null
                          ? "—"
                          : bps(Number(bot.latest_max_drift_bps))}
                      </td>
                      <td
                        className="px-5 py-3 text-right text-muted tabular"
                        title={dateTime(bot.latest_run_at as string)}
                      >
                        {relative(bot.latest_run_at as string)}
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
  );
}
