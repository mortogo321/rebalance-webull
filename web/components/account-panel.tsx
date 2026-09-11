"use client";

import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { type AccountSnapshot, api } from "@/lib/api";
import { money, quantity } from "@/lib/format";

/**
 * Live account snapshot.
 *
 * Client-side rather than server-rendered because it comes from the broker
 * through the engine, not from Postgres: making the dashboard await a venue
 * round trip would block the whole page on the slowest dependency, and a broker
 * outage would render a blank dashboard instead of a working one with one
 * panel showing an error.
 */
export function AccountPanel() {
  const [data, setData] = useState<AccountSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setData(await api.account());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not load the account");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <Card>
      <CardHeader className="flex flex-wrap items-center justify-between gap-2">
        <CardTitle>Portfolio</CardTitle>
        <div className="flex items-center gap-2">
          {data ? (
            <span className="text-xs text-muted">
              {data.broker} &middot; {data.account_id}
            </span>
          ) : null}
          <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        </div>
      </CardHeader>

      <CardBody>
        {error ? (
          <p className="text-sm text-muted">
            {error} <span className="text-muted">Connect an account to see holdings.</span>
          </p>
        ) : !data ? (
          <p className="text-sm text-muted">{loading ? "Loading…" : "No data."}</p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              {[
                ["Total value", data.total_value],
                ["Cash", data.cash],
                ["Equity", data.equity],
              ].map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-muted">{label}</dt>
                  <dd className="mt-0.5 text-lg font-semibold tabular">
                    {money(value, data.currency)}
                  </dd>
                </div>
              ))}
            </dl>

            {data.positions.length ? (
              <div className="mt-5 overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="border-b border-border text-left text-xs text-muted">
                    <tr>
                      <th className="py-2 font-medium">Symbol</th>
                      <th className="py-2 text-right font-medium">Quantity</th>
                      <th className="py-2 text-right font-medium">Price</th>
                      <th className="py-2 text-right font-medium">Avg cost</th>
                      <th className="py-2 text-right font-medium">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.positions.map((p) => (
                      <tr key={p.symbol} className="border-b border-border last:border-0">
                        <td className="py-2 font-medium">{p.symbol}</td>
                        <td className="py-2 text-right tabular">{quantity(p.quantity)}</td>
                        <td className="py-2 text-right tabular">{money(p.price, data.currency)}</td>
                        <td className="py-2 text-right text-muted tabular">
                          {money(p.avg_cost, data.currency)}
                        </td>
                        <td className="py-2 text-right tabular">
                          {money(p.market_value, data.currency)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="mt-5 text-sm text-muted">No open positions.</p>
            )}
          </>
        )}
      </CardBody>
    </Card>
  );
}
