import type {
  Competition,
  MatchStatus,
  PlayerPosition,
  PlayerSeason,
  PlayerSeasonStats,
  SeasonStat,
  SquadPlayer,
  TeamMatch,
  TeamMatchStats,
  TeamRef,
  TeamSeason,
  TeamStanding,
} from "@/contract";

/** Small hand-made contract payloads for the Equipos tests. */
export const SEASON_ID = "season-test";

const team = (
  id: string,
  name: string,
  shortName: string,
  crestUrl: string | null = null,
): TeamRef => ({
  id,
  name,
  shortName,
  crestUrl,
  colors: { primary: "#123456", secondary: "#ffffff" },
});

export const RJ = team("team-real-jaen", "Real Jaén CF", "Real Jaén", "/crests/real-jaen.webp");
export const ALTO = team("team-alto", "CD Montealto", "Montealto");
export const VEGA = team("team-vega", "UD Vegaclara", "Vegaclara");
export const BRAVO = team("team-bravo", "Atlético Bravo", "Bravo");

export const competitions: Competition[] = [
  {
    id: "competition-test",
    name: "Liga de Prueba",
    shortName: "Liga Prueba",
    seasons: [
      {
        id: SEASON_ID,
        competitionId: "competition-test",
        name: "2026/27",
        rounds: 6,
        currentRound: 3,
      },
    ],
  },
];

const record = (w: number, d: number, l: number, gf: number, ga: number) => ({
  played: w + d + l,
  wins: w,
  draws: d,
  losses: l,
  goalsFor: gf,
  goalsAgainst: ga,
  points: w * 3 + d,
});

export const standings: TeamStanding[] = [
  { rank: 1, team: ALTO, record: record(2, 1, 0, 5, 2) },
  { rank: 2, team: RJ, record: record(1, 2, 1, 4, 4) },
  { rank: 3, team: VEGA, record: record(1, 0, 2, 3, 4) },
  { rank: 4, team: BRAVO, record: record(0, 2, 1, 2, 4) },
];

export const full = (value: number, of = 3): SeasonStat => ({
  value,
  coverage: "complete",
  matches: of,
  of,
});
const none = (of = 3): SeasonStat => ({
  value: null,
  coverage: "none",
  reason: "not_available",
  matches: 0,
  of,
});

function stats(
  appearances: number,
  starts: number,
  minutes: number,
  goals: number,
  assists: number,
): PlayerSeasonStats {
  const of = appearances;
  const base = {
    appearances: full(appearances, of),
    starts: full(starts, of),
    minutes: full(minutes, of),
    goals: full(goals, of),
    assists: full(assists, of),
  };
  const rest = Object.fromEntries(
    [
      "shots",
      "shotsOnTarget",
      "passes",
      "passAccuracy",
      "dribbles",
      "dribblesWon",
      "recoveries",
      "possessionLost",
      "fouls",
      "yellowCards",
      "redCards",
      "saves",
      "goalsConceded",
    ].map((key) => [key, full(0, of)]),
  );
  return { ...rest, ...base } as PlayerSeasonStats;
}

const member = (
  id: string,
  name: string,
  position: PlayerPosition,
  shirtNumber: number,
  seasonStats: PlayerSeasonStats,
): SquadPlayer => ({ player: { id, name, position }, shirtNumber, stats: seasonStats });

export const squad: SquadPlayer[] = [
  member("p-keeper", "Ángel Portero", "goalkeeper", 1, stats(3, 3, 270, 0, 0)),
  member("p-reserve", "Tomás Reserva", "goalkeeper", 13, stats(0, 0, 0, 0, 0)),
  member("p-back", "Lucas Defensa", "defender", 4, stats(3, 3, 270, 0, 1)),
  member("p-mid", "Mario Medio", "midfielder", 8, stats(3, 2, 200, 1, 2)),
  member("p-nine", "Diego Goleador", "forward", 9, stats(3, 3, 250, 3, 0)),
  {
    ...member("p-academy", "Iker Cantera", "midfielder", 28, stats(2, 0, 30, 0, 0)),
    stats: { ...stats(2, 0, 30, 0, 0), assists: none(2) },
  },
];

const summary = (
  round: number,
  home: TeamRef,
  away: TeamRef,
  status: MatchStatus,
  score: [number, number] | null,
) => ({
  id: `m-${round}`,
  seasonId: SEASON_ID,
  round,
  kickoff:
    status === "postponed" ? null : `2026-08-${String(20 + round).padStart(2, "0")}T18:00:00Z`,
  status,
  home,
  away,
  score: score && { home: score[0], away: score[1] },
});

/** Team stats of one side of a match; `null` xG means not collected. */
const side = (
  possession: number,
  shots: number,
  shotsOnTarget: number,
  xg: number | null,
  corners: number,
): TeamMatchStats => ({
  possession: { value: possession },
  shots: { value: shots },
  shotsOnTarget: { value: shotsOnTarget },
  xg: xg === null ? { value: null, reason: "not_collected" } : { value: xg },
  corners: { value: corners },
  fouls: { value: 11 },
  yellowCards: { value: 2 },
  redCards: { value: 0 },
});

const rjMatch = (
  round: number,
  venue: "home" | "away",
  opponent: TeamRef,
  status: MatchStatus,
  score: [number, number] | null,
  result: TeamMatch["result"],
  teamStats: TeamMatch["teamStats"] = null,
): TeamMatch => ({
  match: summary(
    round,
    venue === "home" ? RJ : opponent,
    venue === "home" ? opponent : RJ,
    status,
    score,
  ),
  side: venue,
  opponent,
  result,
  teamStats,
});

/**
 * Real Jaén: four finished matches (rounds 1, 2, 3 and 6), round 4 postponed
 * and round 5 scheduled, so «last 3» is rounds 2, 3 and 6 whatever
 * `currentRound` says. Round 2 has no xG.
 */
export const rjSeason: TeamSeason = {
  season: competitions[0].seasons[0],
  team: RJ,
  standing: standings[1],
  matches: [
    rjMatch(1, "home", ALTO, "finished", [2, 1], "win", {
      own: side(55, 12, 5, 1.5, 6),
      opponent: side(45, 8, 3, 0.9, 3),
    }),
    rjMatch(2, "away", VEGA, "finished", [1, 1], "draw", {
      own: side(48, 10, 4, null, 4),
      opponent: side(52, 11, 4, null, 5),
    }),
    rjMatch(3, "home", BRAVO, "finished", [1, 2], "loss", {
      own: side(60, 14, 6, 1.2, 7),
      opponent: side(40, 9, 5, 1.6, 2),
    }),
    rjMatch(4, "away", ALTO, "postponed", null, null),
    rjMatch(5, "home", VEGA, "scheduled", null, null),
    rjMatch(6, "away", BRAVO, "finished", [0, 0], "draw", {
      own: side(49, 8, 3, 0.7, 3),
      opponent: side(51, 10, 4, 1.1, 4),
    }),
  ],
  stats: {
    possession: full(52.4),
    shots: full(31),
    shotsOnTarget: full(12),
    xg: { value: 3.1, coverage: "partial", matches: 2, of: 3 },
    corners: full(14),
    fouls: none(),
    yellowCards: full(5),
    redCards: full(0),
  },
  squad,
};

export const altoSeason: TeamSeason = {
  ...rjSeason,
  team: ALTO,
  standing: standings[0],
  matches: [rjMatch(1, "away", RJ, "finished", [2, 1], "loss")].map((m) => ({
    ...m,
    opponent: RJ,
  })),
  squad: [member("p-alto", "Pablo Alto", "forward", 10, stats(3, 3, 270, 2, 0))],
};

export function playerProfile(row: SquadPlayer): PlayerSeason {
  const unknown = row.player.id === "p-back";
  return {
    season: competitions[0].seasons[0],
    player: {
      ...row.player,
      birthDate: unknown ? null : "2000-05-10",
      heightCm: unknown ? null : 181,
      preferredFoot: unknown ? null : "left",
    },
    team: RJ,
    shirtNumber: row.shirtNumber,
    stats: row.stats,
    matches: [],
  };
}
