import { describe, expect, it, vi } from "vitest";
import { bps, dateTime, money, quantity, relative, signedBps } from "@/lib/format";

/**
 * These run with TZ=UTC, pinned in vitest.config.ts — dateTime() and relative()
 * would otherwise assert whatever zone the machine happens to be in.
 */

describe("money", () => {
  it("formats a numeric string without going through a float first", () => {
    expect(money("1234.5")).toBe("$1,234.50");
  });

  it("renders null and undefined as zero rather than NaN", () => {
    expect(money(null)).toBe("$0.00");
    expect(money(undefined)).toBe("$0.00");
  });

  it("honours a non-default currency", () => {
    expect(money("1000", "EUR")).toBe("€1,000.00");
  });

  it("falls back to zero on unparseable input instead of rendering NaN", () => {
    expect(money("not a number")).toBe("$0.00");
  });
});

describe("quantity", () => {
  it("strips trailing zeros on a whole number of shares", () => {
    expect(quantity("12.00000000")).toBe("12");
  });

  it("keeps every significant place of a fractional share", () => {
    expect(quantity("0.12345678")).toBe("0.12345678");
  });

  it("renders null as zero", () => {
    expect(quantity(null)).toBe("0");
  });
});

describe("bps", () => {
  it("renders basis points as a percentage", () => {
    expect(bps(4000)).toBe("40.00%");
    expect(bps(3333)).toBe("33.33%");
  });

  it("defaults null to zero", () => {
    expect(bps(null)).toBe("0.00%");
  });

  it("honours a custom precision", () => {
    expect(bps(4000, 0)).toBe("40%");
  });
});

describe("signedBps", () => {
  it("prefixes a plus on positive drift so direction is unambiguous", () => {
    expect(signedBps(742)).toBe("+7.42%");
  });

  it("keeps the minus on negative drift", () => {
    expect(signedBps(-742)).toBe("-7.42%");
  });

  it("does not sign zero", () => {
    expect(signedBps(0)).toBe("0.00%");
  });
});

describe("dateTime", () => {
  it("renders an em dash for a missing timestamp", () => {
    expect(dateTime(null)).toBe("—");
    expect(dateTime("")).toBe("—");
  });

  it("formats an ISO timestamp in UTC", () => {
    expect(dateTime("2026-01-02T15:04:05Z")).toBe("2 Jan 2026, 15:04");
  });
});

describe("relative", () => {
  const now = new Date("2026-01-02T12:00:00Z");
  const ago = (ms: number) => new Date(now.getTime() - ms).toISOString();

  it("renders 'never' for a missing timestamp", () => {
    expect(relative(null)).toBe("never");
  });

  it("bucket by bucket, from seconds to days", () => {
    vi.useFakeTimers();
    vi.setSystemTime(now);
    try {
      expect(relative(ago(5_000))).toBe("just now");
      expect(relative(ago(5 * 60_000))).toBe("5m ago");
      expect(relative(ago(3 * 3_600_000))).toBe("3h ago");
      expect(relative(ago(2 * 86_400_000))).toBe("2d ago");
    } finally {
      vi.useRealTimers();
    }
  });
});
