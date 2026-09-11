/**
 * Configuration for the edge function.
 *
 * Supabase injects SUPABASE_URL and the service key automatically. Everything
 * else comes from `supabase/functions/.env.<APP_ENV>`, which the CLI loads via
 * `--env-file` locally and which CI supplies as secrets when deploying.
 *
 * Read once at module load and validated immediately: a function that boots with
 * a missing encryption key would accept credentials it cannot store.
 */

function required(name: string, ...fallbacks: string[]): string {
  for (const key of [name, ...fallbacks]) {
    const value = Deno.env.get(key);
    if (value && value.trim()) return value.trim();
  }
  throw new Error(
    `missing required environment variable ${name}. ` +
      `Local: set it in supabase/functions/.env.development. ` +
      `Deployed: supabase secrets set ${name}=...`,
  );
}

function optional(name: string, fallback = ""): string {
  return Deno.env.get(name)?.trim() || fallback;
}

export const env = {
  appEnv: optional("APP_ENV", "development"),

  supabaseUrl: required("SUPABASE_URL"),
  // The newer secret-key format and the legacy service-role JWT are both
  // accepted, so this works against a current local stack and an older project.
  supabaseSecretKey: required("SUPABASE_SECRET_KEY", "SUPABASE_SERVICE_ROLE_KEY"),
  // RLS-constrained key used for every read, paired with the caller's JWT.
  supabasePublishableKey: required("SUPABASE_PUBLISHABLE_KEY", "SUPABASE_ANON_KEY"),

  encryptionKey: required("APP_ENCRYPTION_KEY"),
  encryptionKeyVersion: Number(optional("APP_ENCRYPTION_KEY_VERSION", "1")),

  engineUrl: required("ENGINE_URL"),
  engineToken: required("ENGINE_INTERNAL_TOKEN"),
  engineTimeoutMs: Number(optional("ENGINE_TIMEOUT_MS", "15000")),
} as const;
