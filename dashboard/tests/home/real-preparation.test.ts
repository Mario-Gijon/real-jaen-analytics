import { describe, expect, it } from "vitest";
import { preparationSummary, upcomingMatches } from "@/features/home/preparation";
import { rivalHistory, unconfirmedClubMatches } from "@/features/home/real-preparation";
import {
  afterTarget, clubMatches, jaen, later, next, NOW, observed, other, postponed, rival, rivalMatches, rivalPerformance,
  rivalPlayed, target,
} from "./real-fixtures";

const ids = (rows: readonly { match: { id: string } }[]) => rows.map((row) => row.match.id.replace("sofascore-match-", ""));

describe("Real Inicio: the encounter to prepare", () => {
  it("is the next current match with a confirmed date, never a postponed one, which stays a pending match", () => {
    const rows = clubMatches([postponed, target, next]).matches;
    expect(upcomingMatches(rows.map((r) => r.match), NOW).map((m) => m.id)).toEqual([target.id, next.id]);
    const { postponed: pending, stale } = unconfirmedClubMatches(rows, NOW);
    expect(pending.map((m) => m.id)).toEqual([postponed.id]);
    expect(stale).toEqual([]);
    // The same list reaches the view: nothing is removed nor recounted.
    expect(rows).toHaveLength(3);
  });

  it("never guesses a match with an unknown status into the next match or a stale one, and says it", () => {
    const unknown = observed(11, "2026-10-24T11:00:00Z", null, jaen, other, 11);
    const rows = clubMatches([unknown, target]).matches;
    expect(upcomingMatches(rows.map((r) => r.match), NOW)[0].id).toBe(target.id);
    expect(unconfirmedClubMatches(rows, NOW)).toEqual({ postponed: [], stale: [], unknownStatus: [unknown] });
  });

  it("does not propose a scheduled match whose date has passed: the imported data may not be up to date", () => {
    const stale = observed(8, "2026-10-18T16:15:00Z", "scheduled", jaen, other, 8);
    const rows = clubMatches([stale, target]).matches;
    expect(upcomingMatches(rows.map((r) => r.match), NOW)[0].id).toBe(target.id);
    expect(unconfirmedClubMatches(rows, NOW).stale.map((m) => m.id)).toEqual([stale.id]);
  });
});

describe("Real Inicio: the rival's last matches", () => {
  it("are its five latest finished matches before the target, by date and not by round, without the target or later ones", () => {
    const history = rivalHistory(rivalMatches().matches, target, rivalPerformance().competitions, NOW);
    expect(ids(history.recent)).toEqual(["107", "108", "104", "103", "102"]);
    expect(history.recent.map((e) => e.result)).toEqual(["loss", "draw", "loss", "draw", "draw"]);
    expect(ids(history.counted)).toEqual(ids(history.recent));
    expect([history.upcomingBefore, history.overdueBefore, history.unknownStatus, history.undatedFinished]).toEqual([0, 0, 0, 0]);
  });

  it("are recalculated against another target's date: its rival, and only matches before it", () => {
    const algeciras = [observed(201, "2026-10-04T17:00:00Z", "finished", later, other, 6, { home: 0, away: 1 }),
      observed(202, "2026-11-08T17:00:00Z", "finished", later, other, 11, { home: 2, away: 0 })];
    const history = rivalHistory(rivalMatches([...algeciras, next], later).matches, next, rivalPerformance(algeciras, later).competitions, NOW);
    expect(ids(history.recent)).toEqual(["201"]);
    // The first rival's match after J9 (29-10) is before J10 (02-11): with J10 as the target it enters the window.
    expect(ids(rivalHistory(rivalMatches().matches, next, rivalPerformance().competitions, NOW).recent)[0]).toBe("110");
  });

  it("lists a finished match the source does not count without a result, out of the summary", () => {
    const counted = rivalPlayed.filter((m) => m.id !== "sofascore-match-108");
    const history = rivalHistory(rivalMatches().matches, target, rivalPerformance(counted).competitions, NOW);
    expect(history.recent.find((e) => e.match.id === "sofascore-match-108")!.result).toBeNull();
    expect(ids(history.counted)).toEqual(["107", "104", "103", "102"]);
  });

  it("counts, without guessing them into the window, matches still to be played, overdue ones, unknown statuses and undated finished ones", () => {
    const overdue = [observed(109, "2026-10-21T17:00:00Z", "postponed", rival, other, 9),
      observed(112, "2026-10-22T17:00:00Z", "scheduled", other, rival, 12)];
    const upcoming = observed(113, "2026-10-24T17:00:00Z", "scheduled", rival, other, 13);
    const unknown = [observed(114, "2026-10-20T17:00:00Z", null, rival, other, 14), observed(115, null, null, other, rival, null),
      observed(116, "2026-11-20T17:00:00Z", null, rival, other, 16)];
    const undated = observed(111, null, "finished", rival, other, 11, { home: 1, away: 1 });
    const history = rivalHistory(rivalMatches([...rivalPlayed.slice(0, 2), ...overdue, upcoming, ...unknown, undated, target]).matches, target,
      rivalPerformance([...rivalPlayed.slice(0, 2), undated]).competitions, NOW);
    expect(ids(history.recent)).toEqual(["102", "101"]);
    // An unknown status after the target is irrelevant to it; before it or undated, it is said, never read as played or pending.
    expect([history.upcomingBefore, history.overdueBefore, history.unknownStatus, history.undatedFinished]).toEqual([1, 2, 2, 1]);
  });
});

describe("Real Inicio: the summary", () => {
  it("uses per-match xG from the rival's side for home and away matches", () => {
    const history = rivalHistory(rivalMatches().matches, target, rivalPerformance().competitions, NOW);
    const away = history.counted.find(entry => entry.match.id === "sofascore-match-107")!;
    const home = history.counted.find(entry => entry.match.id === "sofascore-match-108")!;
    expect([away.side, away.xgFor, away.xgAgainst]).toEqual(["away", 0.39, 1.56]);
    expect([home.side, home.xgFor, home.xgAgainst]).toEqual(["home", 2.15, 3.13]);
    const summary = preparationSummary([
      { ...away, result: "loss", teamStats: null },
      { ...home, result: "draw", teamStats: null },
    ]);
    expect([summary.wins, summary.draws, summary.losses]).toEqual([0, 1, 1]);
    expect([summary.goalsFor.value, summary.goalsAgainst.value]).toEqual([5, 6]);
    expect(summary.xg).toMatchObject({ value: 1.27, coverage: "complete", matches: 2, of: 2 });
    expect(summary.opponentXg.value).toBeCloseTo(2.345, 12);
    expect(summary.opponentXg).toMatchObject({ coverage: "complete", matches: 2, of: 2 });
  });

  it("excludes the target's own match and those after it from the summary", () => {
    const history = rivalHistory(rivalMatches().matches, target, rivalPerformance([...rivalPlayed, afterTarget]).competitions, NOW);
    expect(history.counted.some((e) => e.match.id === afterTarget.id || e.match.id === target.id)).toBe(false);
  });
});
