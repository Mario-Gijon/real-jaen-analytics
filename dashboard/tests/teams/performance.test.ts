import { describe, expect, it } from "vitest";
import { rjSeason } from "./fixtures.test-data";
import {
  average,
  difference,
  finishedMatches,
  periodMatches,
  venueRecord,
  type Indicator,
} from "@/features/teams/performance";

const rounds = (matches: { match: { round: number } }[]) => matches.map((m) => m.match.round);
const season = periodMatches(rjSeason.matches, "season");
const last3 = periodMatches(rjSeason.matches, "last3");
const value = (matches: typeof season, indicator: Indicator) => average(matches, indicator).value;

describe("team performance derivations", () => {
  it("uses only finished matches, in chronological order", () => {
    expect(rounds(finishedMatches(rjSeason.matches))).toEqual([1, 2, 3, 6]);
    expect(rounds(season)).toEqual([1, 2, 3, 6]);
  });

  it("takes the three most recent finished matches, skipping postponed and scheduled ones", () => {
    // currentRound is 3 in the fixture: the period must not depend on it.
    expect(rjSeason.season.currentRound).toBe(3);
    expect(rounds(last3)).toEqual([2, 3, 6]);
    const shuffled = [...rjSeason.matches].reverse();
    expect(rounds(periodMatches(shuffled, "last3"))).toEqual([2, 3, 6]);
  });

  it("splits wins, draws and losses at home and away from finished matches only", () => {
    expect(venueRecord(rjSeason.matches)).toEqual({
      home: { win: 1, draw: 0, loss: 1 },
      away: { win: 0, draw: 2, loss: 0 },
    });
  });

  it("averages goals scored and conceded per match from the scores", () => {
    expect(value(season, "goals")).toBe(1);
    expect(value(season, "goalsAgainst")).toBe(1);
    expect(value(last3, "goals")).toBeCloseTo(2 / 3);
    expect(value(last3, "goalsAgainst")).toBe(1);
  });

  it("derives conceded stats from the rival's stats in each match", () => {
    expect(value(season, "shots")).toBe(11);
    expect(value(season, "shotsAgainst")).toBe(9.5);
    expect(value(season, "shotsOnTarget")).toBe(4.5);
    expect(value(season, "shotsOnTargetAgainst")).toBe(4);
    expect(value(season, "possession")).toBe(53);
    expect(value(season, "corners")).toBe(5);
  });

  it("averages xG only over the matches that have it, and says so", () => {
    expect(average(season, "xg")).toEqual({
      value: expect.closeTo(3.4 / 3, 6),
      coverage: "partial",
      matches: 3,
      of: 4,
    });
    expect(average(season, "xgAgainst")).toMatchObject({
      value: expect.closeTo(1.2, 6),
      matches: 3,
    });
    expect(average(last3, "xg")).toMatchObject({
      value: expect.closeTo(0.95, 6),
      coverage: "partial",
      matches: 2,
      of: 3,
    });
  });

  it("never invents a value without data", () => {
    const noStats = rjSeason.matches.map((m) => ({ ...m, teamStats: null }));
    expect(average(periodMatches(noStats, "season"), "xg")).toMatchObject({
      value: null,
      coverage: "none",
    });
    expect(average([], "goals")).toMatchObject({ value: null, coverage: "none" });
  });

  it("compares a period with the season as a plain difference", () => {
    expect(difference(average(last3, "goals"), average(season, "goals"))).toBeCloseTo(-1 / 3);
    expect(difference(average(last3, "shotsAgainst"), average(season, "shotsAgainst"))).toBe(0.5);
    expect(
      difference(
        { value: null, coverage: "none", reason: "not_available", matches: 0, of: 3 },
        average(season, "xg"),
      ),
    ).toBeNull();
  });
});
