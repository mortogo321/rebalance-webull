"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

/**
 * Start / pause / stop, plus a manual rebalance.
 *
 * "Rebalance now" forces the trigger but not the planner: the bot still refuses
 * to place orders that are dust, unaffordable, or round to zero shares. A manual
 * button that bypassed those checks would let a user do by hand exactly what the
 * automation is careful not to do.
 */
export function BotControls({ botId, status }: { botId: string; status: string }) {
  const router = useRouter();
  const [busy, startTransition] = useTransition();
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  const act = (fn: () => Promise<string>) =>
    startTransition(async () => {
      setNote(null);
      try {
        setNote({ tone: "ok", text: await fn() });
        router.refresh();
      } catch (e) {
        setNote({ tone: "bad", text: e instanceof Error ? e.message : "action failed" });
      }
    });

  const setStatus = (action: "start" | "pause" | "stop", done: string) =>
    act(async () => {
      await api.setStatus(botId, action);
      return done;
    });

  const rebalance = () =>
    act(async () => {
      const result = (await api.rebalanceNow(botId)) as {
        executed: boolean;
        reason: string;
        submitted_orders: number;
      };
      return result.executed
        ? `Rebalanced: ${result.submitted_orders} order(s) submitted.`
        : `No trade: ${result.reason}`;
    });

  return (
    <div className="text-right">
      <div className="flex flex-wrap items-center justify-end gap-2">
        {status !== "running" ? (
          <Button size="sm" onClick={() => setStatus("start", "Bot started.")} disabled={busy}>
            Start
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setStatus("pause", "Bot paused.")}
            disabled={busy}
          >
            Pause
          </Button>
        )}

        <Button
          size="sm"
          variant="outline"
          onClick={() => setStatus("stop", "Bot stopped.")}
          disabled={busy || status === "stopped"}
        >
          Stop
        </Button>

        <Button size="sm" variant="ghost" onClick={rebalance} disabled={busy}>
          {busy ? "Working…" : "Rebalance now"}
        </Button>
      </div>

      {note ? (
        <p
          role="status"
          className={`mt-2 max-w-md text-xs ${note.tone === "ok" ? "text-positive" : "text-negative"}`}
        >
          {note.text}
        </p>
      ) : null}
    </div>
  );
}
