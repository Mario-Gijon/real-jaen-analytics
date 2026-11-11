/** The competitions and team exports declared by the local API. */
import { type ObservedCompetition } from "@/contract";
import { baseOf, getJson, type Options, UpstreamUnavailableError } from "./http";
import { invalid, isRecord, optionalText, positiveId } from "./validation";
import { SOURCE_TEAM_IDS } from "./identifiers";

export const PROVENANCE = { synthetic: false, label: "Datos reales" } as const;

/** A competition the local API serves, with the source ids that identify its matches. */
export type SourceCompetition = ObservedCompetition & { tournamentId: number; seasonId: number };

/**
 * The competitions the local API serves (`GET /api/v1/scope`): one group of a
 * competition in one season each. The service filters by them; a service that
 * does not declare its scope is unusable, never assumed to serve everything.
 */
export async function sourceScope(options: Options): Promise<SourceCompetition[]> {
  return (await sourceDeclaration(options)).competitions;
}

/**
 * The scope and the teams with an export of their own declared by the
 * importer's manifest (`teams_with_own_export`). An export of its own does
 * not prove a full calendar; a service that declares none (an older one, or
 * data imported without a manifest) has no team to analyse.
 */
export async function sourceDeclaration(options: Options): Promise<{ competitions: SourceCompetition[]; ownExport: Set<number> }> {
  const body = await getJson(options, `${baseOf(options)}/api/v1/scope`);
  if (body === null) throw new UpstreamUnavailableError("La API local no declara su ámbito (/api/v1/scope).");
  if (!isRecord(body) || !isRecord(body.data) || !Array.isArray(body.data.competitions) || !body.data.competitions.length) {
    throw invalid("se esperaba { data: { competitions: [...] } }");
  }
  const declared = body.data.teams_with_own_export ?? [];
  if (!Array.isArray(declared)) throw invalid("data.teams_with_own_export");
  const ownExport = new Set(declared.map((entry, index) => {
    if (!isRecord(entry)) throw invalid(`data.teams_with_own_export[${index}]`);
    return positiveId(entry.team_id, `data.teams_with_own_export[${index}].team_id`);
  }));
  return { competitions: parseCompetitions(body.data.competitions), ownExport };
}

/**
 * The source id of a team that can be analysed: explicit correspondence and
 * an export of its own declared by the source. `null` otherwise, so a rival
 * seen only in other teams' matches never passes for an analysed team.
 */
export async function analysableSourceTeam(options: Options, teamId: string): Promise<number | null> {
  const sourceId = SOURCE_TEAM_IDS[teamId];
  if (sourceId === undefined) return null;
  return (await sourceDeclaration(options)).ownExport.has(sourceId) ? sourceId : null;
}

function parseCompetitions(competitions: unknown[]): SourceCompetition[] {
  return competitions.map((entry, index) => {
    const at = (field: string) => `data.competitions[${index}].${field}`;
    if (!isRecord(entry)) throw invalid(`data.competitions[${index}]`);
    const name = optionalText(entry.name, at("name"));
    if (name === null) throw invalid(at("name"));
    return { name, season: optionalText(entry.season, at("season")),
      tournamentId: positiveId(entry.tournament_id, at("tournament_id")), seasonId: positiveId(entry.season_id, at("season_id")) };
  });
}

/** A match the service returned outside its own scope makes the whole answer invalid: it is never shown. */
export function assertInScope(match: Record<string, unknown>, scope: readonly SourceCompetition[], at: (field: string) => string) {
  if (!scope.some((c) => c.tournamentId === match.tournament_id && c.seasonId === match.season_id)) {
    throw invalid(`${at("tournament_id")}: partido fuera del ámbito declarado`);
  }
}
