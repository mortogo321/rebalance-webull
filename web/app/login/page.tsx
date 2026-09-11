import { Suspense } from "react";
import { LoginForm } from "@/components/login-form";

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8">
          <h1 className="text-xl font-semibold tracking-tight">Rebalance Webull</h1>
          <p className="mt-1 text-sm text-muted">Sign in to manage your rebalancing bots.</p>
        </div>

        <Suspense>
          <LoginForm />
        </Suspense>

        {/* The assignment has no sign-up flow: accounts come from the seed or
            Supabase Studio, so the demo credentials are shown here to make a
            clean clone usable without reading the README first. */}
        <div className="mt-6 rounded-card border border-border bg-surface px-4 py-3 text-xs text-muted">
          <p className="font-medium text-ink">Seeded demo accounts</p>
          <p className="mt-1 tabular">demo@rebalance.test &middot; Password123!</p>
          <p className="tabular">second@rebalance.test &middot; Password123!</p>
          <p className="mt-1.5">
            The second account exists to show isolation: it sees none of the first account&rsquo;s
            data.
          </p>
        </div>
      </div>
    </main>
  );
}
