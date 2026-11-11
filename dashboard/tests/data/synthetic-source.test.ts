// @vitest-environment node
import { beforeAll, describe, expect, it } from "vitest";
import {
  PLAYER_STAT_KEYS,
  TEAM_STAT_KEYS,
  scoringSide,
  type CardEvent,
  type GoalEvent,
  type MatchDetail,
  type PlayerSeason,
  type Stat,
  type TeamSeason,
  type TeamStanding,
} from "@/contract";
import { createSyntheticSource, loadSyntheticDataset } from "@/server/synthetic/synthetic-source";

/**
 * The synthetic season through the contract: what the screens will receive,
 * the special cases they must show, and season totals that can be justified
 * match by match.
 */
const source = createSyntheticSource(loadSyntheticDataset());
const SEASON = "season-demo-2026-27";
const RJ = "team-real-jaen";

let rj: TeamSeason;
let rjMatches: MatchDetail[];
let standings: TeamStanding[];

beforeAll(async () => {
  rj = (await source.teamSeason(SEASON, RJ))!;
  standings = (await source.seasonTeams(SEASON))!;
  rjMatches = await Promise.all(
    rj.matches.map(async ({ match }) => (await source.match(match.id))!),
  );
});

const played = () => rjMatches.filter((m) => m.status === "finished");
const rjSide = (m: MatchDetail) => (m.home.id === RJ ? "home" : "away");

describe("SyntheticSource", () => {
  it("exposes one demo competition and season", async () => {
    const [competition, ...others] = await source.competitions();
    expect(others).toHaveLength(0);
    expect(competition.name).toMatch(/Demostración/);
    expect(competition.seasons).toEqual([
      { id: SEASON, competitionId: competition.id, name: "2026/27", rounds: 38, currentRound: 14 },
    ]);
  });

  it("returns null for anything unknown", async () => {
    expect(await source.seasonMatches("season-x", {})).toBeNull();
    expect(await source.match("match-x")).toBeNull();
    expect(await source.matchComments("match-x")).toBeNull();
    expect(await source.seasonTeams("season-x")).toBeNull();
    expect(await source.teamSeason(SEASON, "team-x")).toBeNull();
    expect(await source.player("player-x", SEASON)).toBeNull();
    expect(await source.player("player-0001", "season-x")).toBeNull();
  });

  it("filters matches by round and team", async () => {
    const round = (await source.seasonMatches(SEASON, { round: 3 }))!;
    expect(round).toHaveLength(10);
    expect(round.every((m) => m.round === 3)).toBe(true);
    const team = (await source.seasonMatches(SEASON, { teamId: RJ }))!;
    expect(team).toHaveLength(38);
    expect(team.every((m) => m.home.id === RJ || m.away.id === RJ)).toBe(true);
    expect(await source.seasonMatches(SEASON, { round: 3, teamId: RJ })).toHaveLength(1);
  });

  it("marks demo comments as not collected and does not fabricate narratives", async () => {
    const match = (await source.seasonMatches(SEASON, {}))![0];
    expect(await source.matchComments(match.id)).toMatchObject({ availability: "not_collected", comments: [] });
  });

  it("ranks the table from the results", () => {
    expect(standings).toHaveLength(20);
    expect(standings.map((row) => row.rank)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1));
    const points = standings.map((row) => row.record.points);
    expect(points).toEqual([...points].sort((a, b) => b - a));
    for (const { record } of standings) {
      expect(record.wins + record.draws + record.losses).toBe(record.played);
      expect(record.points).toBe(record.wins * 3 + record.draws);
    }
    const totalFor = standings.reduce((sum, row) => sum + row.record.goalsFor, 0);
    expect(totalFor).toBe(standings.reduce((sum, row) => sum + row.record.goalsAgainst, 0));
  });

  it("justifies Real Jaén's record with its matches", () => {
    const results = rj.matches.map((m) => m.result).filter((r) => r !== null);
    expect(rj.standing.record).toMatchObject({
      played: results.length,
      wins: results.filter((r) => r === "win").length,
      draws: results.filter((r) => r === "draw").length,
      losses: results.filter((r) => r === "loss").length,
    });
  });

  it("justifies team season totals match by match, with their coverage", () => {
    const games = played();
    for (const key of TEAM_STAT_KEYS) {
      const perMatch = games.map((m) => m.teamStats![rjSide(m)][key]);
      const known = perMatch.filter((s): s is { value: number } => s.value !== null);
      const total = rj.stats[key];
      expect(total.of).toBe(games.length);
      expect(total.matches, key).toBe(known.length);
      if (key === "possession") {
        expect(total.value).toBeCloseTo(known.reduce((a, s) => a + s.value, 0) / known.length, 1);
      } else {
        expect(total.value, key).toBeCloseTo(
          known.reduce((a, s) => a + s.value, 0),
          2,
        );
      }
    }
    expect(rj.stats.shots.coverage).toBe("complete");
    expect(rj.stats.xg).toMatchObject({
      coverage: "partial",
      matches: games.length - 2,
      of: games.length,
    });
  });

  it("gives each finished team match its own and its rival's stats, as in the match sheet", () => {
    for (const teamMatch of rj.matches) {
      const detail = rjMatches.find((m) => m.id === teamMatch.match.id)!;
      if (teamMatch.match.status !== "finished") {
        expect(teamMatch.teamStats).toBeNull();
        continue;
      }
      const other = teamMatch.side === "home" ? "away" : "home";
      expect(teamMatch.teamStats).toEqual({
        own: detail.teamStats![teamMatch.side],
        opponent: detail.teamStats![other],
      });
    }
    // The rival's view of the same match mirrors it.
    const first = rj.matches[0];
    return source.teamSeason(SEASON, first.opponent.id).then((rival) => {
      const same = rival!.matches.find((m) => m.match.id === first.match.id)!;
      expect(same.teamStats).toEqual({
        own: first.teamStats!.opponent,
        opponent: first.teamStats!.own,
      });
    });
  });

  it("justifies every squad player's season totals with the matches they played", async () => {
    for (const { player, stats } of rj.squad) {
      const profile = (await source.player(player.id, SEASON))!;
      expect(profile.stats).toEqual(stats);
      expect(stats.appearances.value).toBe(profile.matches.length);
      expect(stats.starts.value).toBe(profile.matches.filter((m) => m.started).length);
      for (const key of PLAYER_STAT_KEYS) {
        if (key === "passAccuracy") continue;
        const known = profile.matches.map((m) => m.stats[key]).filter((s) => s.value !== null);
        expect(stats[key].of, key).toBe(profile.matches.length);
        if (known.length > 0) {
          expect(stats[key].value, `${player.name} ${key}`).toBeCloseTo(
            known.reduce((a, s) => a + s.value!, 0),
            2,
          );
        }
      }
    }
  });

  it("gives each player match the events that name them", async () => {
    const scorer = rj.squad.find((p) => (p.stats.goals.value ?? 0) >= 2)!;
    const profile = (await source.player(scorer.player.id, SEASON))!;
    const goals = profile.matches
      .flatMap((m) => m.events)
      .filter((e) => e.type === "goal" && !e.ownGoal && e.player.id === scorer.player.id);
    expect(goals).toHaveLength(scorer.stats.goals.value!);
    for (const goal of goals) expect(rjMatches.find((m) => m.id === goal.matchId)).toBeDefined();
  });
});

describe("synthetic cases the interface must show", () => {
  it("Real Jaén wins, draws and loses, at home and away", () => {
    const seen = new Set(rj.matches.filter((m) => m.result).map((m) => `${m.result}-${m.side}`));
    for (const result of ["win", "draw", "loss"]) {
      for (const side of ["home", "away"])
        expect(seen, `${result} ${side}`).toContain(`${result}-${side}`);
    }
  });

  it("includes a 0-0, a high-scoring match, a postponed match and scheduled rounds", () => {
    const scores = played().map((m) => m.score!);
    expect(scores).toContainEqual({ home: 0, away: 0 });
    expect(scores.some((s) => s.home + s.away >= 6)).toBe(true);
    const postponed = rj.matches.find((m) => m.match.status === "postponed")!;
    expect(postponed.match).toMatchObject({ round: 9, kickoff: null, score: null });
    expect(rj.matches.filter((m) => m.match.status === "scheduled")).toHaveLength(24);
  });

  it("shows different formations, and a line-up without formation nor slots", () => {
    const formations = new Set(played().map((m) => m.lineups![rjSide(m)].formation));
    expect(formations.size).toBeGreaterThanOrEqual(5);
    const opponentLineup = (m: MatchDetail) => m.lineups![rjSide(m) === "home" ? "away" : "home"];
    const lineup = played()
      .map(opponentLineup)
      .find((l) => l.formation === null)!;
    expect(lineup.formation).toBeNull();
    expect(lineup.starters.every((s) => s.slot === null && s.player.position !== null)).toBe(true);
  });

  it("has starters replaced, substitutes coming on and several changes at different minutes", () => {
    const subs = played()
      .flatMap((m) => m.events)
      .filter((e) => e.type === "substitution" && e.teamId === RJ);
    expect(new Set(subs.map((s) => s.minute)).size).toBeGreaterThan(5);
    const fiveChanges = played().some(
      (m) => m.events.filter((e) => e.type === "substitution" && e.teamId === RJ).length === 5,
    );
    expect(fiveChanges).toBe(true);
  });

  it("has goals with and without assist, a penalty, added time, yellow and red cards", () => {
    const events = played().flatMap((m) => m.events);
    const goals = events.filter((e) => e.type === "goal");
    expect(goals.some((g) => g.assist !== null)).toBe(true);
    expect(goals.some((g) => g.assist === null && !g.penalty)).toBe(true);
    expect(goals.some((g) => g.penalty && g.teamId === RJ)).toBe(true);
    expect(events.some((e) => e.addedTime !== null)).toBe(true);
    const cards = events.filter((e) => e.type === "card");
    expect(cards.some((c) => c.card === "yellow")).toBe(true);
    expect(cards.some((c) => c.card === "second_yellow" && c.teamId === RJ)).toBe(true);
    expect(cards.some((c) => c.card === "red")).toBe(true);
    expect(events.every((e) => e.clip === null)).toBe(true);
  });

  it("counts an own goal for the opponent, not as the player's goal", async () => {
    const match = played().find((m) =>
      m.events.some((e) => e.type === "goal" && e.ownGoal && e.teamId !== RJ),
    )!;
    const ownGoal = match.events.find((e): e is GoalEvent => e.type === "goal" && e.ownGoal)!;
    const playerSide = match.home.id === ownGoal.teamId ? "home" : "away";
    expect(scoringSide(ownGoal, playerSide)).toBe(rjSide(match));
    expect(ownGoal).toMatchObject({ assist: null, penalty: false });

    // Every score is the goals counted with scoringSide.
    for (const m of played()) {
      const goals = m.events.filter((e): e is GoalEvent => e.type === "goal");
      const side = (g: GoalEvent) => scoringSide(g, m.home.id === g.teamId ? "home" : "away");
      expect(goals.filter((g) => side(g) === "home")).toHaveLength(m.score!.home);
      expect(goals.filter((g) => side(g) === "away")).toHaveLength(m.score!.away);
    }

    // The player's goals exclude it, but the action stays in their match events.
    const profile = (await source.player(ownGoal.player.id, SEASON))!;
    const line = profile.matches.find((pm) => pm.match.id === match.id)!;
    expect(line.events.map((e) => e.id)).toContain(ownGoal.id);
    const ownScored = match.events.filter(
      (e) => e.type === "goal" && !e.ownGoal && e.player.id === ownGoal.player.id,
    );
    expect(line.stats.goals).toEqual({ value: ownScored.length });
  });

  it("keeps the real card in the event and derives yellowCards / redCards from it", async () => {
    const match = played().find((m) =>
      m.events.some((e) => e.type === "card" && e.card === "second_yellow" && e.teamId === RJ),
    )!;
    const sendingOff = match.events.find(
      (e): e is CardEvent => e.type === "card" && e.card === "second_yellow",
    )!;
    const cards = match.events.filter(
      (e): e is CardEvent => e.type === "card" && e.player.id === sendingOff.player.id,
    );
    expect(cards.map((c) => c.card)).toEqual(["yellow", "second_yellow"]);
    expect(match.playerStats[sendingOff.player.id]).toMatchObject({
      yellowCards: { value: 1 },
      redCards: { value: 1 },
    });

    // yellowCards = yellow events; redCards = second_yellow + red events.
    for (const m of played()) {
      for (const side of ["home", "away"] as const) {
        const teamCards = m.events.filter(
          (e): e is CardEvent => e.type === "card" && e.teamId === m[side].id,
        );
        expect(m.teamStats![side].yellowCards).toEqual({
          value: teamCards.filter((c) => c.card === "yellow").length,
        });
        expect(m.teamStats![side].redCards).toEqual({
          value: teamCards.filter((c) => c.card !== "yellow").length,
        });
      }
    }
    expect(played().some((m) => m.events.some((e) => e.type === "card" && e.card === "red"))).toBe(
      true,
    );
  });

  it("keeps a real zero as zero", () => {
    const zero = rj.squad.find(
      (p) => p.stats.appearances.value! >= 5 && p.stats.goals.value === 0,
    )!;
    expect(zero.stats.goals).toEqual({
      value: 0,
      coverage: "complete",
      matches: zero.stats.appearances.value,
      of: zero.stats.appearances.value,
    });
    const unused = rj.squad.find((p) => p.stats.appearances.value === 0)!;
    expect(unused.stats.goals).toEqual({ value: 0, coverage: "complete", matches: 0, of: 0 });
    expect(unused.stats.passAccuracy).toMatchObject({
      value: null,
      coverage: "none",
      reason: "not_available",
    });
  });

  it("has a player whose detailed stats are not available", () => {
    const limited = rj.squad.find(
      (p) => p.stats.shots.coverage === "none" && p.stats.appearances.value! > 0,
    )!;
    expect(limited.stats.shots).toMatchObject({ value: null, reason: "not_available" });
    expect(limited.stats.minutes.coverage).toBe("complete");
    expect(limited.stats.goals.coverage).toBe("complete");
  });

  it("keeps not_available and not_collected apart", () => {
    const notCollected = played().find(
      (m) =>
        m.teamStats!.home.xg.value === null &&
        (m.teamStats!.home.xg as { reason: string }).reason === "not_collected",
    )!;
    const notAvailable = played().find(
      (m) =>
        m.teamStats!.home.xg.value === null &&
        (m.teamStats!.home.xg as { reason: string }).reason === "not_available",
    )!;
    expect(notCollected).toBeDefined();
    expect(notAvailable).toBeDefined();
    expect(notCollected.id).not.toBe(notAvailable.id);
    const rows: Stat[] = Object.values(notCollected.playerStats).map((s) => s.passAccuracy);
    expect(rows.every((s) => s.value === null && s.reason === "not_collected")).toBe(true);
    const keeper = Object.entries(notCollected.playerStats).find(
      ([, s]) => s.saves.value !== null,
    )!;
    expect(keeper[1].goalsConceded.value).not.toBeNull();
    const outfield = Object.values(notCollected.playerStats).find((s) => s.saves.value === null)!;
    expect(outfield.saves).toEqual({ value: null, reason: "not_available" });
  });

  it("has players without height or preferred foot", async () => {
    const profiles = await Promise.all(
      rj.squad.map(async (p) => (await source.player(p.player.id, SEASON)) as PlayerSeason),
    );
    expect(profiles.some((p) => p.player.heightCm === null && p.matches.length > 0)).toBe(true);
    expect(profiles.some((p) => p.player.preferredFoot === null && p.matches.length > 0)).toBe(
      true,
    );
  });
});
