import { ConnectionManager } from "@/components/connection-manager";
import { createClient } from "@/lib/supabase/server";

export const dynamic = "force-dynamic";

export default async function ConnectionPage() {
  const supabase = await createClient();
  const { data } = await supabase.from("broker_connections").select("*").maybeSingle();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">Broker connection</h1>
        <p className="mt-0.5 text-sm text-muted">
          Attach a Webull API key so bots can read your account and place orders.
        </p>
      </div>

      {/* The server component passes only non-secret fields. The key and secret
          are not in this table at all -- they live encrypted in a schema that
          has no HTTP surface. */}
      <ConnectionManager
        initial={{
          status: data?.status ?? "disconnected",
          environment: data?.environment ?? null,
          account_id: data?.account_id ?? null,
          account_currency: data?.account_currency ?? null,
          api_key_hint: data?.api_key_hint ?? null,
          last_verified_at: data?.last_verified_at ?? null,
          last_error: data?.last_error ?? null,
        }}
      />
    </div>
  );
}
