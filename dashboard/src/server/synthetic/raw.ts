/**
 * Physical layout of `data/synthetic`, known only to the synthetic adapter and
 * to its generator (`scripts/synthetic`). It is normalised (ids, no embedded
 * names) and is NOT the API contract: `SyntheticSource` turns it into contract
 * types. Type-only module, so the generator can import it under plain Node.
 */

/** A stored metric: a number, or why it is missing. */
export type RawStat = number | "not_available" | "not_collected";

export type RawPosition = "goalkeeper" | "defender" | "midfielder" | "forward";

/** `season.json` */
export type RawSeasonFile = {
  competition: { id: string; name: string; shortName: string };
  season: { id: string; competitionId: string; name: string; rounds: number };
  teams: {
    id: string;
    name: string;
    shortName: string;
    crestUrl: string | null;
    colors: { primary: string; secondary: string };
  }[];
  squads: { teamId: string; players: { playerId: string; shirtNumber: number | null }[] }[];
};

/** `players.json` */
export type RawPlayer = {
  id: string;
  name: string;
  position: RawPosition | null;
  birthDate: string | null;
  heightCm: number | null;
  preferredFoot: "left" | "right" | "both" | null;
};

export type RawLineupPlayer = {
  playerId: string;
  shirtNumber: number | null;
  /** `[line, order]`, or `null` when unknown or on the bench. */
  slot: [number, number] | null;
};

export type RawLineup = {
  formation: string | null;
  starters: RawLineupPlayer[];
  bench: RawLineupPlayer[];
};

type RawEventBase = {
  id: string;
  teamId: string;
  minute: number;
  addedTime: number | null;
  clip: { clipUrl: string; startSec: number; endSec: number } | null;
};

export type RawEvent =
  | (RawEventBase & {
      type: "goal";
      playerId: string;
      assistId: string | null;
      penalty: boolean;
      /** Counts for the other team; `teamId` stays the player's team. */
      ownGoal: boolean;
    })
  | (RawEventBase & { type: "card"; playerId: string; card: "yellow" | "second_yellow" | "red" })
  | (RawEventBase & { type: "substitution"; playerInId: string; playerOutId: string });

/** Team stat as stored; possession and xG are kept as published. */
export type RawTeamStats = Record<
  | "possession"
  | "shots"
  | "shotsOnTarget"
  | "xg"
  | "corners"
  | "fouls"
  | "yellowCards"
  | "redCards",
  RawStat
>;

/** Player stat as stored: completed passes instead of an accuracy percentage. */
export type RawPlayerStats = Record<
  | "minutes"
  | "goals"
  | "assists"
  | "shots"
  | "shotsOnTarget"
  | "passes"
  | "passesCompleted"
  | "dribbles"
  | "dribblesWon"
  | "recoveries"
  | "possessionLost"
  | "fouls"
  | "yellowCards"
  | "redCards"
  | "saves"
  | "goalsConceded",
  RawStat
>;

export type RawMatch = {
  id: string;
  round: number;
  kickoff: string | null;
  status: "scheduled" | "finished" | "postponed";
  homeTeamId: string;
  awayTeamId: string;
  score: { home: number; away: number } | null;
  lineups: { home: RawLineup; away: RawLineup } | null;
  events: RawEvent[];
  teamStats: { home: RawTeamStats; away: RawTeamStats } | null;
  playerStats: Record<string, RawPlayerStats>;
};

/** `rounds/round-NN.json` */
export type RawRoundFile = { round: number; matches: RawMatch[] };

/** Everything the adapter loads. */
export type RawDataset = {
  season: RawSeasonFile;
  players: RawPlayer[];
  rounds: RawRoundFile[];
};
