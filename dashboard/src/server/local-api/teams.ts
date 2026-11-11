/** Observed team performance and its aggregate consistency checks. */
import { type MatchResult, type ObservedCompetitionPerformance, type ObservedTeam, type ObservedTeamOutput, type ObservedTeamPerformance, type SeasonStat, type Side, TEAM_OUTPUT_INDICATORS, type TeamOutputIndicator } from "@/contract";
import { baseOf, getJson, type Options, UpstreamUnavailableError } from "./http";
import { count, invalid, isRecord, positiveId } from "./validation";
import { matchIdFromSource, sourceTeamId, SOURCE_TEAM_IDS } from "./identifiers";
import { PROVENANCE, type SourceCompetition, sourceDeclaration } from "./scope";
import { teamNames, withTeamNames } from "./cache";
import { parseMatch } from "./matches";

const TEAM_PERFORMANCE_SCOPE =
  "Resultados, producción y concesión de los partidos finalizados observados en esta competición (estadísticas del partido completo). No es necesariamente la temporada completa: sin clasificación ni puntos.";

const RESULT_KEYS: Readonly<Record<MatchResult, "wins" | "draws" | "losses">> = { win: "wins", draw: "draws", loss: "losses" };
const resultOf = (goalsFor: number, goalsAgainst: number): MatchResult =>
  goalsFor > goalsAgainst ? "win" : goalsFor === goalsAgainst ? "draw" : "loss";

/** Resolve a known public team id to its source identity without requiring its own statistics export. */
export function createLocalApiTeamIdentity(options: Options) {
  return async (teamId: string): Promise<ObservedTeam | null> => {
    const sourceId = sourceTeamId(teamId);
    if (sourceId === null) return null;
    const name = (await teamNames(options, [sourceId])).get(sourceId) ?? null;
    return name ? { id: teamId, name } : null;
  };
}

/** Highest valid per-match average of each indicator: a percentage for possession, unbounded otherwise. */
const OUTPUT_LIMIT: Partial<Record<TeamOutputIndicator, number>> = { possession: 100 };

/** One average of the service: `{ value, matches, of }` as a {@link SeasonStat}; absent when no match has it. */
function parseAverage(value: unknown, indicator: TeamOutputIndicator, field: string): SeasonStat {
  if (!isRecord(value)) throw invalid(field);
  const matches = count(value.matches, `${field}.matches`);
  const of = count(value.of, `${field}.of`);
  if (matches > of) throw invalid(`${field}.matches`);
  if (matches === 0) {
    if (value.value !== null) throw invalid(`${field}.value: sin partidos con dato no hay media`);
    return { value: null, coverage: "none", reason: "not_available", matches: 0, of };
  }
  const average = value.value;
  if (typeof average !== "number" || !Number.isFinite(average) || average < 0 || average > (OUTPUT_LIMIT[indicator] ?? Infinity)) {
    throw invalid(`${field}.value`);
  }
  return { value: average, coverage: matches === of ? "complete" : "partial", matches, of };
}

function parseAverages(value: unknown, of: number, field: string): Record<TeamOutputIndicator, SeasonStat> {
  if (!isRecord(value)) throw invalid(field);
  const averages = {} as Record<TeamOutputIndicator, SeasonStat>;
  for (const indicator of TEAM_OUTPUT_INDICATORS) {
    averages[indicator] = parseAverage(value[indicator], indicator, `${field}.${indicator}`);
    if (averages[indicator].of !== of) throw invalid(`${field}.${indicator}.of`);
  }
  return averages;
}

/** A missing xG is null; values outside its numeric domain invalidate the response. */
function optionalXg(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw invalid(field);
  return value;
}

const near = (a: number | null, b: number) => a !== null && Math.abs(a - b) < 1e-9;

/**
 * Production and concession of the service over the counted matches. It
 * checks what can be checked against the results: averages over every
 * counted match (season) or over «Últimos 3», which must be the three most
 * recent dated results; goals must match the scores.
 */
function parseOutput(value: unknown, results: readonly { match: { id: string; kickoff: string | null }; goalsFor: number; goalsAgainst: number }[], field: string): ObservedTeamOutput {
  if (!isRecord(value) || !isRecord(value.last3) || !Array.isArray(value.last3.match_ids)) throw invalid(field);
  const season = parseAverages(value.season, results.length, `${field}.season`);
  const dated = results.filter((row) => row.match.kickoff !== null);
  const expected = dated.slice(-3);
  const matchIds = value.last3.match_ids.map((id, i) => matchIdFromSource(positiveId(id, `${field}.last3.match_ids[${i}]`)));
  if (matchIds.join() !== expected.map((row) => row.match.id).join()) throw invalid(`${field}.last3.match_ids: no son los tres más recientes`);
  const last3 = parseAverages(value.last3.values, expected.length, `${field}.last3.values`);
  if (count(value.undated, `${field}.undated`) !== results.length - dated.length) throw invalid(`${field}.undated`);
  const checkGoals = (rows: readonly { goalsFor: number; goalsAgainst: number }[], averages: Record<TeamOutputIndicator, SeasonStat>) => {
    if (!rows.length) return;
    const goals = rows.reduce((sum, row) => sum + row.goalsFor, 0) / rows.length;
    const against = rows.reduce((sum, row) => sum + row.goalsAgainst, 0) / rows.length;
    if (!near(averages.goals.value, goals) || !near(averages.goalsAgainst.value, against)) throw invalid(`${field}: goles que no coinciden con los marcadores`);
  };
  checkGoals(results, season);
  checkGoals(expected, last3);
  return { season, last3: { matchIds, values: last3 }, undated: value.undated as number };
}

/**
 * One competition of the source's aggregate. The service computes it; this
 * only checks it is in the declared scope and consistent (states add up, the
 * record matches its results and venues, each result matches its score and
 * side) before adapting it. Anything else makes the whole answer invalid.
 */
async function parseCompetitionPerformance(
  options: Options, entry: unknown, index: number, teamSourceId: number, scope: readonly SourceCompetition[],
): Promise<ObservedCompetitionPerformance> {
  const at = (field: string) => `data.competitions[${index}].${field}`;
  if (!isRecord(entry) || !isRecord(entry.matches) || !isRecord(entry.record) || !isRecord(entry.venues) || !Array.isArray(entry.results)) {
    throw invalid(`data.competitions[${index}]`);
  }
  const competition = scope.find((c) => c.tournamentId === entry.tournament_id && c.seasonId === entry.season_id);
  if (!competition) throw invalid(`${at("tournament_id")}: competición fuera del ámbito declarado`);
  const m = entry.matches;
  const matches = {
    inScope: count(m.in_scope, at("matches.in_scope")), finished: count(m.finished, at("matches.finished")),
    pending: count(m.pending, at("matches.pending")), counted: count(m.counted, at("matches.counted")),
    finishedWithoutValidScore: count(m.finished_without_valid_score, at("matches.finished_without_valid_score")),
    excludedByVersionConflict: count(m.excluded_by_version_conflict, at("matches.excluded_by_version_conflict")),
    unknownStatus: count(m.unknown_status, at("matches.unknown_status")),
    // A source without local replacements may not send it: none.
    replacedLocally: m.replaced_locally === undefined ? 0 : count(m.replaced_locally, at("matches.replaced_locally")),
  };
  const r = entry.record;
  const record = {
    played: count(r.played, at("record.played")), wins: count(r.wins, at("record.wins")), draws: count(r.draws, at("record.draws")),
    losses: count(r.losses, at("record.losses")), goalsFor: count(r.goals_for, at("record.goals_for")),
    goalsAgainst: count(r.goals_against, at("record.goals_against")), goalDifference: 0,
  };
  if (r.goal_difference !== record.goalsFor - record.goalsAgainst) throw invalid(at("record.goal_difference"));
  record.goalDifference = record.goalsFor - record.goalsAgainst;
  const venues = {} as ObservedCompetitionPerformance["venues"];
  for (const side of ["home", "away"] as const) {
    const v = entry.venues[side];
    if (!isRecord(v)) throw invalid(at(`venues.${side}`));
    venues[side] = { win: count(v.wins, at(`venues.${side}.wins`)), draw: count(v.draws, at(`venues.${side}.draws`)),
      loss: count(v.losses, at(`venues.${side}.losses`)) };
  }

  const rows = entry.results.map((row, i) => {
    const rat = (field: string) => at(`results[${i}].${field}`);
    if (!isRecord(row) || !isRecord(row.match)) throw invalid(rat("match"));
    if (row.match.tournament_id !== competition.tournamentId || row.match.season_id !== competition.seasonId) {
      throw invalid(`${rat("match.tournament_id")}: partido de otra competición`);
    }
    const match = parseMatch(row.match, positiveId(row.match.match_id, rat("match.match_id")), (field) => rat(`match.${field}`));
    if (match.status !== "finished" || match.score === null) throw invalid(`${rat("match")}: no es un finalizado con marcador`);
    if (row.side !== "home" && row.side !== "away") throw invalid(rat("side"));
    const side: Side = row.side;
    if ((side === "home" ? match.homeId : match.awayId) !== teamSourceId) throw invalid(rat("side"));
    const goalsFor = count(row.goals_for, rat("goals_for"));
    const goalsAgainst = count(row.goals_against, rat("goals_against"));
    const [own, other] = side === "home" ? [match.score.home, match.score.away] : [match.score.away, match.score.home];
    if (own !== goalsFor || other !== goalsAgainst) throw invalid(`${rat("goals_for")}: no coincide con el marcador`);
    const result = resultOf(goalsFor, goalsAgainst);
    if (row.result !== result) throw invalid(rat("result"));
    return {
      match, side, result, goalsFor, goalsAgainst,
      xgFor: optionalXg(row.xg_for, rat("xg_for")),
      xgAgainst: optionalXg(row.xg_against, rat("xg_against")),
    };
  });

  const tally = { win: 0, draw: 0, loss: 0 };
  const byVenue = { home: { win: 0, draw: 0, loss: 0 }, away: { win: 0, draw: 0, loss: 0 } };
  for (const row of rows) {
    tally[row.result]++;
    byVenue[row.side][row.result]++;
  }
  const consistent =
    matches.inScope === matches.finished + matches.pending + matches.unknownStatus
    && matches.counted === matches.finished - matches.finishedWithoutValidScore - matches.excludedByVersionConflict
    && record.played === matches.counted && rows.length === record.played
    && (["win", "draw", "loss"] as const).every((k) => tally[k] === record[RESULT_KEYS[k]]
      && byVenue.home[k] === venues.home[k] && byVenue.away[k] === venues.away[k])
    && rows.reduce((sum, row) => sum + row.goalsFor, 0) === record.goalsFor
    && rows.reduce((sum, row) => sum + row.goalsAgainst, 0) === record.goalsAgainst;
  if (!consistent) throw invalid(`${at("record")}: los totales no coinciden con los partidos`);

  // A failure of the metrics alone keeps the record: the figures are left out, never replaced.
  let output: ObservedTeamOutput | null = null;
  try {
    output = parseOutput(entry.output, rows.map((row) => ({ ...row, match: { id: row.match.id, kickoff: row.match.kickoff } })), at("output"));
  } catch (error) {
    if (!(error instanceof UpstreamUnavailableError)) throw error;
    console.error(`API local: ${error.message}; se muestran resultados sin producción ni concesión.`);
  }
  return {
    id: `sofascore-scope-${competition.tournamentId}-${competition.seasonId}`,
    name: competition.name,
    season: competition.season,
    matches,
    record,
    venues,
    results: await withTeamNames(options, rows),
    output,
  };
}

/**
 * `teamObservedPerformance` against the local API: the team's results in each
 * competition of the declared scope, as the service aggregates them, plus the
 * teams that can be analysed (those with explicit correspondence). `null` for
 * a team without correspondence or one the source does not know.
 */
export function createLocalApiTeamPerformance(options: Options) {
  return async (teamId: string): Promise<ObservedTeamPerformance | null> => {
    const teamSourceId = SOURCE_TEAM_IDS[teamId];
    if (teamSourceId === undefined) return null;
    const { competitions: scope, ownExport } = await sourceDeclaration(options);
    if (!ownExport.has(teamSourceId)) return null;
    const body = await getJson(options, `${baseOf(options)}/api/v1/teams/${teamSourceId}/performance`);
    if (body === null) return null;
    if (!isRecord(body) || !isRecord(body.data) || body.data.team_id !== teamSourceId || !Array.isArray(body.data.competitions)) {
      throw invalid("se esperaba { data: { team_id, competitions: [] } }");
    }
    const competitions: ObservedCompetitionPerformance[] = [];
    // One at a time: the local service refuses concurrent connections.
    for (const [index, entry] of body.data.competitions.entries()) {
      competitions.push(await parseCompetitionPerformance(options, entry, index, teamSourceId, scope));
    }
    // The teams that can be analysed: explicit correspondence and an export of their own.
    const analysable = Object.entries(SOURCE_TEAM_IDS).filter(([, sourceId]) => ownExport.has(sourceId));
    const names = await teamNames(options, analysable.map(([, sourceId]) => sourceId));
    const teams = analysable.map(([id, sourceId]) => ({ id, name: names.get(sourceId) ?? null }));
    return { teamId, competitions, teams, scope: TEAM_PERFORMANCE_SCOPE, source: PROVENANCE };
  };
}
