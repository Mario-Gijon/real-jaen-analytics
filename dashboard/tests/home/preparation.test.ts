import { describe, expect, it } from "vitest";
import { daysUntil, preparationHistory, preparationSummary, upcomingMatches } from "@/features/home/preparation";
import { club, history, NOW, other, played, rival, second, target } from "./fixtures";

describe("Preparation derivations", () => {
  it("suggests scheduled dated club matches chronologically, independent of round numbers", () => {
    expect(upcomingMatches([second, { ...target, status: "postponed" }, { ...target, kickoff: null },
      { ...target, kickoff: "invalid" }, { ...target, kickoff: "2026-09-01T11:00:00Z" },
      { ...target, home: rival, away: other }, { ...target, status: "finished" }, target], NOW).map(match => match.id))
      .toEqual(["target", "second"]);
  });
  it("selects only the five latest finished matches before the target", () => {
    expect(preparationHistory(history, target).map(row => row.match.id)).toEqual([10, 9, 8, 7, 6].map(n => `played-${n}`));
  });
  it("excludes scheduled, postponed, undated and finished matches on or after the target", () => {
    const extra = ["scheduled", "postponed"].map(status => ({ ...played(1), match: { ...played(1).match, status: status as "scheduled" | "postponed" } }));
    expect(preparationHistory([...extra, { ...played(1), match: { ...played(1).match, kickoff: null } },
      { ...played(1), match: { ...played(1).match, kickoff: target.kickoff } },
      { ...played(1), match: { ...played(1).match, kickoff: second.kickoff } }], target)).toEqual([]);
  });
  it("uses dates rather than round order to determine recency", () => {
    const rows = [played(1), { ...played(2), match: { ...played(2).match, round: 38, kickoff: "2026-08-01T11:00:00Z" } }];
    expect(preparationHistory(rows, target)[0].match.id).toBe("played-1");
  });
  it.each([0, 2, 6, 7])("handles %s historical matches without invented rows", count => {
    expect(preparationHistory(history.slice(0, count), target)).toHaveLength(Math.min(count, 5));
  });
  it("calculates V/E/D and goals from the analyzed rival's side", () => {
    const away = { ...played(1), side: "away" as const, opponent: club, result: "loss" as const,
      match: { ...played(1).match, home: club, away: rival, score: { home: 3, away: 1 } } };
    const draw = { ...played(2), result: "draw" as const, match: { ...played(2).match, score: { home: 0, away: 0 } } };
    const summary = preparationSummary([played(3), away, draw]);
    expect([summary.wins, summary.draws, summary.losses]).toEqual([1, 1, 1]);
    expect(summary.goalsFor.value).toBe(3); expect(summary.goalsAgainst.value).toBe(4);
  });
  it("averages only available xG and reports partial coverage separately for each side", () => {
    const missing = { ...played(2), teamStats: { own: { ...played(2).teamStats!.own, xg: { value: null, reason: "not_collected" as const } },
      opponent: { ...played(2).teamStats!.opponent, xg: { value: 1.5 } } } };
    const summary = preparationSummary([played(1), missing]);
    expect(summary.xg).toMatchObject({ value: 1.5, coverage: "partial", matches: 1, of: 2 });
    expect(summary.opponentXg).toMatchObject({ value: 1, coverage: "complete" });
  });
  it("averages individual real xG values, preserving zero and partial coverage", () => {
    const summary = preparationSummary([
      { ...played(1), xgFor: 0, xgAgainst: null },
      { ...played(2), xgFor: null, xgAgainst: 1.5 },
    ]);
    expect(summary.xg).toMatchObject({ value: 0, coverage: "partial", matches: 1, of: 2 });
    expect(summary.opponentXg).toMatchObject({ value: 1.5, coverage: "partial", matches: 1, of: 2 });
  });
  it("keeps genuine zero xG, and never turns absent xG into zero", () => {
    const zero = { ...played(1), teamStats: { own: { ...played(1).teamStats!.own, xg: { value: 0 } }, opponent: played(1).teamStats!.opponent } };
    expect(preparationSummary([zero]).xg.value).toBe(0);
    expect(preparationSummary([{ ...played(1), teamStats: null }]).xg).toMatchObject({ value: null, reason: "not_available" });
  });
  it("preserves not_collected when every xG value is absent", () => {
    const row = { ...played(1), teamStats: { own: { ...played(1).teamStats!.own, xg: { value: null, reason: "not_collected" as const } }, opponent: played(1).teamStats!.opponent } };
    expect(preparationSummary([row]).xg).toMatchObject({ value: null, reason: "not_collected", coverage: "none" });
  });
  it("computes Spain calendar days without inventing a date", () => {
    expect(daysUntil(null, NOW)).toBeNull(); expect(daysUntil("invalid", NOW)).toBeNull();
    expect(daysUntil("2026-09-01T12:00:00Z", NOW)).toBeNull();
    expect(daysUntil(target.kickoff, NOW)).toBe(31);
    expect(daysUntil("2026-10-01T23:00:00Z", NOW)).toBe(1);
  });
});
