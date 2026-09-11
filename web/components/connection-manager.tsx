"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";
import { Badge, statusTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/field";
import { api, type BrokerConnection } from "@/lib/api";
import { dateTime } from "@/lib/format";

export function ConnectionManager({ initial }: { initial: BrokerConnection }) {
  const router = useRouter();
  const [connection, setConnection] = useState(initial);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [busy, startTransition] = useTransition();

  const run = (fn: () => Promise<void>) =>
    startTransition(async () => {
      setMessage(null);
      try {
        await fn();
        router.refresh();
      } catch (e) {
        setMessage({ tone: "bad", text: e instanceof Error ? e.message : "something went wrong" });
      }
    });

  const save = () =>
    run(async () => {
      const result = await api.saveCredentials(apiKey, apiSecret);
      // Cleared immediately on success. The plaintext has no reason to stay in
      // component state -- it is already encrypted server-side, and React state
      // is visible to anything running in the page.
      setApiKey("");
      setApiSecret("");
      if (result.error) {
        setMessage({ tone: "bad", text: result.error });
      } else {
        setMessage({ tone: "ok", text: "Connected. Credentials stored encrypted." });
      }
      setConnection(await api.connection());
    });

  const verify = () =>
    run(async () => {
      const result = await api.verify();
      setMessage(
        result.error
          ? { tone: "bad", text: result.error }
          : { tone: "ok", text: "Connection verified." },
      );
      setConnection(await api.connection());
    });

  const disconnect = () =>
    run(async () => {
      await api.disconnect();
      setMessage({ tone: "ok", text: "Disconnected. Stored credentials were deleted." });
      setConnection(await api.connection());
    });

  const connected = connection.status === "connected";

  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Status</CardTitle>
          <Badge tone={statusTone(connection.status)}>{connection.status}</Badge>
        </CardHeader>
        <CardBody className="space-y-3 text-sm">
          <Row label="Environment" value={connection.environment ?? "—"} />
          <Row label="Account" value={connection.account_id ?? "—"} />
          <Row label="Currency" value={connection.account_currency ?? "—"} />
          <Row
            label="API key"
            value={connection.api_key_hint ? `•••• ${connection.api_key_hint}` : "none stored"}
          />
          <Row label="Last verified" value={dateTime(connection.last_verified_at)} />

          {connection.last_error ? (
            <p className="rounded-lg bg-negative/10 px-3 py-2 text-xs text-negative">
              {connection.last_error}
            </p>
          ) : null}

          <div className="flex gap-2 pt-1">
            <Button variant="outline" size="sm" onClick={verify} disabled={busy}>
              Verify
            </Button>
            <Button variant="danger" size="sm" onClick={disconnect} disabled={busy || !connected}>
              Disconnect
            </Button>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{connected ? "Replace API key" : "Attach API key"}</CardTitle>
        </CardHeader>
        <CardBody>
          <form
            className="space-y-4"
            onSubmit={(e) => {
              e.preventDefault();
              save();
            }}
          >
            <Field label="API key">
              <Input
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                required
                minLength={4}
                autoComplete="off"
                spellCheck={false}
                placeholder="wb_..."
              />
            </Field>

            <Field
              label="API secret"
              hint="Encrypted with AES-256-GCM before storage. It is never sent back to this page."
            >
              <Input
                type="password"
                value={apiSecret}
                onChange={(e) => setApiSecret(e.target.value)}
                required
                minLength={4}
                autoComplete="off"
              />
            </Field>

            {message ? (
              <p
                role="status"
                className={
                  message.tone === "ok" ? "text-sm text-positive" : "text-sm text-negative"
                }
              >
                {message.text}
              </p>
            ) : null}

            <Button type="submit" disabled={busy || !apiKey || !apiSecret}>
              {busy ? "Saving…" : connected ? "Replace key" : "Connect"}
            </Button>
          </form>
        </CardBody>
      </Card>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-xs text-muted">{label}</span>
      <span className="tabular">{value}</span>
    </div>
  );
}
