"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState, useTransition } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input, Select } from "@/components/ui/field";
import { ApiError, api } from "@/lib/api";
import { percentToBps, type TargetInput, validateTargets } from "@/lib/targets";
import { cn } from "@/lib/utils";

/**
 * A row carries an id React can key on. Neither field is usable as one: a
 * symbol is blank on a new row and duplicated while one is being retyped, and
 * keying on the array index reuses the removed row's DOM node for its
 * successor — so deleting the middle of three rows moves the focus ring and
 * caret onto the wrong input.
 */
type TargetRow = TargetInput & { id: string };

const newRow = (symbol = "", percent = "0"): TargetRow => ({
  id: crypto.randomUUID(),
  symbol,
  percent,
});

const blankRows = (): TargetRow[] => [
  newRow("AAPL", "40"),
  newRow("MSFT", "35"),
  newRow("NVDA", "25"),
];

export function BotForm() {
  const router = useRouter();
  const [busy, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [amount, setAmount] = useState("100000");
  const [triggerType, setTriggerType] = useState("schedule_or_drift");
  const [interval, setInterval] = useState("60");
  const [threshold, setThreshold] = useState("5");
  const [minOrder, setMinOrder] = useState("25");
  const [cashBuffer, setCashBuffer] = useState("2");
  const [fractional, setFractional] = useState(false);
  // Lazy initialiser: blankRows() mints ids, so it must run per mount rather
  // than once at module load.
  const [targets, setTargets] = useState<TargetRow[]>(blankRows);

  const {
    totalBps,
    valid: balanced,
    error: basketError,
  } = useMemo(() => validateTargets(targets), [targets]);

  const setRow = (index: number, patch: Partial<TargetRow>) =>
    setTargets((rows) => rows.map((r, i) => (i === index ? { ...r, ...patch } : r)));

  const submit = () => {
    setError(null);
    startTransition(async () => {
      try {
        const { id } = await api.createBot({
          name: name.trim(),
          investment_amount: Number(amount),
          trigger_type: triggerType,
          interval_minutes: triggerType === "drift" ? null : Number(interval),
          drift_threshold_bps:
            triggerType === "schedule" ? null : Math.round(Number(threshold) * 100),
          min_order_value: Number(minOrder),
          cash_buffer_bps: Math.round(Number(cashBuffer) * 100),
          allow_fractional: fractional,
          targets: targets.map((t) => ({
            symbol: t.symbol.trim().toUpperCase(),
            weight_bps: percentToBps(t.percent),
          })),
        });
        router.push(`/bots/${id}`);
      } catch (e) {
        setError(
          e instanceof ApiError ? `${e.message}${e.details ? "" : ""}` : "could not create the bot",
        );
      }
    });
  };

  return (
    <form
      className="grid gap-6 lg:grid-cols-2"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <Card>
        <CardHeader>
          <CardTitle>Basics</CardTitle>
        </CardHeader>
        <CardBody className="space-y-4">
          <Field label="Name">
            <Input value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} />
          </Field>

          <Field
            label="Amount to invest"
            hint="The sleeve this bot manages. It never deploys more than this, even if the account holds more."
          >
            <Input
              type="number"
              min="1"
              step="0.01"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
          </Field>

          <Field label="Rebalance when">
            <Select value={triggerType} onChange={(e) => setTriggerType(e.target.value)}>
              <option value="schedule_or_drift">Schedule or drift — whichever comes first</option>
              <option value="schedule">On a schedule only</option>
              <option value="drift">When drift exceeds a band</option>
            </Select>
          </Field>

          <div className="grid gap-4 sm:grid-cols-2">
            {triggerType !== "drift" ? (
              <Field label="Every (minutes)">
                <Input
                  type="number"
                  min="1"
                  value={interval}
                  onChange={(e) => setInterval(e.target.value)}
                  required
                />
              </Field>
            ) : null}

            {triggerType !== "schedule" ? (
              <Field label="Drift band (%)" hint="Trade once any holding is this far off target.">
                <Input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={threshold}
                  onChange={(e) => setThreshold(e.target.value)}
                  required
                />
              </Field>
            ) : null}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Target basket</CardTitle>
          <span className={cn("text-xs tabular", balanced ? "text-positive" : "text-negative")}>
            {(totalBps / 100).toFixed(2)}% of 100%
          </span>
        </CardHeader>
        <CardBody className="space-y-3">
          {targets.map((row, index) => (
            <div key={row.id} className="flex items-end gap-2">
              <div className="flex-1">
                <Field label={index === 0 ? "Symbol" : ""}>
                  <Input
                    value={row.symbol}
                    onChange={(e) => setRow(index, { symbol: e.target.value.toUpperCase() })}
                    placeholder="AAPL"
                    required
                    maxLength={12}
                  />
                </Field>
              </div>
              <div className="w-28">
                <Field label={index === 0 ? "Weight %" : ""}>
                  <Input
                    type="number"
                    min="0"
                    max="100"
                    step="0.01"
                    value={row.percent}
                    onChange={(e) => setRow(index, { percent: e.target.value })}
                    required
                  />
                </Field>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="mb-0.5"
                onClick={() => setTargets((rows) => rows.filter((_, i) => i !== index))}
                disabled={targets.length <= 1}
                aria-label={`Remove ${row.symbol || "row"}`}
              >
                Remove
              </Button>
            </div>
          ))}

          <div className="flex flex-wrap gap-2 pt-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setTargets((rows) => [...rows, newRow()])}
              disabled={targets.length >= 50}
            >
              Add holding
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                // Integer bps, remainder onto the first row, so an even split of
                // three still totals exactly 100% instead of 99.99%.
                const each = Math.floor(10000 / targets.length);
                const remainder = 10000 - each * targets.length;
                setTargets((rows) =>
                  rows.map((r, i) => ({
                    ...r,
                    percent: ((i === 0 ? each + remainder : each) / 100).toFixed(2),
                  })),
                );
              }}
            >
              Split evenly
            </Button>
          </div>
        </CardBody>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle>Execution</CardTitle>
        </CardHeader>
        <CardBody className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Minimum order value"
            hint="Smaller orders are skipped as not worth the commission."
          >
            <Input
              type="number"
              min="0"
              step="0.01"
              value={minOrder}
              onChange={(e) => setMinOrder(e.target.value)}
            />
          </Field>

          <Field label="Cash buffer (%)" hint="Held back on purpose and not deployed.">
            <Input
              type="number"
              min="0"
              max="100"
              step="0.01"
              value={cashBuffer}
              onChange={(e) => setCashBuffer(e.target.value)}
            />
          </Field>

          <Field label="Fractional shares" hint="Off means whole shares only, rounded down.">
            <label className="flex h-9 items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={fractional}
                onChange={(e) => setFractional(e.target.checked)}
                className="size-4"
              />
              Allow fractional quantities
            </label>
          </Field>
        </CardBody>
      </Card>

      <div className="lg:col-span-2">
        {error ? (
          <p role="alert" className="mb-3 text-sm text-negative">
            {error}
          </p>
        ) : null}
        {!balanced ? <p className="mb-3 text-sm text-muted">{basketError}</p> : null}
        <Button type="submit" disabled={busy || !balanced}>
          {busy ? "Creating…" : "Create bot"}
        </Button>
      </div>
    </form>
  );
}
