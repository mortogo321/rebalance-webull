import { cn } from "@/lib/utils";

const TONES = {
  neutral: "bg-canvas text-muted border-border",
  positive: "bg-positive/12 text-positive border-positive/25",
  negative: "bg-negative/12 text-negative border-negative/25",
  warning: "bg-warning/15 text-warning border-warning/30",
  accent: "bg-accent/12 text-accent border-accent/25",
} as const;

export type Tone = keyof typeof TONES;

export function Badge({
  tone = "neutral",
  children,
  className,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Bot and run statuses share a vocabulary; keep their colours in one place. */
export function statusTone(status: string | null | undefined): Tone {
  switch (status) {
    case "running":
    case "succeeded":
    case "filled":
    case "connected":
      return "positive";
    case "failed":
    case "rejected":
    case "error":
      return "negative";
    case "paused":
    case "partially_filled":
    case "skipped":
      return "warning";
    default:
      return "neutral";
  }
}
