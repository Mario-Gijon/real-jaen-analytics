// @vitest-environment node
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { datasetFiles, generateDataset } from "../../scripts/synthetic/generate.mts";
import { scoringSide } from "@/contract";
import type { RawEvent, RawLineup, RawMatch } from "@/server/synthetic/raw";
import { loadSyntheticDataset } from "@/server/synthetic/synthetic-source";

/**
 * Coherence of the versioned synthetic season. These checks catch the
 * inconsistencies that would later mislead us while designing the screens.
 */
const data = loadSyntheticDataset();
const teamIds = new Set(data.season.teams.map((team) => team.id));
const playerIds = new Set(data.players.map((player) => player.id));
const squadOf = new Map<string, { teamId: string; shirtNumber: number | null }>();
for (const squad of data.season.squads) {
  for (const member of squad.players)
    squadOf.set(member.playerId, { teamId: squad.teamId, shirtNumber: member.shirtNumber });
}
const matches = data.rounds.flatMap((round) => round.matches);
const finished = matches.filter((match) => match.status === "finished");
const sides = ["home", "away"] as const;

const time = (minute: number, addedTime: number | null) => minute + (addedTime ?? 0) / 100;
const teamOf = (match: RawMatch, side: "home" | "away") =>
  side === "home" ? match.homeTeamId : match.awayTeamId;
const sideOf = (match: RawMatch, teamId: string) => (match.homeTeamId === teamId ? "home" : "away");
const value = (stat: unknown) => (typeof stat === "number" ? stat : null);

/** When each player of a side was on the pitch, replaying substitutions and sendings-off. */
function intervals(match: RawMatch, side: "home" | "away") {
  const lineup = match.lineups![side];
  const result = new Map<string, { on: number; off: number }>(
    lineup.starters.map((s) => [s.playerId, { on: 0, off: Infinity }]),
  );
  for (const event of match.events) {
    if (event.teamId !== teamOf(match, side)) continue;
    const t = time(event.minute, event.addedTime);
    if (event.type === "substitution") {
      result.get(event.playerOutId)!.off = t;
      result.set(event.playerInId, { on: t, off: Infinity });
    } else if (event.type === "card" && event.card !== "yellow") {
      result.get(event.playerId)!.off = t;
    }
  }
  return result;
}

/** Runs a check on every finished match; failures name the match. */
const eachFinished = (check: (match: RawMatch) => void) => () => {
  for (const match of finished) {
    try {
      check(match);
    } catch (error) {
      if (error instanceof Error) error.message = `${match.id}: ${error.message}`;
      throw error;
    }
  }
};

const onPitch = (match: RawMatch, side: "home" | "away", playerId: string, t: number) => {
  const interval = intervals(match, side).get(playerId);
  return interval !== undefined && interval.on <= t && t <= interval.off;
};

describe("synthetic dataset", () => {
  it("is exactly what the generator produces with the fixed seed", () => {
    const dir = join(process.cwd(), "data", "synthetic");
    for (const [path, content] of datasetFiles(generateDataset())) {
      expect(
        readFileSync(join(dir, path), "utf8"),
        `${path} está desactualizado: npm run data:generate`,
      ).toBe(content);
    }
  });

  it("has the expected size: 20 teams, 38 rounds, 14 played", () => {
    expect(data.season.teams).toHaveLength(20);
    expect(data.rounds).toHaveLength(data.season.season.rounds);
    expect(new Set(finished.map((m) => m.round))).toEqual(
      new Set(Array.from({ length: 14 }, (_, i) => i + 1)),
    );
    expect(matches.filter((m) => m.status === "postponed")).toHaveLength(1);
    expect(matches.filter((m) => m.status === "scheduled").length).toBeGreaterThan(200);
  });

  it("references only existing ids, and every player is in exactly one squad", () => {
    expect(new Set(matches.map((m) => m.id)).size).toBe(matches.length);
    expect(squadOf.size).toBe(data.players.length);
    for (const id of squadOf.keys()) expect(playerIds.has(id)).toBe(true);
    for (const squad of data.season.squads) expect(teamIds.has(squad.teamId)).toBe(true);
    for (const match of matches) {
      expect(teamIds.has(match.homeTeamId) && teamIds.has(match.awayTeamId)).toBe(true);
      expect(match.homeTeamId).not.toBe(match.awayTeamId);
      for (const id of Object.keys(match.playerStats)) expect(playerIds.has(id)).toBe(true);
    }
  });

  it("follows a double round robin: one match per team and round, each pairing home and away", () => {
    for (const round of data.rounds) {
      const playing = round.matches.flatMap((m) => [m.homeTeamId, m.awayTeamId]);
      expect(new Set(playing).size).toBe(20);
      for (const match of round.matches) expect(match.round).toBe(round.round);
    }
    const pairings = new Set(matches.map((m) => `${m.homeTeamId}>${m.awayTeamId}`));
    expect(pairings.size).toBe(20 * 19);
  });

  it("keeps not-played matches empty", () => {
    for (const match of matches.filter((m) => m.status !== "finished")) {
      expect(match.score).toBeNull();
      expect(match.lineups).toBeNull();
      expect(match.events).toEqual([]);
      expect(match.teamStats).toBeNull();
      expect(match.playerStats).toEqual({});
    }
  });

  describe("every finished match", () => {
    it(
      "has coherent line-ups",
      eachFinished((match) => {
        for (const side of sides) {
          const lineup: RawLineup = match.lineups![side];
          const teamId = teamOf(match, side);
          const everyone = [...lineup.starters, ...lineup.bench];
          expect(lineup.starters).toHaveLength(11);
          expect(new Set(everyone.map((p) => p.playerId)).size, "jugador repetido").toBe(
            everyone.length,
          );
          for (const p of everyone) {
            expect(squadOf.get(p.playerId), `${p.playerId} no es de ${teamId}`).toEqual({
              teamId,
              shirtNumber: p.shirtNumber,
            });
          }
          expect(lineup.bench.every((p) => p.slot === null)).toBe(true);
          expect(
            lineup.starters.filter(
              (p) => data.players.find((x) => x.id === p.playerId)!.position === "goalkeeper",
            ),
          ).toHaveLength(1);

          if (lineup.formation === null) {
            expect(lineup.starters.every((p) => p.slot === null)).toBe(true);
            continue;
          }
          const lines = lineup.formation.split("-").map(Number);
          expect(lines.reduce((a, b) => a + b, 0)).toBe(10);
          const perLine = [1, ...lines];
          const slots = lineup.starters.map((p) => p.slot!);
          expect(new Set(slots.map(String)).size).toBe(11);
          perLine.forEach((count, line) => {
            const orders = slots
              .filter(([l]) => l === line)
              .map(([, order]) => order)
              .sort((a, b) => a - b);
            expect(orders).toEqual(Array.from({ length: count }, (_, i) => i));
          });
        }
      }),
    );

    it(
      "has events in order, from valid players of the right team, at valid minutes",
      eachFinished((match) => {
        const times = match.events.map((e) => time(e.minute, e.addedTime));
        expect(times).toEqual([...times].sort((a, b) => a - b));
        for (const event of match.events) {
          expect([match.homeTeamId, match.awayTeamId]).toContain(event.teamId);
          expect(event.minute).toBeGreaterThanOrEqual(1);
          expect(event.minute).toBeLessThanOrEqual(90);
          if (event.addedTime !== null) expect([45, 90]).toContain(event.minute);
          const players =
            event.type === "substitution"
              ? [event.playerInId, event.playerOutId]
              : [event.playerId];
          if (event.type === "goal" && event.assistId) players.push(event.assistId);
          for (const id of players)
            expect(squadOf.get(id)?.teamId, `${event.id}: ${id}`).toBe(event.teamId);
        }
      }),
    );

    it(
      "has substitutions from the pitch to the bench of the same team",
      eachFinished((match) => {
        for (const side of sides) {
          const lineup = match.lineups![side];
          const subs = match.events.filter(
            (e): e is Extract<RawEvent, { type: "substitution" }> =>
              e.type === "substitution" && e.teamId === teamOf(match, side),
          );
          expect(subs.length).toBeLessThanOrEqual(5);
          const entered = new Set<string>();
          for (const sub of subs) {
            const t = time(sub.minute, sub.addedTime);
            expect(
              onPitch(match, side, sub.playerOutId, t),
              `${sub.id}: sale alguien que no está en el campo`,
            ).toBe(true);
            expect(
              lineup.bench.some((p) => p.playerId === sub.playerInId),
              `${sub.id}: entra alguien que no está en el banquillo`,
            ).toBe(true);
            expect(entered.has(sub.playerInId)).toBe(false);
            entered.add(sub.playerInId);
            expect(sub.playerInId).not.toBe(sub.playerOutId);
          }
        }
      }),
    );

    it(
      "has goals that add up to the score, scored and assisted by players on the pitch",
      eachFinished((match) => {
        const goals = match.events.filter(
          (e): e is Extract<RawEvent, { type: "goal" }> => e.type === "goal",
        );
        for (const side of sides) {
          // An own goal by the other side's player counts for this side.
          const counted = goals.filter((g) => scoringSide(g, sideOf(match, g.teamId)) === side);
          expect(counted).toHaveLength(match.score![side]);
        }
        for (const goal of goals) {
          const side = sideOf(match, goal.teamId);
          const t = time(goal.minute, goal.addedTime);
          expect(onPitch(match, side, goal.playerId, t)).toBe(true);
          if (goal.ownGoal) expect([goal.assistId, goal.penalty]).toEqual([null, false]);
          if (goal.assistId === null) continue;
          expect(goal.assistId).not.toBe(goal.playerId);
          expect(goal.penalty).toBe(false);
          expect(onPitch(match, side, goal.assistId, t)).toBe(true);
        }
      }),
    );

    it(
      "has cards for players on the pitch, and nothing after a sending-off",
      eachFinished((match) => {
        for (const side of sides) {
          const cards = match.events.filter(
            (e): e is Extract<RawEvent, { type: "card" }> =>
              e.type === "card" && e.teamId === teamOf(match, side),
          );
          for (const card of cards) {
            expect(onPitch(match, side, card.playerId, time(card.minute, card.addedTime))).toBe(
              true,
            );
            if (card.card === "second_yellow") {
              expect(
                cards.filter((c) => c.playerId === card.playerId && c.card === "yellow"),
              ).toHaveLength(1);
            }
            if (card.card === "yellow") {
              expect(
                cards.filter((c) => c.playerId === card.playerId && c.card === "yellow"),
              ).toHaveLength(1);
            }
          }
        }
      }),
    );

    it(
      "has player stats exactly for the players who took part, consistent with the events",
      eachFinished((match) => {
        for (const side of sides) {
          const teamId = teamOf(match, side);
          const played = intervals(match, side);
          const ids = Object.keys(match.playerStats).filter(
            (id) => squadOf.get(id)!.teamId === teamId,
          );
          expect(new Set(ids)).toEqual(new Set(played.keys()));
          for (const [id, { on, off }] of played) {
            const stats = match.playerStats[id];
            const own = match.events.filter((e) => e.type !== "substitution" && e.playerId === id);
            expect(stats.minutes).toBe(Math.floor(Math.min(off, 90)) - Math.floor(on));
            expect(stats.goals).toBe(own.filter((e) => e.type === "goal" && !e.ownGoal).length);
            expect(stats.assists).toBe(
              match.events.filter((e) => e.type === "goal" && e.assistId === id).length,
            );
            expect(stats.yellowCards).toBe(
              own.filter((e) => e.type === "card" && e.card === "yellow").length,
            );
            expect(stats.redCards).toBe(
              own.filter((e) => e.type === "card" && e.card !== "yellow").length,
            );
            const [shots, onTarget, goals] = [
              value(stats.shots),
              value(stats.shotsOnTarget),
              value(stats.goals),
            ];
            if (shots !== null && onTarget !== null) {
              expect(onTarget).toBeLessThanOrEqual(shots);
              expect(goals!).toBeLessThanOrEqual(onTarget);
            }
            const [passes, completed] = [value(stats.passes), value(stats.passesCompleted)];
            if (passes !== null && completed !== null)
              expect(completed).toBeLessThanOrEqual(passes);
            const [dribbles, won] = [value(stats.dribbles), value(stats.dribblesWon)];
            if (dribbles !== null && won !== null) expect(won).toBeLessThanOrEqual(dribbles);

            const isKeeper = data.players.find((p) => p.id === id)!.position === "goalkeeper";
            if (isKeeper) {
              expect(stats.goalsConceded).toBe(match.score![side === "home" ? "away" : "home"]);
            } else {
              expect([stats.saves, stats.goalsConceded]).toEqual([
                "not_available",
                "not_available",
              ]);
            }
          }
        }
      }),
    );

    it(
      "has team stats consistent with the events and the players",
      eachFinished((match) => {
        const { home, away } = match.teamStats!;
        expect(value(home.possession)! + value(away.possession)!).toBe(100);
        for (const side of sides) {
          const teamId = teamOf(match, side);
          const stats = match.teamStats![side];
          const cards = match.events.filter((e) => e.type === "card" && e.teamId === teamId);
          expect(stats.yellowCards).toBe(
            cards.filter((e) => e.type === "card" && e.card === "yellow").length,
          );
          expect(stats.redCards).toBe(
            cards.filter((e) => e.type === "card" && e.card !== "yellow").length,
          );
          const rows = Object.entries(match.playerStats)
            .filter(([id]) => squadOf.get(id)!.teamId === teamId)
            .map(([, row]) => row);
          for (const key of ["shots", "shotsOnTarget", "fouls"] as const) {
            const values = rows.map((row) => value(row[key]));
            if (values.every((v) => v !== null)) {
              expect(stats[key], key).toBe(values.reduce((a, b) => a! + b!, 0));
            }
          }
        }
      }),
    );
  });

  it("puts each team's matches on its own side", () => {
    for (const match of finished) {
      for (const id of Object.keys(match.playerStats)) {
        expect([match.homeTeamId, match.awayTeamId]).toContain(squadOf.get(id)!.teamId);
        expect(match.lineups![sideOf(match, squadOf.get(id)!.teamId)]).toBeDefined();
      }
    }
  });
});
