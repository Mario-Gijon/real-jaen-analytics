/** Player profiles, observed players, and observed team squads. */
import { OBSERVED_SQUAD_STATS, type ObservedPlayers, type ObservedSquad, type ObservedSquadCompetition, type ObservedSquadPlayer, type ObservedSquadStat, type PlayerProfile, type PlayerRef, type SeasonStat } from "@/contract";
import { baseOf, getJson, type Options } from "./http";
import { count, FEET, invalid, isRecord, optionalText, position, positiveId } from "./validation";
import { playerIdFromSource, SOURCE_TEAM_IDS, sourcePlayerId } from "./identifiers";
import { analysableSourceTeam, PROVENANCE, type SourceCompetition, sourceDeclaration } from "./scope";

const PROFILE_SCOPE = "Datos personales según la fuente. No indican equipo actual.";

/** Checks the whole body before using any of it: a malformed row fails the request. */
function parsePlayers(body: unknown): { players: PlayerRef[]; scope: string } {
  if (!isRecord(body) || !Array.isArray(body.data) || !isRecord(body.meta)) {
    throw invalid("se esperaba { data: [], meta: {} }");
  }
  const { scope, count } = body.meta;
  if (typeof scope !== "string" || !scope.trim()) throw invalid("meta.scope ausente");

  const seen = new Set<number>();
  const players = body.data.map((row, index): PlayerRef => {
    if (!isRecord(row)) throw invalid(`data[${index}] no es un objeto`);
    const { player_id: id, name } = row;
    if (typeof id !== "number" || !Number.isSafeInteger(id) || id <= 0) {
      throw invalid(`data[${index}].player_id`);
    }
    if (seen.has(id)) throw invalid(`player_id ${id} repetido`);
    seen.add(id);
    if (typeof name !== "string" || !name.trim()) throw invalid(`data[${index}].name`);
    return {
      id: playerIdFromSource(id),
      name: name.trim(),
      position: position(row.usual_position, `data[${index}].usual_position`),
    };
  });
  if (count !== undefined && count !== players.length) {
    console.warn(`API local: meta.count (${String(count)}) ≠ ${players.length} jugadores recibidos.`);
  }
  return { players, scope: scope.trim() };
}

/**
 * Personal data only. Fields the dashboard does not show (the provider's
 * name, the download time `fetched_at`) are ignored: the download time says
 * nothing about when the profile itself changed.
 */
function parseProfile(body: unknown, sourceId: number): PlayerProfile["player"] {
  if (!isRecord(body) || !isRecord(body.data)) throw invalid("se esperaba { data: {} }");
  const row = body.data;
  if (row.player_id !== sourceId) throw invalid("player_id no coincide con el solicitado");
  if (typeof row.name !== "string" || !row.name.trim()) throw invalid("name");

  const birthDate = optionalText(row.birth_date, "birth_date");
  if (birthDate !== null && !/^\d{4}-\d{2}-\d{2}$/.test(birthDate)) throw invalid("birth_date");
  const height = row.height_cm;
  if (height !== undefined && height !== null && !(Number.isInteger(height) && (height as number) > 0)) {
    throw invalid("height_cm");
  }
  const foot = optionalText(row.preferred_foot, "preferred_foot");
  return {
    id: playerIdFromSource(sourceId),
    name: row.name.trim(),
    position: position(row.usual_position, "usual_position"),
    birthDate,
    heightCm: typeof height === "number" ? height : null,
    preferredFoot: (foot && FEET[foot.toLowerCase()]) || null,
    country: optionalText(row.country, "country"),
  };
}

/**
 * `observedPlayers` against the local API. `null` when the team has no
 * correspondence in the source or the source does not know it.
 */
export function createLocalApiObservedPlayers(options: Options) {
  return async (teamId: string): Promise<ObservedPlayers | null> => {
    const sourceId = await analysableSourceTeam(options, teamId);
    if (sourceId === null) return null;
    const body = await getJson(options, `${baseOf(options)}/api/v1/teams/${sourceId}/players`);
    return body === null ? null : { teamId, ...parsePlayers(body), source: PROVENANCE };
  };
}

/**
 * `playerProfile` against the local API. `null` for an id that is not one of
 * ours derived from this source, or a player the source does not know.
 */
export function createLocalApiPlayerProfile(options: Options) {
  return async (playerId: string): Promise<PlayerProfile | null> => {
    const sourceId = sourcePlayerId(playerId);
    if (sourceId === null) return null;
    const body = await getJson(options, `${baseOf(options)}/api/v1/players/${sourceId}`);
    if (body === null) return null;
    return { player: parseProfile(body, sourceId), scope: PROFILE_SCOPE, source: PROVENANCE };
  };
}

const OBSERVED_SQUAD_SCOPE =
  "Jugadores observados en las convocatorias de los partidos finalizados disponibles de esta competición. No garantiza toda la temporada ni es una plantilla completa o necesariamente actual.";

/** The service's name for each accumulated figure. */
const SQUAD_STAT_FIELDS: Readonly<Record<ObservedSquadStat, string>> = {
  appearances: "appearances",
  starts: "starts",
  minutes: "minutes",
  goals: "goals",
  assists: "assists",
};

/**
 * One accumulated figure of the service, `{ value, matches, of }`, over the
 * player's call-ups: a sum of non-negative integers, absent when no call-up
 * has it. `appearances` counts the call-ups in which the player took part
 * (`value`) out of those whose participation is known (`matches`: played or
 * unused), so 0 is a value whenever any is known.
 */
function parseSquadStat(value: unknown, stat: ObservedSquadStat, callUps: number, field: string): SeasonStat {
  if (!isRecord(value)) throw invalid(field);
  const matches = count(value.matches, `${field}.matches`);
  if (count(value.of, `${field}.of`) !== callUps) throw invalid(`${field}.of: no son las convocatorias del jugador`);
  if (matches > callUps) throw invalid(`${field}.matches`);
  if (matches === 0) {
    if (value.value !== null) throw invalid(`${field}.value: sin convocatorias con dato no hay cifra`);
    return { value: null, coverage: "none", reason: "not_available", matches: 0, of: callUps };
  }
  const total = count(value.value, `${field}.value`);
  if ((stat === "starts" || stat === "appearances") && total > matches) throw invalid(`${field}.value`);
  return { value: total, coverage: matches === callUps ? "complete" : "partial", matches, of: callUps };
}

function parseSquadPlayer(row: unknown, field: string, withCallUps: number): ObservedSquadPlayer {
  if (!isRecord(row)) throw invalid(field);
  const id = positiveId(row.player_id, `${field}.player_id`);
  const name = optionalText(row.name, `${field}.name`);
  if (name === null) throw invalid(`${field}.name`);
  const height = row.height_cm ?? null;
  if (height !== null && !(typeof height === "number" && Number.isInteger(height) && height > 0)) throw invalid(`${field}.height_cm`);
  const foot = optionalText(row.preferred_foot, `${field}.preferred_foot`);
  const shirt = row.last_shirt_number ?? null;
  const callUps = count(row.call_ups, `${field}.call_ups`);
  if (callUps === 0 || callUps > withCallUps) throw invalid(`${field}.call_ups`);
  const stats = {} as Record<ObservedSquadStat, SeasonStat>;
  for (const stat of OBSERVED_SQUAD_STATS) {
    stats[stat] = parseSquadStat(row[SQUAD_STAT_FIELDS[stat]], stat, callUps, `${field}.${SQUAD_STAT_FIELDS[stat]}`);
  }
  return {
    player: { id: playerIdFromSource(id), name, position: position(row.usual_position, `${field}.usual_position`) },
    heightCm: height,
    preferredFoot: (foot && FEET[foot.toLowerCase()]) || null,
    lastShirtNumber: shirt === null ? null : count(shirt, `${field}.last_shirt_number`),
    callUps,
    stats,
  };
}

/**
 * One competition of the service's player aggregate. The service computes
 * it; this checks it is in the declared scope and adds up (each figure over
 * the player's call-ups, players once each, call-ups and confirmed
 * participations matching their totals). Anything else makes the whole
 * answer invalid: no figure is shown from a response that does not hold.
 */
function parseSquadCompetition(entry: unknown, index: number, scope: readonly SourceCompetition[]): ObservedSquadCompetition {
  const at = (field: string) => `data.competitions[${index}].${field}`;
  if (!isRecord(entry) || !isRecord(entry.matches) || !isRecord(entry.call_ups) || !Array.isArray(entry.players)) {
    throw invalid(`data.competitions[${index}]`);
  }
  const competition = scope.find((c) => c.tournamentId === entry.tournament_id && c.seasonId === entry.season_id);
  if (!competition) throw invalid(`${at("tournament_id")}: competición fuera del ámbito declarado`);
  const matches = { counted: count(entry.matches.counted, at("matches.counted")),
    withCallUps: count(entry.matches.with_call_ups, at("matches.with_call_ups")) };
  if (matches.withCallUps > matches.counted) throw invalid(at("matches.with_call_ups"));
  const c = entry.call_ups;
  const callUps = {
    total: count(c.total, at("call_ups.total")), played: count(c.played, at("call_ups.played")),
    unused: count(c.unused, at("call_ups.unused")), unknown: count(c.unknown, at("call_ups.unknown")),
    contradictions: count(c.contradictions, at("call_ups.contradictions")),
    mismatches: count(c.mismatches, at("call_ups.mismatches")),
    teamWarnings: count(c.team_warnings, at("call_ups.team_warnings")),
    duplicateRecords: count(c.duplicate_records, at("call_ups.duplicate_records")),
    discardedRecords: count(c.discarded_records, at("call_ups.discarded_records")),
  };
  const seen = new Set<string>();
  const players = entry.players.map((row, i) => {
    const player = parseSquadPlayer(row, at(`players[${i}]`), matches.withCallUps);
    if (seen.has(player.player.id)) throw invalid(`${at(`players[${i}].player_id`)} repetido`);
    seen.add(player.player.id);
    return player;
  });
  const sum = (of: (p: ObservedSquadPlayer) => number) => players.reduce((total, p) => total + of(p), 0);
  if (sum((p) => p.callUps) !== callUps.total || callUps.played + callUps.unused + callUps.unknown !== callUps.total
    || sum((p) => p.stats.appearances.value ?? 0) !== callUps.played
    || sum((p) => p.stats.appearances.matches) !== callUps.played + callUps.unused
    || callUps.contradictions > callUps.unknown) {
    throw invalid(`${at("call_ups")}: los totales no coinciden con los jugadores`);
  }
  return {
    id: `sofascore-scope-${competition.tournamentId}-${competition.seasonId}`,
    name: competition.name,
    season: competition.season,
    matches,
    callUps,
    players,
  };
}

/**
 * `observedSquad` against the local API: the team's observed players with
 * their figures in each competition of the declared scope, as the service
 * accumulates them, personal data included (one request, never one per
 * player). `null` for a team without correspondence or one the source does
 * not know.
 */
export function createLocalApiObservedSquad(options: Options) {
  return async (teamId: string): Promise<ObservedSquad | null> => {
    const teamSourceId = SOURCE_TEAM_IDS[teamId];
    if (teamSourceId === undefined) return null;
    const { competitions: scope, ownExport } = await sourceDeclaration(options);
    if (!ownExport.has(teamSourceId)) return null;
    const body = await getJson(options, `${baseOf(options)}/api/v1/teams/${teamSourceId}/player-stats`);
    if (body === null) return null;
    if (!isRecord(body) || !isRecord(body.data) || body.data.team_id !== teamSourceId || !Array.isArray(body.data.competitions)) {
      throw invalid("se esperaba { data: { team_id, competitions: [] } }");
    }
    const competitions = body.data.competitions.map((entry, index) => parseSquadCompetition(entry, index, scope));
    return { teamId, competitions, scope: OBSERVED_SQUAD_SCOPE, source: PROVENANCE };
  };
}
