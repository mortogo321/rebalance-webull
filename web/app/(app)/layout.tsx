import Link from "next/link";
import { redirect } from "next/navigation";
import { signOut } from "@/app/login/actions";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/server";

const NAV = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/bots", label: "Bots" },
  { href: "/connection", label: "Connection" },
];

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();

  // The proxy already redirects unauthenticated requests. This is the
  // server-side backstop: a page must never render with a null user because a
  // proxy matcher was edited.
  if (!data.user) redirect("/login");

  return (
    <div className="min-h-screen">
      <header className="border-b border-border bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
          <Link href="/dashboard" className="text-sm font-semibold tracking-tight">
            Rebalance Webull
          </Link>

          <nav className="flex items-center gap-1">
            {NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                className="rounded-lg px-2.5 py-1.5 text-sm text-muted transition-colors hover:bg-canvas hover:text-ink"
              >
                {item.label}
              </Link>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            <span className="hidden text-xs text-muted sm:inline">{data.user.email}</span>
            <form action={signOut}>
              <Button variant="outline" size="sm" type="submit">
                Sign out
              </Button>
            </form>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>

      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-muted">
        <span className="tabular">
          {process.env.NEXT_PUBLIC_APP_ENV} &middot; build {process.env.NEXT_PUBLIC_GIT_SHA}
        </span>
      </footer>
    </div>
  );
}
