/** Observed match details, lineups, statistics, and incidents. */
import { type LocalReplacement, type ObservedGoal, type ObservedLineupPlayer, type ObservedMatchDetail, type ObservedSectionKey, type ObservedSectionReview, type ObservedSubstitution, type PlayerRef, TEAM_STAT_KEYS, type TeamMatchStats, type TeamStatKey } from "@/contract";
import { baseOf, getJson, type Options, UpstreamUnavailableError } from "./http";
import { invalid, isRecord, NOT_AVAILABLE, optionalNumber, optionalText, position, positiveId } from "./validation";
import { matchIdFromSource, playerIdFromSource, sourceMatchId, teamIdFromSource } from "./identifiers";
import { assertInScope, PROVENANCE, sourceScope } from "./scope";
import { type NormalizedCallUp, parseMatch, parseNormalized } from "./matches";

const MATCH_DETAIL_SCOPE =
  "Partido, estadísticas colectivas, alineaciones e incidencias según la fuente. Un dato que la fuente no da se muestra como «—».";

/**
 * The source's key (period `ALL` of `statistics`) behind each team stat. A key
 * the source omits (e.g. `redCards` in most matches) stays missing, never 0.
 */
const TEAM_STAT_FIELDS: Readonly<Record<TeamStatKey, string>> = {
  possession: "ballPossession",
  shots: "totalShotsOnGoal",
  shotsOnTarget: "shotsOnGoal",
  xg: "expectedGoals",
  corners: "cornerKicks",
  fouls: "fouls",
  yellowCards: "yellowCards",
  redCards: "redCards",
};

const SECTION_STATES = new Set(["stored", "empty", "not_stored"]);

/**
 * Team stats from the period `ALL` of a stored `statistics` payload, each
 * from its first item. A missing key or period leaves the value missing.
 */
function parseTeamStats(payload: unknown): { home: TeamMatchStats; away: TeamMatchStats } {
  if (!isRecord(payload) || !Array.isArray(payload.statistics)) throw invalid("statistics");
  const all = payload.statistics.find((period) => isRecord(period) && period.period === "ALL");
  const items = new Map<string, Record<string, unknown>>();
  if (isRecord(all) && Array.isArray(all.groups)) {
    for (const group of all.groups) {
      if (!isRecord(group) || !Array.isArray(group.statisticsItems)) continue;
      for (const item of group.statisticsItems) {
        if (isRecord(item) && typeof item.key === "string" && !items.has(item.key)) items.set(item.key, item);
      }
    }
  }
  const side = (field: "homeValue" | "awayValue") => {
    const stats = {} as TeamMatchStats;
    for (const key of TEAM_STAT_KEYS) {
      const item = items.get(TEAM_STAT_FIELDS[key]);
      const value = item ? optionalNumber(item[field], `statistics.${TEAM_STAT_FIELDS[key]}.${field}`) : null;
      stats[key] = value === null ? NOT_AVAILABLE : { value };
    }
    return stats;
  };
  return { home: side("homeValue"), away: side("awayValue") };
}

/**
 * One side of a stored `lineups` payload, in the source's order. The side is
 * the payload's (`home`/`away`), not each player's `teamId`. The role comes
 * only from the explicit `substitute` flag. Participation and minutes are the
 * service's normalization of each call-up (`normalized`, as in the figures and
 * the profile); a player it does not cover stays unknown. Formation, colours,
 * captain and stats are left out.
 */
function parseLineupSide(
  payload: Record<string, unknown>, side: "home" | "away", normalized: ReadonlyMap<number, NormalizedCallUp>,
): ObservedLineupPlayer[] {
  const team = payload[side];
  if (team === undefined || team === null) return [];
  if (!isRecord(team)) throw invalid(`lineups.${side}`);
  const players = team.players ?? [];
  if (!Array.isArray(players)) throw invalid(`lineups.${side}.players`);
  return players.map((entry, index): ObservedLineupPlayer => {
    const at = (field: string) => `lineups.${side}.players[${index}].${field}`;
    if (!isRecord(entry) || !isRecord(entry.player)) throw invalid(at("player"));
    const id = positiveId(entry.player.id, at("player.id"));
    const name = optionalText(entry.player.name, at("player.name"));
    if (name === null) throw invalid(at("player.name"));
    const shirt = optionalNumber(entry.shirtNumber, at("shirtNumber"));
    if (shirt !== null && (!Number.isInteger(shirt) || shirt < 0)) throw invalid(at("shirtNumber"));
    const substitute = entry.substitute ?? null;
    if (substitute !== null && typeof substitute !== "boolean") throw invalid(at("substitute"));
    const callUp = normalized.get(id);
    return {
      player: { id: playerIdFromSource(id), name, position: position(entry.position ?? entry.player.position, at("position")) },
      shirtNumber: shirt,
      role: substitute === null ? null : substitute ? "substitute" : "starter",
      participation: callUp?.participation ?? "unknown",
      minutes: callUp?.minutes ?? NOT_AVAILABLE,
    };
  });
}

/** The source's goal classes; any other stays `unknown`, never an ordinary goal. */
const GOAL_KINDS: Readonly<Record<string, ObservedGoal["kind"]>> = {
  regular: "regular",
  penalty: "penalty",
  ownGoal: "ownGoal",
};

/** A player named in an incident (`{ id, name }`), or `null` when the incident names none. */
function incidentPlayer(value: unknown, field: string): PlayerRef | null {
  if (value === undefined || value === null) return null;
  if (!isRecord(value)) throw invalid(field);
  const name = optionalText(value.name, `${field}.name`);
  return { id: playerIdFromSource(positiveId(value.id, `${field}.id`)), name: name ?? "Jugador sin nombre", position: null };
}

/**
 * Goals and substitutions of a stored `incidents` payload, as recorded.
 * Other incident types (cards, VAR, periods, missed penalties, a shoot-out)
 * are left out; nothing is recalculated or inferred.
 */
function parseIncidents(payload: unknown): { goals: ObservedGoal[]; substitutions: ObservedSubstitution[] } {
  if (!isRecord(payload) || !Array.isArray(payload.incidents)) throw invalid("incidents");
  const goals: ObservedGoal[] = [];
  const substitutions: ObservedSubstitution[] = [];
  payload.incidents.forEach((incident, index) => {
    const at = (field: string) => `incidents[${index}].${field}`;
    if (!isRecord(incident)) throw invalid(`incidents[${index}]`);
    const when = {
      minute: optionalNumber(incident.time, at("time")),
      addedTime: optionalNumber(incident.addedTime, at("addedTime")),
    };
    if (incident.incidentType === "goal") {
      const kind = optionalText(incident.incidentClass, at("incidentClass"));
      goals.push({
        kind: (kind && GOAL_KINDS[kind]) || "unknown",
        ...when,
        scorer: incidentPlayer(incident.player, at("player")),
        assist: incidentPlayer(incident.assist1, at("assist1")),
      });
    } else if (incident.incidentType === "substitution") {
      substitutions.push({
        ...when,
        playerIn: incidentPlayer(incident.playerIn, at("playerIn")),
        playerOut: incidentPlayer(incident.playerOut, at("playerOut")),
      });
    }
  });
  return { goals, substitutions };
}

/** The service's normalized call-ups of a match (`normalized.players`), by the provider's player id. */
function parseNormalizedPlayers(value: unknown): Map<number, NormalizedCallUp> {
  if (!isRecord(value) || !Array.isArray(value.players)) throw invalid("normalized: falta la normalización del servicio");
  const players = new Map<number, NormalizedCallUp>();
  value.players.forEach((entry, index) => {
    const field = `normalized.players[${index}]`;
    if (!isRecord(entry)) throw invalid(field);
    const id = positiveId(entry.player_id, `${field}.player_id`);
    if (players.has(id)) throw invalid(`${field}.player_id repetido`);
    players.set(id, parseNormalized(entry, field));
  });
  return players;
}

function parseLineups(payload: unknown, body: Record<string, unknown>) {
  if (!isRecord(payload)) throw invalid("lineups");
  const confirmed = payload.confirmed ?? null;
  if (confirmed !== null && typeof confirmed !== "boolean") throw invalid("lineups.confirmed");
  const normalized = parseNormalizedPlayers(body.normalized);
  return { confirmed, home: parseLineupSide(payload, "home", normalized), away: parseLineupSide(payload, "away", normalized) };
}

/** The corrections of the event a section predates (`meta.predates_*`); a flag the service does not send is false. */
function predates(meta: Record<string, unknown>): Set<"score" | "teams"> {
  const found = new Set<"score" | "teams">();
  for (const [field, correction] of [["predates_score_correction", "score"], ["predates_team_change", "teams"]] as const) {
    const flag = meta[field];
    if (flag !== undefined && typeof flag !== "boolean") throw invalid(`meta.${field}`);
    if (flag === true) found.add(correction);
  }
  return found;
}

/**
 * One section of a real match (`statistics`, `lineups`), kept apart from the
 * match: if it cannot be read (the service fails, or answers something
 * unusable) its state is `error` and the rest of the detail is still served.
 * A section read before the teams changed is `outdated`, without content; the
 * corrections it predates are added to `review`.
 */
async function matchSection<T extends object>(
  options: Options,
  sourceId: number,
  section: ObservedSectionKey,
  parse: (data: unknown, body: Record<string, unknown>, corrections: ReadonlySet<"score" | "teams">) => T,
  review: ObservedSectionReview,
): Promise<({ state: "stored"; fetchedAt: string | null } & T) | { state: "empty" | "not_stored" | "error" | "outdated"; fetchedAt: string | null }> {
  try {
    const body = await getJson(options, `${baseOf(options)}/api/v1/matches/${sourceId}/${section}`);
    // The match exists, so a 404 here is an inconsistent answer, not a missing section.
    if (body === null) throw invalid(`${section} del partido ${sourceId}: 404`);
    if (!isRecord(body) || !isRecord(body.meta)) throw invalid("se esperaba { data, meta }");
    const state = body.meta.availability;
    if (typeof state !== "string" || !SECTION_STATES.has(state)) throw invalid("meta.availability");
    const fetchedAt = optionalText(body.meta.fetched_at, "meta.fetched_at");
    const corrections = predates(body.meta);
    if (corrections.size) {
      review.sections.push(section);
      for (const correction of corrections) if (!review.corrections.includes(correction)) review.corrections.push(correction);
    }
    if (corrections.has("teams") && state !== "not_stored") return { state: "outdated", fetchedAt };
    if (state !== "stored") return { state: state as "empty" | "not_stored", fetchedAt };
    return { state: "stored", fetchedAt, ...parse(body.data, body, corrections) };
  } catch (failure) {
    if (!(failure instanceof UpstreamUnavailableError)) throw failure;
    console.error(`[api] ${section} del partido ${sourceId}:`, failure.message);
    return { state: "error", fetchedAt: null };
  }
}

/** One local decision on a match (`decision: "local"`): the other event, the reason and who decided it, when. */
function parseReplacementEntry(value: unknown, field: string): LocalReplacement {
  if (!isRecord(value) || value.decision !== "local") throw invalid(field);
  const text = (key: string) => {
    const found = optionalText(value[key], `${field}.${key}`);
    if (found === null) throw invalid(`${field}.${key}`);
    return found;
  };
  const decidedOn = text("decided_on");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(decidedOn)) throw invalid(`${field}.decided_on`);
  return { matchId: matchIdFromSource(positiveId(value.match_id, `${field}.match_id`)), reason: text("reason"),
    decidedBy: text("decided_by"), decidedOn };
}

/** The local replacement of a match (`replaced_by`, `replaces`), or `undefined` when no local decision concerns it. */
function parseLocalReplacement(data: Record<string, unknown>, at: (field: string) => string): ObservedMatchDetail["localReplacement"] {
  if (data.replaced_by === undefined && data.replaces === undefined) return undefined;
  if (data.replaces !== undefined && !Array.isArray(data.replaces)) throw invalid(at("replaces"));
  const replacedBy = data.replaced_by === undefined ? null : parseReplacementEntry(data.replaced_by, at("replaced_by"));
  const replaces = (data.replaces ?? []).map((entry, i) => parseReplacementEntry(entry, at(`replaces[${i}]`)));
  if (replacedBy !== null && replaces.length) throw invalid(`${at("replaced_by")}: un evento sustituido no sustituye a otro`);
  return { replacedBy, replaces };
}

/**
 * `matchObservedDetail` against the local API: the match with the source's
 * team names, its team stats, line-ups and incidents, one request at a time.
 * `null` for an id not derived from this source (a synthetic id is never
 * read as real) or a match the source does not know.
 */
export function createLocalApiMatchDetail(options: Options) {
  return async (matchId: string): Promise<ObservedMatchDetail | null> => {
    const sourceId = sourceMatchId(matchId);
    if (sourceId === null) return null;
    const scope = await sourceScope(options);
    const body = await getJson(options, `${baseOf(options)}/api/v1/matches/${sourceId}`);
    if (body === null) return null;
    if (!isRecord(body) || !isRecord(body.data)) throw invalid("se esperaba { data: {} }");
    const at = (field: string) => `data.${field}`;
    assertInScope(body.data, scope, at);
    const { homeId, awayId, ...match } = parseMatch(body.data, sourceId, at);
    const review: ObservedSectionReview = { corrections: [], sections: [] };
    const statistics = await matchSection(options, sourceId, "statistics", parseTeamStats, review);
    const lineups = await matchSection(options, sourceId, "lineups", parseLineups, review);
    // Incidents read before a score correction keep their substitutions; their goals do not describe the score.
    const incidents = await matchSection(options, sourceId, "incidents",
      (data, _, corrections) => ({ ...parseIncidents(data), ...(corrections.has("score") ? { goals: null } : {}) }), review);
    const localReplacement = parseLocalReplacement(body.data, at);
    return {
      match: {
        ...match,
        home: { id: teamIdFromSource(homeId), name: optionalText(body.data.home_team_name, at("home_team_name")) },
        away: { id: teamIdFromSource(awayId), name: optionalText(body.data.away_team_name, at("away_team_name")) },
      },
      ...(localReplacement ? { localReplacement } : {}),
      ...(review.sections.length ? { review } : {}),
      statistics,
      lineups,
      incidents,
      scope: MATCH_DETAIL_SCOPE,
      source: PROVENANCE,
    };
  };
}
