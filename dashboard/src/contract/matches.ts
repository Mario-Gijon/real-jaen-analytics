import type { DataProvenance, ObservedTeam, PlayerRef, Stat, TeamRef } from "./common";

export type MatchStatus = "scheduled" | "finished" | "postponed";

export type Side = "home" | "away";

export type Score = { home: number; away: number };

/** One row of a match list. */
export type MatchSummary = {
  id: string;
  seasonId: string;
  round: number;
  /** ISO date-time; `null` when the date is not set (e.g. postponed). */
  kickoff: string | null;
  status: MatchStatus;
  home: TeamRef;
  away: TeamRef;
  /** Final score; `null` unless `status` is `finished`. */
  score: Score | null;
};

/**
 * Place of a starter in the formation drawing: `line` 0 is the goalkeeper,
 * then one line per formation group from defence to attack; `order` goes from
 * left to right (from the team's point of view) starting at 0.
 */
export type LineupSlot = { line: number; order: number };

export type LineupPlayer = {
  player: PlayerRef;
  shirtNumber: number | null;
  /**
   * Where the player stood in this line-up, only when the source says so.
   * Always `null` on the bench, and for starters whose slot is unknown.
   */
  slot: LineupSlot | null;
};

export type Lineup = {
  /** As announced, e.g. "4-2-3-1"; `null` when unknown. */
  formation: string | null;
  starters: LineupPlayer[];
  /** Every substitute named, whether they played or not. */
  bench: LineupPlayer[];
};

/** Optional video clip of an action. Nothing in the app plays it yet. */
export type Clip = { clipUrl: string; startSec: number; endSec: number };

type EventBase = {
  id: string;
  matchId: string;
  /**
   * Team of the player who performs the action. For an own goal this is NOT
   * the team that scores: see `GoalEvent.ownGoal`.
   */
  teamId: string;
  /** Match minute (1–90); added time goes in `addedTime` (45+2 → 45, 2). */
  minute: number;
  addedTime: number | null;
  clip: Clip | null;
};

/**
 * A goal. `player` is who put the ball in the net and `teamId` is their team.
 * The goal counts in the score for `teamId`, unless `ownGoal` is true: then
 * it counts for the opponent. An own goal has no assist, is never a penalty and
 * does not add to the player's `goals` (see {@link scoringSide}).
 */
export type GoalEvent = EventBase & {
  type: "goal";
  player: PlayerRef;
  /** `null` when nobody is credited with the assist (always for an own goal). */
  assist: PlayerRef | null;
  penalty: boolean;
  ownGoal: boolean;
};

/** Side whose score a goal adds to, given the side of the player's team. */
export function scoringSide(goal: Pick<GoalEvent, "ownGoal">, playerSide: Side): Side {
  if (!goal.ownGoal) return playerSide;
  return playerSide === "home" ? "away" : "home";
}

export type CardEvent = EventBase & {
  type: "card";
  player: PlayerRef;
  /**
   * The real card, never simplified: `second_yellow` is a player's second
   * yellow, which sends them off; `red` is a straight red.
   */
  card: "yellow" | "second_yellow" | "red";
};

export type SubstitutionEvent = EventBase & {
  type: "substitution";
  playerIn: PlayerRef;
  playerOut: PlayerRef;
};

/** Ordered by minute, then added time. */
export type MatchEvent = GoalEvent | CardEvent | SubstitutionEvent;

export const TEAM_STAT_KEYS = [
  "possession",
  "shots",
  "shotsOnTarget",
  "xg",
  "corners",
  "fouls",
  "yellowCards",
  "redCards",
] as const;

export type TeamStatKey = (typeof TEAM_STAT_KEYS)[number];

/**
 * `possession` is a percentage (home + away = 100); `xg` has two decimals.
 * `yellowCards` and `redCards` are derived from card events as in
 * {@link PlayerMatchStats}.
 */
export type TeamMatchStats = Record<TeamStatKey, Stat>;

export const PLAYER_STAT_KEYS = [
  "minutes",
  "goals",
  "assists",
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
] as const;

export type PlayerStatKey = (typeof PLAYER_STAT_KEYS)[number];

/**
 * `passAccuracy` is a percentage with one decimal; `fouls` are committed;
 * `saves` and `goalsConceded` are `not_available` for outfield players.
 *
 * `goals` excludes own goals. Cards are derived from card events:
 * `yellowCards` counts `yellow` events and `redCards` counts `second_yellow`
 * and `red` events. A player sent off for two yellows therefore has
 * `yellowCards: 1, redCards: 1`; the event list keeps which card it was.
 */
export type PlayerMatchStats = Record<PlayerStatKey, Stat>;

/** `GET /api/v1/matches/{matchId}`. */
export type MatchDetail = MatchSummary & {
  /** `null` until known (scheduled and postponed matches). */
  lineups: { home: Lineup; away: Lineup } | null;
  events: MatchEvent[];
  teamStats: { home: TeamMatchStats; away: TeamMatchStats } | null;
  /** Players who took part, by player id. Unused substitutes have no entry. */
  playerStats: Record<string, PlayerMatchStats>;
};

export type MatchResult = "win" | "draw" | "loss";

export const OBSERVED_MATCH_STAT_KEYS = [
  "minutes",
  "goals",
  "assists",
  "shots",
  "shotsOnTarget",
  "xg",
  "passesCompleted",
  "passes",
  "passAccuracy",
  "dribbles",
  "dribblesWon",
  "recoveries",
  "possessionLost",
  "duelsWon",
  "duelsLost",
  "fouls",
  "saves",
  "rating",
] as const;

export type ObservedMatchStatKey = (typeof OBSERVED_MATCH_STAT_KEYS)[number];

/**
 * A match as a real source lists it. Ids are derived from the provider's
 * (`sofascore-match-…`, `sofascore-team-…`) and lead to no synthetic match;
 * season, competition and round are the source's, never the app's.
 */
export type ObservedMatch = {
  id: string;
  /** ISO date-time; `null` when unknown. */
  kickoff: string | null;
  /** `null` when the source gives a status the app does not know. */
  status: MatchStatus | null;
  competition: string | null;
  /** Season as the source names it (e.g. "26/27"); never the app's selected season. */
  season: string | null;
  round: number | null;
  home: ObservedTeam;
  away: ObservedTeam;
  /** Final score; `null` unless finished and given (a pending match never reads 0–0). */
  score: Score | null;
};

/**
 * A player in a real match's line-up, in the source's order. No formation,
 * pitch slot, events or individual stats here.
 */
export type ObservedLineupPlayer = {
  /** Id derived from the provider's (`sofascore-player-…`). */
  player: PlayerRef;
  shirtNumber: number | null;
  /** From the source's explicit flag; `null` when it does not say, never guessed. */
  role: "starter" | "substitute" | null;
  /** As in {@link ObservedPlayerMatch}: the same normalization, so both screens agree. */
  participation: "confirmed" | "unused" | "unknown";
  /** As normalized: the source's minutes when the player took part (missing if it gives none), 0 when unused. */
  minutes: Stat;
};

/**
 * A goal as a real source records it. `kind` keeps what the source says:
 * `regular`, `penalty`, `ownGoal` (`scorer` put it in their own net) or
 * `unknown` (a class the app does not know), so they are never all counted
 * as ordinary goals. Penalty shoot-out kicks are not goals here.
 */
export type ObservedGoal = {
  kind: "regular" | "penalty" | "ownGoal" | "unknown";
  minute: number | null;
  addedTime: number | null;
  scorer: PlayerRef | null;
  /** Only when the source names the assistant; never inferred. */
  assist: PlayerRef | null;
};

/** A substitution as a real source records it; nothing is inferred from minutes played. */
export type ObservedSubstitution = {
  minute: number | null;
  addedTime: number | null;
  playerIn: PlayerRef | null;
  playerOut: PlayerRef | null;
};

/**
 * A section of a real match, as the source keeps it: `stored` with content,
 * `empty` (stored without content), `not_stored` (normal for a pending
 * match), `error` (the source could not be queried for it) or `outdated`
 * (read before the match's teams changed: kept as evidence by the service,
 * never served here as the current match's).
 */
export type ObservedSectionState = "stored" | "empty" | "not_stored" | "error" | "outdated";

/** A section of a real match's detail. */
export type ObservedSectionKey = "statistics" | "lineups" | "incidents";

/**
 * Sections read before a correction of the event, pending review: of the
 * score (`score`) or of the teams (`teams`). With `score`, what depends on the
 * score is not used (the incidents' goals and assists; the line-ups' goals and
 * assists in the figures) and the rest is; with `teams`, the section is not
 * used at all (`outdated`). Either way the original stays as evidence.
 */
export type ObservedSectionReview = { corrections: ("score" | "teams")[]; sections: ObservedSectionKey[] };

/**
 * A local decision of this installation, never the source's: an event
 * replaced by another (a postponed match played under a new id; the source
 * does not link them). Both events are kept whole; the replaced one is no
 * longer a current match.
 */
export type LocalReplacement = {
  /** The other event: the current one for a replaced event, the replaced one for a current event. */
  matchId: string;
  reason: string;
  decidedBy: string;
  /** Date of the decision, `YYYY-MM-DD`. */
  decidedOn: string;
};

/**
 * `GET /api/v1/matches/{matchId}/observed`: a real match with the team
 * stats and line-ups the source keeps, each section with its own state.
 * Values the source omits are missing, never 0. `fetchedAt` is when the
 * source was read, not when its content changed. No interpreted events or
 * narrative here.
 */
export type ObservedMatchDetail = {
  match: ObservedMatch;
  /**
   * Present only when a local decision concerns this event: replaced by
   * another one, or replacing others. Never the source's data.
   */
  localReplacement?: { replacedBy: LocalReplacement | null; replaces: LocalReplacement[] };
  /** Present only when some section is pending review after a correction of the event. */
  review?: ObservedSectionReview;
  statistics:
    | { state: "stored"; fetchedAt: string | null; home: TeamMatchStats; away: TeamMatchStats }
    | { state: Exclude<ObservedSectionState, "stored">; fetchedAt: string | null };
  lineups:
    | {
        state: "stored";
        fetchedAt: string | null;
        /** Whether the source marks the line-ups as confirmed; `null` when it does not say. */
        confirmed: boolean | null;
        home: ObservedLineupPlayer[];
        away: ObservedLineupPlayer[];
      }
    | { state: Exclude<ObservedSectionState, "stored">; fetchedAt: string | null };
  /**
   * The goals and substitutions among the source's incidents, enough to mark
   * them on the line-ups. Not a timeline: cards, VAR, periods or missed
   * penalties are left out. An empty list does not prove nothing happened.
   * `goals` is `null` when the incidents were read before a score correction:
   * they do not describe the current score (never an empty list).
   */
  incidents:
    | { state: "stored"; fetchedAt: string | null; goals: ObservedGoal[] | null; substitutions: ObservedSubstitution[] }
    | { state: Exclude<ObservedSectionState, "stored">; fetchedAt: string | null };
  /** What the detail covers, for the analyst, in Spanish. */
  scope: string;
  source: DataProvenance;
};

/** Filters of `GET /api/v1/seasons/{seasonId}/matches` (`?round=&team=`). */
export type MatchFilter = { round?: number; teamId?: string };

/** Availability of the most recently selected narrative-comment capture. */
export type NarrativeCommentsAvailability = "available" | "empty" | "not_collected" | "error";

/** A SofaScore participant reference kept separate from local player identity. */
export type NarrativeCommentParticipant = {
  name: string | null;
  shortName: string | null;
  position: string | null;
  jerseyNumber: string | null;
};

/** One original narrative item, in the provider response's order. */
export type NarrativeComment = {
  id: string;
  position: number;
  text: string;
  type: string;
  minute: number | null;
  periodName: string | null;
  isHome: boolean | null;
  score: { home: number | null; away: number | null } | null;
  incidentId: string | null;
  participants: Partial<Record<"player" | "playerIn" | "playerOut" | "assist1", NarrativeCommentParticipant>>;
};

/** `GET /api/v1/matches/{matchId}/comments`; comments remain outside incidents and sports statistics. */
export type MatchComments = {
  matchId: string;
  availability: NarrativeCommentsAvailability;
  /** Local evidence capture time; the source response does not provide a fetch timestamp. */
  capturedAt: string | null;
  responseSha256: string | null;
  lastAttempt: {
    availability: NarrativeCommentsAvailability;
    occurredAt: string | null;
    httpStatus: number | null;
    errorCode: string | null;
  };
  comments: NarrativeComment[];
};
