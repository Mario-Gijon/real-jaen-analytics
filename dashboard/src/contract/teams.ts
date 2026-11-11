import type { DataProvenance, ObservedTeam, PlayerRef, PreferredFoot, SeasonStat, TeamRef } from "./common";
import type { ObservedCompetition, Season } from "./competitions";
import type { MatchResult, MatchSummary, ObservedMatch, Side, TeamMatchStats, TeamStatKey } from "./matches";
import type { SquadPlayer } from "./players";

/** Table record, derived from finished matches only. */
export type TeamRecord = {
  played: number;
  wins: number;
  draws: number;
  losses: number;
  goalsFor: number;
  goalsAgainst: number;
  points: number;
};

/** `GET /api/v1/seasons/{seasonId}/teams` returns `TeamStanding[]`, by rank. */
export type TeamStanding = { rank: number; team: TeamRef; record: TeamRecord };

/** One of a team's matches, seen from that team. */
export type TeamMatch = {
  match: MatchSummary;
  side: Side;
  opponent: TeamRef;
  /** `null` unless the match is finished. */
  result: MatchResult | null;
  /**
   * Team stats of that match from this team's side: `own` is what the team
   * did, `opponent` what its rival did (so, what the team conceded).
   * `null` unless the match is finished.
   */
  teamStats: { own: TeamMatchStats; opponent: TeamMatchStats } | null;
};

export type TeamSeasonStats = Record<TeamStatKey, SeasonStat>;

/** `GET /api/v1/seasons/{seasonId}/teams/{teamId}`. */
export type TeamSeason = {
  season: Season;
  team: TeamRef;
  standing: TeamStanding;
  /** Whole calendar, by round. */
  matches: TeamMatch[];
  stats: TeamSeasonStats;
  squad: SquadPlayer[];
};

/**
 * `GET /api/v1/teams/{teamId}/players`: players seen with a team. It is not a
 * full nor necessarily current squad and it belongs to no season; `scope`
 * says what the list covers. A real source offers no stats nor personal data
 * here, so none appear.
 *
 * Player ids from a real source are derived from the provider's own ids
 * (e.g. `sofascore-player-966303`): stable while that provider is used, but
 * not an identity independent of it. Screens treat them as opaque.
 */
export type ObservedPlayers = {
  teamId: string;
  players: PlayerRef[];
  /** What the list covers, for the analyst, in Spanish. */
  scope: string;
  source: DataProvenance;
};

/** What «Equipos → Jugadores» accumulates for an observed player. */
export const OBSERVED_SQUAD_STATS = ["appearances", "starts", "minutes", "goals", "assists"] as const;

export type ObservedSquadStat = (typeof OBSERVED_SQUAD_STATS)[number];

/**
 * An observed player's figures in one competition of a real source, over
 * their call-ups there (`of`), from the service's normalized call-ups:
 * `starts` from the explicit line-up flag, `appearances` the call-ups in
 * which the player took part out of those whose participation is known
 * (`matches`: played or unused), and minutes, goals and assists summed over
 * the call-ups that have them (an unused substitute adds 0). A partial
 * figure is not a total.
 */
export type ObservedSquadPlayer = {
  player: PlayerRef;
  heightCm: number | null;
  preferredFoot: PreferredFoot | null;
  /** Shirt number in the player's most recent dated call-up here: historical, not necessarily the current one. */
  lastShirtNumber: number | null;
  /** Counted matches in which the source lists the player in this team's call-up. */
  callUps: number;
  stats: Record<ObservedSquadStat, SeasonStat>;
};

/**
 * Observed players of a team in one competition of a real source's scope,
 * over the same counted matches as its results. Not a full nor necessarily
 * current squad.
 */
export type ObservedSquadCompetition = {
  /** Same id as the competition in {@link ObservedCompetitionPerformance}. */
  id: string;
  name: string;
  season: string | null;
  /** Counted matches, and how many of them list any call-up of the team. */
  matches: { counted: number; withCallUps: number };
  /**
   * Call-ups (one per player and match) by participation, and the quality
   * issues: participation contradictions, goals or assists that contradict
   * the incidents, a team id that differs from the line-up side (counted for
   * the side's team), merged duplicates and discarded records.
   */
  callUps: {
    total: number; played: number; unused: number; unknown: number; contradictions: number; mismatches: number;
    teamWarnings: number; duplicateRecords: number; discardedRecords: number;
  };
  players: ObservedSquadPlayer[];
};

/** `GET /api/v1/teams/{teamId}/observed-squad`: per competition of the source's scope. */
export type ObservedSquad = {
  teamId: string;
  competitions: ObservedSquadCompetition[];
  /** What the figures cover, for the analyst, in Spanish. */
  scope: string;
  source: DataProvenance;
};

export type ObservedTeamMatches = {
  teamId: string;
  matches: { match: ObservedMatch; side: Side }[];
  /** The competitions the source serves, whether or not they have matches here. */
  competitions: ObservedCompetition[];
  /** What the list covers, for the analyst, in Spanish. */
  scope: string;
  source: DataProvenance;
};

/** What a team produces and concedes per match, as «Rendimiento» shows it; `…Against` is what its rivals did. */
export const TEAM_OUTPUT_INDICATORS = [
  "goals", "xg", "shots", "shotsOnTarget",
  "goalsAgainst", "xgAgainst", "shotsAgainst", "shotsOnTargetAgainst",
  "possession", "corners",
] as const;

export type TeamOutputIndicator = (typeof TEAM_OUTPUT_INDICATORS)[number];

/**
 * Per-match averages over a team's counted matches: each one over the
 * matches that have the value (`matches` of `of`), `null` when none has it
 * (never 0). Possession is the arithmetic mean of the percentages.
 */
export type ObservedTeamOutput = {
  season: Record<TeamOutputIndicator, SeasonStat>;
  /**
   * The three most recent counted matches with a date (oldest first), and
   * the averages over them: chosen first, then each value's coverage.
   */
  last3: { matchIds: string[]; values: Record<TeamOutputIndicator, SeasonStat> };
  /** Counted matches without a date: in the season averages, never in «Últimos 3». */
  undated: number;
};

/**
 * Results of a team in one competition of a real source's scope, from its
 * finished matches with a valid score only: the observed sample, not
 * necessarily the whole season. There is no table position nor points.
 */
export type ObservedCompetitionPerformance = {
  /**
   * Our id for one competition group in one season, derived from the
   * provider's ids: `sofascore-scope-{tournamentId}-{seasonId}`. Stable while
   * that source is used; never related to a synthetic season.
   */
  id: string;
  name: string;
  season: string | null;
  /**
   * The team's matches in this competition by state. Only `counted` ones
   * (finished with a valid score) make the record; a finished match without
   * one is reported here, never read as 0–0, and so is one whose stored
   * versions contradict each other (`excludedByVersionConflict`).
   * `inScope` counts the current matches; an event replaced by another by a
   * local decision (`LocalReplacement`) is kept but counted apart
   * (`replacedLocally`), neither pending nor current.
   */
  matches: {
    inScope: number; finished: number; pending: number; counted: number; finishedWithoutValidScore: number;
    excludedByVersionConflict: number; unknownStatus: number; replacedLocally: number;
  };
  record: { played: number; wins: number; draws: number; losses: number; goalsFor: number; goalsAgainst: number; goalDifference: number };
  venues: Record<Side, Record<MatchResult, number>>;
  /** The counted matches, oldest first, seen from the team. xG is null when absent or inconsistent at source. */
  results: {
    match: ObservedMatch; side: Side; result: MatchResult; goalsFor: number; goalsAgainst: number;
    xgFor: number | null; xgAgainst: number | null;
  }[];
  /** Production and concession over the same matches; `null` when the source's figures could not be read. */
  output: ObservedTeamOutput | null;
};

/** `GET /api/v1/teams/{teamId}/observed-performance`. */
export type ObservedTeamPerformance = {
  teamId: string;
  /** One per competition of the source's scope, in the source's order. */
  competitions: ObservedCompetitionPerformance[];
  /** Teams with an explicit correspondence in this source: the ones that can be analysed. */
  teams: ObservedTeam[];
  /** What the figures cover, for the analyst, in Spanish. */
  scope: string;
  source: DataProvenance;
};
