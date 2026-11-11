/** Match parsing, normalized player appearances, and match listings. */
import { type MatchStatus, type MissingReason, OBSERVED_MATCH_STAT_KEYS, type ObservedMatchStatKey, type ObservedPlayerMatch, type ObservedPlayerMatches, type ObservedTeamMatches, type Stat, type StatDerivation } from "@/contract";
import { baseOf, getJson, type Options } from "./http";
import { count, decimal, invalid, isRecord, NOT_AVAILABLE, optionalNumber, optionalText, position, positiveId } from "./validation";
import { matchIdFromSource, SOURCE_TEAM_IDS, sourcePlayerId } from "./identifiers";
import { assertInScope, PROVENANCE, type SourceCompetition, sourceScope } from "./scope";
import { type RawMatch, withTeamNames } from "./cache";

const MATCHES_SCOPE =
  "Partidos en los que la fuente registra al jugador en la convocatoria. No garantiza cobertura completa ni indica equipo actual. Sin participación confirmada no se muestran minutos ni estadísticas.";

const TEAM_MATCHES_SCOPE =
  "Partidos del equipo que registra la fuente. No es necesariamente un calendario completo.";

/** The provider behind `rating`, named so the analyst knows whose rating it is. */
const RATING_SOURCE = "SofaScore";

/** Source match statuses the app knows; anything else is unknown. */
const STATUSES: Readonly<Record<string, MatchStatus>> = {
  finished: "finished",
  notstarted: "scheduled",
  postponed: "postponed",
};

/** Each per-match stat in the service's normalization of a call-up (`normalized.stats`). */
const NORMALIZED_STAT_FIELDS: Readonly<Record<Exclude<ObservedMatchStatKey, "minutes" | "goals" | "assists">, string>> = {
  shots: "shots",
  shotsOnTarget: "shots_on_target",
  xg: "xg",
  passesCompleted: "passes_accurate",
  passes: "passes",
  passAccuracy: "pass_accuracy",
  dribbles: "dribbles",
  dribblesWon: "dribbles_won",
  recoveries: "ball_recoveries",
  possessionLost: "possession_lost",
  duelsWon: "duels_won",
  duelsLost: "duels_lost",
  fouls: "fouls",
  saves: "saves",
  rating: "rating",
};
/** Stats that are not counts: any finite non-negative number. */
const DECIMAL_STATS: ReadonlySet<ObservedMatchStatKey> = new Set(["xg", "passAccuracy", "rating"]);

/**
 * Why the service has no value, as the interface tells it apart: what the
 * source does not give (or gives as null, or does not apply) is unavailable;
 * figures that contradict each other, disagreeing versions or duplicates and
 * values out of domain keep their own reason.
 */
const NORMALIZED_REASONS: Readonly<Record<string, MissingReason>> = {
  not_reported: "not_available",
  reported_null: "not_available",
  participation_unknown: "not_available",
  not_applicable: "not_available",
  inconsistent: "inconsistent",
  version_conflict: "conflicting",
  duplicate_disagree: "conflicting",
  invalid: "invalid",
  // A rating from line-ups read before a score correction: pending a valid re-read, never the current match's.
  before_score_correction: "not_collected",
};
type RawObservedMatch = Omit<ObservedPlayerMatch, "match"> & { match: RawMatch };

/**
 * A source match object (in a player's call-ups or a team's matches). The
 * score is kept only for a finished match, so a pending one never reads 0–0;
 * an unknown status stays unknown.
 */
export function parseMatch(match: Record<string, unknown>, id: number, at: (field: string) => string): RawMatch {
  if (match.match_id !== undefined && match.match_id !== id) throw invalid(at("match_id"));
  const kickoff = optionalText(match.kickoff_utc, at("kickoff_utc"));
  if (kickoff !== null && Number.isNaN(Date.parse(kickoff))) throw invalid(at("kickoff_utc"));
  const status = optionalText(match.status, at("status"));
  const known = (status && STATUSES[status]) || null;
  const round = optionalNumber(match.round, at("round"));
  const homeScore = optionalNumber(match.home_score, at("home_score"));
  const awayScore = optionalNumber(match.away_score, at("away_score"));
  return {
    id: matchIdFromSource(id),
    kickoff,
    status: known,
    competition: optionalText(match.competition, at("competition")),
    season: optionalText(match.season, at("season")),
    round: round !== null && Number.isInteger(round) ? round : null,
    homeId: positiveId(match.home_team_id, at("home_team_id")),
    awayId: positiveId(match.away_team_id, at("away_team_id")),
    score:
      known === "finished" && homeScore !== null && awayScore !== null
        ? { home: homeScore, away: awayScore }
        : null,
  };
}

/** The service's participation of a call-up. */
const PARTICIPATIONS: Readonly<Record<string, ObservedPlayerMatch["participation"]>> = {
  played: "confirmed",
  unused: "unused",
  unknown: "unknown",
};
const NORMALIZED_SOURCES = new Set(["reported", "omitted_zero", "unused"]);

/** A call-up as the service normalizes it: participation, and minutes, goals and assists with their origin. */
export type NormalizedCallUp = { participation: ObservedPlayerMatch["participation"]; minutes: Stat; goals: Stat; assists: Stat };

/** One normalized figure, `{ value, source }`: a non-negative integer with its origin, or missing with none. */
function normalizedFigure(value: unknown, field: string): Stat {
  if (!isRecord(value)) throw invalid(field);
  if (value.value === null) {
    if (value.source !== null) throw invalid(`${field}.source`);
    return NOT_AVAILABLE;
  }
  if (typeof value.source !== "string" || !NORMALIZED_SOURCES.has(value.source)) throw invalid(`${field}.source`);
  return { value: count(value.value, `${field}.value`) };
}

/**
 * The service's normalization of one call-up (`normalized`): the source's
 * rules live there, once, for figures, profile and line-ups. This only checks
 * it holds: an unused substitute has 0 of everything, an unknown call-up
 * nothing.
 */
export function parseNormalized(value: unknown, field: string): NormalizedCallUp {
  if (!isRecord(value)) throw invalid(`${field}: falta la normalización del servicio`);
  const participation = typeof value.participation === "string" ? PARTICIPATIONS[value.participation] : undefined;
  if (!participation) throw invalid(`${field}.participation`);
  const [minutes, goals, assists] = (["minutes", "goals", "assists"] as const).map((key) => normalizedFigure(value[key], `${field}.${key}`));
  const figures = [minutes, goals, assists].map((stat) => stat.value);
  if (participation === "unused" && figures.some((figure) => figure !== 0)) throw invalid(`${field}: un no utilizado suma 0`);
  if (participation === "unknown" && figures.some((figure) => figure !== null)) throw invalid(`${field}: sin participación no hay cifras`);
  return { participation, minutes, goals, assists };
}

/** The only derivation the service may give each stat; any other is an unusable answer. */
const STAT_DERIVATIONS: Partial<Record<ObservedMatchStatKey, StatDerivation>> = {
  shotsOnTarget: { source: "derived", rule: "shots_partition" },
  passAccuracy: { source: "calculated", rule: "ratio" },
};

/**
 * One stat of `normalized.stats`, `{ value, source, rule, reason }`: the
 * source's value (`reported`, or 0 of an `unused` substitute), one the
 * service fixed by a documented rule (`derived`, `calculated`), or none with
 * its reason. The rules are the service's; this only checks the answer holds.
 */
function normalizedStat(value: unknown, key: ObservedMatchStatKey, field: string): Stat {
  if (!isRecord(value)) throw invalid(field);
  if (value.value === null) {
    const reason = typeof value.reason === "string" ? NORMALIZED_REASONS[value.reason] : undefined;
    if (!reason || value.source !== null || value.rule !== null) throw invalid(`${field}.reason`);
    return { value: null, reason };
  }
  if (value.reason !== null) throw invalid(`${field}.reason`);
  const number = DECIMAL_STATS.has(key) ? decimal(value.value, `${field}.value`) : count(value.value, `${field}.value`);
  if ((value.source === "reported" || value.source === "unused") && value.rule === null) return { value: number };
  const derivation = STAT_DERIVATIONS[key];
  if (!derivation || value.source !== derivation.source || value.rule !== derivation.rule) throw invalid(`${field}.source`);
  return { value: number, derivation };
}

/**
 * One call-up row, with the service's normalization: participation, minutes,
 * goals and assists (an unused substitute has 0 of each) and every other
 * stat (`normalized.stats`) come from it, so the service's rules are never
 * repeated here. Only a confirmed appearance shows stats.
 */
function parseMatchRow(row: unknown, index: number, playerSourceId: number, scope: readonly SourceCompetition[]): RawObservedMatch {
  const at = (field: string) => `data[${index}].${field}`;
  if (!isRecord(row) || !isRecord(row.match)) throw invalid(`data[${index}]`);
  if (row.player_id !== playerSourceId) throw invalid(at("player_id"));
  const id = positiveId(row.match_id, at("match_id"));
  if (row.side !== "home" && row.side !== "away") throw invalid(at("side"));
  for (const flag of ["substitute", "played"] as const) {
    const value = row[flag];
    if (value !== undefined && value !== null && typeof value !== "boolean") throw invalid(at(flag));
  }

  assertInScope(row.match, scope, (field) => at(`match.${field}`));
  const match = parseMatch(row.match, id, (field) => at(`match.${field}`));

  const normalized = parseNormalized(row.normalized, at("normalized"));
  const confirmed = normalized.participation === "confirmed";
  const served = isRecord(row.normalized) ? row.normalized.stats : undefined;
  if (!isRecord(served)) throw invalid(`${at("normalized.stats")}: falta la normalización del servicio`);
  const stats = {} as Record<ObservedMatchStatKey, Stat>;
  for (const key of OBSERVED_MATCH_STAT_KEYS) {
    if (key === "minutes" || key === "goals" || key === "assists") {
      stats[key] = normalized[key];
      continue;
    }
    const field = NORMALIZED_STAT_FIELDS[key];
    const stat = normalizedStat(served[field], key, at(`normalized.stats.${field}`));
    if (normalized.participation === "unknown" && stat.value !== null) throw invalid(`${at("normalized.stats")}: sin participación no hay cifras`);
    stats[key] = confirmed ? stat : NOT_AVAILABLE;
  }

  return {
    match,
    side: row.side,
    role: row.substitute === true ? "substitute" : row.substitute === false ? "starter" : null,
    position: position(row.match_position, at("match_position")),
    participation: normalized.participation,
    stats,
  };
}

/** By kickoff (newest first when `newest`); matches without a date always go last. */
function byKickoff(newest: boolean) {
  return (a: { match: RawMatch }, b: { match: RawMatch }): number => {
    if (a.match.kickoff === b.match.kickoff) return a.match.id.localeCompare(b.match.id);
    if (a.match.kickoff === null) return 1;
    if (b.match.kickoff === null) return -1;
    const order = Date.parse(a.match.kickoff) - Date.parse(b.match.kickoff);
    return newest ? -order : order;
  };
}

/**
 * `playerObservedMatches` against the local API. `null` for an id that is not
 * one of ours derived from this source, or a player the source does not know;
 * an empty list when it knows the player but has no matches.
 */
export function createLocalApiObservedMatches(options: Options) {
  return async (playerId: string): Promise<ObservedPlayerMatches | null> => {
    const playerSourceId = sourcePlayerId(playerId);
    if (playerSourceId === null) return null;
    const scope = await sourceScope(options);
    const body = await getJson(options, `${baseOf(options)}/api/v1/players/${playerSourceId}/matches`);
    if (body === null) return null;
    if (!isRecord(body) || !Array.isArray(body.data)) throw invalid("se esperaba { data: [] }");
    const rows = body.data.map((row, index) => parseMatchRow(row, index, playerSourceId, scope));
    const matches = await withTeamNames(options, rows.sort(byKickoff(true)));
    return { playerId, matches, scope: MATCHES_SCOPE, ratingSource: RATING_SOURCE, source: PROVENANCE };
  };
}

/**
 * `teamObservedMatches` against the local API: the team's matches by kickoff
 * (undated last). `null` when the team has no explicit correspondence in the
 * source or the source does not know it; an empty list when it has none.
 */
export function createLocalApiTeamMatches(options: Options) {
  return async (teamId: string): Promise<ObservedTeamMatches | null> => {
    const teamSourceId = SOURCE_TEAM_IDS[teamId];
    if (teamSourceId === undefined) return null;
    const scope = await sourceScope(options);
    const body = await getJson(options, `${baseOf(options)}/api/v1/teams/${teamSourceId}/matches`);
    if (body === null) return null;
    if (!isRecord(body) || !Array.isArray(body.data)) throw invalid("se esperaba { data: [] }");
    const rows = body.data.map((row, index) => {
      const at = (field: string) => `data[${index}].${field}`;
      if (!isRecord(row)) throw invalid(`data[${index}]`);
      assertInScope(row, scope, at);
      const match = parseMatch(row, positiveId(row.match_id, at("match_id")), at);
      const side = match.homeId === teamSourceId ? "home" : match.awayId === teamSourceId ? "away" : null;
      if (side === null) throw invalid(`${at("match_id")}: el equipo no juega este partido`);
      return { match, side } as const;
    });
    const matches = await withTeamNames(options, rows.sort(byKickoff(false)));
    const competitions = scope.map(({ name, season }) => ({ name, season }));
    return { teamId, matches, competitions, scope: TEAM_MATCHES_SCOPE, source: PROVENANCE };
  };
}
