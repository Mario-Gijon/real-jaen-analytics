import type { DataProvenance, PlayerPosition, PlayerRef, PreferredFoot, SeasonStat, Stat, TeamRef } from "./common";
import type { Season } from "./competitions";
import type { MatchEvent, MatchSummary, ObservedMatch, ObservedMatchStatKey, PlayerMatchStats, PlayerStatKey, Side } from "./matches";

/** Personal data. Unknown attributes are `null`, never invented. */
export type Player = PlayerRef & {
  /** ISO date `YYYY-MM-DD`. */
  birthDate: string | null;
  heightCm: number | null;
  preferredFoot: PreferredFoot | null;
};

export type PlayerSeasonStats = Record<PlayerStatKey | "appearances" | "starts", SeasonStat>;

/** A squad member with the season accumulated for this team. */
export type SquadPlayer = {
  player: PlayerRef;
  shirtNumber: number | null;
  stats: PlayerSeasonStats;
};

/** One match of a player: what they did and the actions that name them. */
export type PlayerMatch = {
  match: MatchSummary;
  side: Side;
  started: boolean;
  stats: PlayerMatchStats;
  /** Goals, assists, cards and substitutions involving the player. */
  events: MatchEvent[];
};

/** `GET /api/v1/players/{playerId}?season={seasonId}`. */
export type PlayerSeason = {
  season: Season;
  player: Player;
  team: TeamRef;
  shirtNumber: number | null;
  stats: PlayerSeasonStats;
  /** Matches the player took part in, by round. */
  matches: PlayerMatch[];
};

/**
 * `GET /api/v1/players/{playerId}/profile`: a player's personal data only, as
 * a real source gives them. It belongs to no season and claims no current
 * team: there are no stats, shirt number or team here. Unknown attributes are
 * `null`, never filled in. `scope` says what the profile covers.
 */
export type PlayerProfile = {
  player: Player & {
    /** Country the source associates with the player, as the source names it. */
    country: string | null;
  };
  /** What the profile covers, for the analyst, in Spanish. */
  scope: string;
  source: DataProvenance;
};

/** One match in which a real source lists the player in a squad: a call-up, not necessarily an appearance. */
export type ObservedPlayerMatch = {
  match: ObservedMatch;
  /** Side of the player's team. */
  side: Side;
  /** Named as starter or substitute; `null` when unknown. */
  role: "starter" | "substitute" | null;
  /** Position in this match's line-up, as the source gives it; not the player's usual position. */
  position: PlayerPosition | null;
  /**
   * As the data service normalizes the call-up with line-ups, minutes and
   * incidents: `confirmed` when the player took part; `unused` for a
   * substitute who did not come on (confirmed line-up and substitutions that
   * account for every substitute used); `unknown` when the evidence is not
   * enough or contradicts itself: never "did not play".
   */
  participation: "confirmed" | "unused" | "unknown";
  /**
   * `minutes`, `goals` and `assists` are the service's normalization: the
   * source's value, or 0 when it omits a goal or assist in an appearance
   * with full individual stats (a rule for this source, checked against the
   * incidents; a contradiction stays missing), or 0 for an unused
   * substitute. Minutes are never derived. Every other value is the
   * service's normalization too: the source's value, or one fixed by a
   * documented relation with values the source does give (`derivation`); a
   * value it omits without such a relation is missing, never 0, and figures
   * that contradict each other are `inconsistent`. Without confirmed
   * participation every other value is missing. `xg` keeps its decimals;
   * `rating` is the provider's own rating (see `ratingSource`). `fouls` are
   * committed; `duelsWon`/`duelsLost` include aerial duels; `dribbles` are
   * attempted and `dribblesWon` successful. `passAccuracy` is a percentage
   * (`calculated`), only when both pass figures are known and some pass was
   * attempted.
   */
  stats: Record<ObservedMatchStatKey, Stat>;
};

/** `GET /api/v1/players/{playerId}/observed-matches`. */
export type ObservedPlayerMatches = {
  playerId: string;
  /** Newest first. */
  matches: ObservedPlayerMatch[];
  /** What the list covers, for the analyst, in Spanish. */
  scope: string;
  /** Who computes `stats.rating`, to label it (e.g. "SofaScore"). */
  ratingSource: string;
  source: DataProvenance;
};

/**
 * `GET /api/v1/teams/{teamId}/observed-matches`: a team's matches as a real
 * source lists them, by kickoff (undated last). Not necessarily a complete
 * calendar; `scope` says so.
 */
