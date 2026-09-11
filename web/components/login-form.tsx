"use client";

import { useSearchParams } from "next/navigation";
import { useActionState } from "react";
import { useFormStatus } from "react-dom";
import { signIn } from "@/app/login/actions";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Field, Input } from "@/components/ui/field";

function SubmitButton() {
  const { pending } = useFormStatus();
  return (
    <Button type="submit" className="w-full" disabled={pending}>
      {pending ? "Signing in…" : "Sign in"}
    </Button>
  );
}

export function LoginForm() {
  const [state, formAction] = useActionState(signIn, {} as { error?: string });
  const next = useSearchParams().get("next") ?? "/dashboard";

  return (
    <Card>
      <CardBody>
        <form action={formAction} className="space-y-4">
          <input type="hidden" name="next" value={next} />

          <Field label="Email" htmlFor="email">
            <Input
              id="email"
              name="email"
              type="email"
              autoComplete="username"
              required
              defaultValue="demo@rebalance.test"
            />
          </Field>

          <Field label="Password" htmlFor="password">
            <Input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </Field>

          {state?.error ? (
            <p role="alert" className="text-sm text-negative">
              {state.error}
            </p>
          ) : null}

          <SubmitButton />
        </form>
      </CardBody>
    </Card>
  );
}
