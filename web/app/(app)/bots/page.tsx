import Link from "next/link";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { bps, dateTime, money, relative } from "@/lib/format";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

const TRIGGER_LABEL: Record<string, string> = {
  schedule: "On schedule",
  drift: "On drift",
  schedule_or_drift: "Schedule or drift",
};

export default async function BotsPage() {
  const supabase = await createClient();
  const { data: bots } = await supabase.from("bot_overview").select("*").order("name");

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Bots</h1>
          <p className="mt-0.5 text-sm text-muted">
            Each bot holds a target basket and the rule for when to trade back to it.
          </p>
        </div>
        <Button asChild size="sm">
          <Link href="/bots/new">New bot</Link>
        </Button>
      </div>

      {!bots?.length ? (
        <Card>
          <CardBody className="py-12 text-center">
            <p className="text-sm text-muted">No bots yet.</p>
            <Button asChild size="sm" className="mt-4">
              <Link href="/bots/new">Create your first bot</Link>
            </Button>
          </CardBody>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {bots.map((bot) => (
            <Card key={String(bot.id)}>
              <CardBody className="space-y-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <Link
                      href={`/bots/${bot.id}`}
                      className="font-medium hover:text-accent hover:underline"
                    >
                      {String(bot.name)}
                    </Link>
                    <p className="mt-0.5 text-xs text-muted">
                      {TRIGGER_LABEL[String(bot.trigger_type)] ?? String(bot.trigger_type)}
                      {bot.drift_threshold_bps
                        ? ` · ${bps(Number(bot.drift_threshold_bps), 1)} band`
                        : ""}
                      {bot.interval_minutes ? ` · every ${bot.interval_minutes}m` : ""}
                    </p>
                  </div>
                  <Badge tone={statusTone(String(bot.status))}>{String(bot.status)}</Badge>
                </div>

                <dl className="grid grid-cols-3 gap-3 border-t border-border pt-3 text-sm">
                  <div>
                    <dt className="text-xs text-muted">Allocated</dt>
                    <dd className="tabular">
                      {money(bot.investment_amount as string, String(bot.base_currency))}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Holdings</dt>
                    <dd className="tabular">{String(bot.target_count)}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted">Max drift</dt>
                    <dd className="tabular">
                      {bot.latest_max_drift_bps === null
                        ? "—"
                        : bps(Number(bot.latest_max_drift_bps))}
                    </dd>
                  </div>
                </dl>

                <p className="text-xs text-muted" title={dateTime(bot.latest_run_at as string)}>
                  Last evaluated {relative(bot.latest_run_at as string)}
                  {bot.latest_run_status ? ` · ${bot.latest_run_status}` : ""}
                </p>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
