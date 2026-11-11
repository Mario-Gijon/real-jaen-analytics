// @vitest-environment node
import { describe, expect, it } from "vitest";
import { seasonStat, type Stat } from "@/contract";

const v = (value: number): Stat => ({ value });
const na: Stat = { value: null, reason: "not_available" };
const nc: Stat = { value: null, reason: "not_collected" };

describe("seasonStat", () => {
  it("adds complete totals, keeping a real zero", () => {
    expect(seasonStat([v(1), v(0), v(2)])).toEqual({
      value: 3,
      coverage: "complete",
      matches: 3,
      of: 3,
    });
    expect(seasonStat([v(0), v(0)])).toEqual({ value: 0, coverage: "complete", matches: 2, of: 2 });
  });

  it("marks a total as partial when some matches lack the metric", () => {
    expect(seasonStat([v(1.25), nc, v(0.5)])).toEqual({
      value: 1.75,
      coverage: "partial",
      matches: 2,
      of: 3,
    });
  });

  it("never turns a full absence into zero, and says why", () => {
    expect(seasonStat([na, na])).toEqual({
      value: null,
      coverage: "none",
      reason: "not_available",
      matches: 0,
      of: 2,
    });
    expect(seasonStat([na, nc])).toEqual({
      value: null,
      coverage: "none",
      reason: "not_collected",
      matches: 0,
      of: 2,
    });
  });

  it("averages and weights over covered matches only", () => {
    expect(seasonStat([v(40), na, v(60)], { kind: "mean" })).toMatchObject({
      value: 50,
      matches: 2,
      of: 3,
    });
    // 80 % of 10 passes and 50 % of 30 passes: 23 of 40.
    expect(
      seasonStat([v(80), v(50), nc], { kind: "weighted", weights: [v(10), v(30), nc] }),
    ).toEqual({
      value: 57.5,
      coverage: "partial",
      matches: 2,
      of: 3,
    });
  });

  it("covers a match with a real weight of zero without counting it", () => {
    expect(seasonStat([v(90), na], { kind: "weighted", weights: [v(20), v(0)] })).toEqual({
      value: 90,
      coverage: "complete",
      matches: 2,
      of: 2,
    });
  });

  it("handles a player with no matches", () => {
    expect(seasonStat([])).toEqual({ value: 0, coverage: "complete", matches: 0, of: 0 });
    expect(seasonStat([], { kind: "mean" })).toMatchObject({ value: null, coverage: "none" });
  });
});
