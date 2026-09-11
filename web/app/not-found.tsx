import Link from "next/link";

export default function NotFound() {
  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="text-center">
        <h1 className="text-lg font-semibold">Not found</h1>
        <p className="mt-1 text-sm text-muted">
          That page does not exist, or it is not yours to view.
        </p>
        <Link href="/dashboard" className="mt-4 inline-block text-sm text-accent hover:underline">
          Back to dashboard
        </Link>
      </div>
    </main>
  );
}
