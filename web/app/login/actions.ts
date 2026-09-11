"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

/**
 * Sign in with email and password.
 *
 * A server action rather than a client-side call so the session cookies are set
 * by the server on the same response that redirects -- the alternative races the
 * navigation against the cookie write and intermittently lands back on /login.
 */
export async function signIn(_prev: { error?: string }, formData: FormData) {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const next = String(formData.get("next") ?? "/dashboard");

  if (!email || !password) {
    return { error: "Enter your email and password." };
  }

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });

  if (error) {
    // Deliberately not "no such user" vs "wrong password": that difference
    // turns the login form into an account-enumeration oracle.
    return { error: "Those credentials did not work. Check them and try again." };
  }

  revalidatePath("/", "layout");
  // Only same-origin paths: an open redirect here would be a phishing vector.
  redirect(next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard");
}

export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  revalidatePath("/", "layout");
  redirect("/login");
}
