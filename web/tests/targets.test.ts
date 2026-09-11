import { describe, expect, it } from "vitest";
import { percentToBps, totalBps, validateTargets } from "@/lib/targets";

/**
 * The basket rules are the one place a rounding mistake becomes a wrong trade,
 * so these tests are about exactness rather than coverage: the cases below are
 * the ones where floating point, case, or whitespace would silently let an
 * invalid basket through.
 */

describe("percentToBps", () => {
  it("converts a decimal percent to integer basis points", () => {
    expect(percentToBps("40")).toBe(4000);
    expect(percentToBps("33.33")).toBe(3333);
    expect(percentToBps("0.01")).toBe(1);
  });

  it("treats blank and undefined input as zero rather than NaN", () => {
    // A freshly added row has percent "" until the user types.
    expect(percentToBps("")).toBe(0);
  });

  it("rounds to the nearest basis point", () => {
    expect(percentToBps("33.335")).toBe(3334);
    expect(percentToBps("33.334")).toBe(3333);
  });

  it("does not accumulate float error: 3 x 33.33% is exactly 9999", () => {
    // 33.33 * 100 is 3332.9999999999995 as a double. Rounding per row is what
    // keeps the sum an integer, and is why weights are stored as bps at all.
    expect(totalBps([r("A", "33.33"), r("B", "33.33"), r("C", "33.33")])).toBe(9999);
  });
});

describe("validateTargets", () => {
  it("accepts a basket totalling exactly 100%", () => {
    const result = validateTargets([r("AAPL", "40"), r("MSFT", "35"), r("NVDA", "25")]);
    expect(result).toEqual({ valid: true, totalBps: 10000, error: null });
  });

  it("accepts an uneven split whose remainder lands on one row", () => {
    // What the form's "Split evenly" button produces for three holdings.
    const result = validateTargets([r("A", "33.34"), r("B", "33.33"), r("C", "33.33")]);
    expect(result.valid).toBe(true);
    expect(result.totalBps).toBe(10000);
  });

  it("rejects an empty basket", () => {
    const result = validateTargets([]);
    expect(result.valid).toBe(false);
    expect(result.error).toBe("Add at least one holding.");
  });

  it("rejects duplicate symbols regardless of case or surrounding space", () => {
    const result = validateTargets([r("aapl", "50"), r(" AAPL ", "50")]);
    expect(result.valid).toBe(false);
    expect(result.error).toBe("Each symbol can only appear once.");
  });

  it("reports the duplicate before the total, so the actionable error wins", () => {
    // Both rules are broken here; the user cannot fix the total until the
    // duplicate is gone, so that is the message worth showing.
    const result = validateTargets([r("AAPL", "10"), r("AAPL", "10")]);
    expect(result.error).toBe("Each symbol can only appear once.");
  });

  it("rejects a basket that is one basis point short", () => {
    const result = validateTargets([r("A", "50"), r("B", "49.99")]);
    expect(result.valid).toBe(false);
    expect(result.totalBps).toBe(9999);
    expect(result.error).toBe("Weights must total exactly 100%.");
  });

  it("rejects a basket that is over 100%", () => {
    const result = validateTargets([r("A", "60"), r("B", "50")]);
    expect(result.valid).toBe(false);
    expect(result.totalBps).toBe(11000);
  });

  it("still reports the running total when the basket is invalid", () => {
    // The form renders this total live while the user is still typing.
    expect(validateTargets([r("A", "10")]).totalBps).toBe(1000);
  });
});

function r(symbol: string, percent: string) {
  return { symbol, percent };
}
