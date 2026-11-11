/** Shared team-name cache and the resolution of source team names. */
import { type ObservedMatch, type ObservedTeam } from "@/contract";
import { baseOf, getJson, type Options } from "./http";
import { invalid, isRecord, optionalText } from "./validation";
import { teamIdFromSource } from "./identifiers";

/** A match before its team names are resolved. */
export type RawMatch = Omit<ObservedMatch, "home" | "away"> & { homeId: number; awayId: number };

/** How long a resolved team name is reused before asking the source again. */
const TEAM_NAME_TTL_MS = 60 * 60 * 1000;
/** Resolved names by service URL and team id, shared by every request of this server process. */
const teamNameCache = new Map<string, { name: string | null; expires: number }>();

/** Forgets every resolved team name (tests start from a clean cache). */
export function clearTeamNameCache() {
  teamNameCache.clear();
}

/**
 * Team names as the source gives them (`GET /api/v1/teams/{id}`). A team the
 * source does not know keeps `name: null`; never a name from elsewhere.
 * Resolved names (and unknown teams) are reused for an hour, so lists that
 * share teams do not ask again; failures are not kept. One request at a time:
 * the local service refused connections (ECONNREFUSED) with about ten at
 * once. The cause is still to be investigated (the service already uses
 * ThreadingHTTPServer); see README.
 */
export async function teamNames(options: Options, ids: readonly number[]): Promise<Map<number, string | null>> {
  const names = new Map<number, string | null>();
  for (const id of new Set(ids)) {
    const key = `${baseOf(options)}|${id}`;
    const cached = teamNameCache.get(key);
    if (cached && cached.expires > Date.now()) {
      names.set(id, cached.name);
      continue;
    }
    const body = await getJson(options, `${baseOf(options)}/api/v1/teams/${id}`);
    let name: string | null = null;
    if (body !== null) {
      if (!isRecord(body) || !isRecord(body.data) || body.data.team_id !== id) throw invalid(`equipo ${id}`);
      name = optionalText(body.data.name, `equipo ${id}.name`);
    }
    teamNameCache.set(key, { name, expires: Date.now() + TEAM_NAME_TTL_MS });
    names.set(id, name);
  }
  return names;
}

/** Contract matches with the source's team names. */
export async function withTeamNames<T extends { match: RawMatch }>(
  options: Options,
  rows: readonly T[],
): Promise<(Omit<T, "match"> & { match: ObservedMatch })[]> {
  const names = await teamNames(options, rows.flatMap((row) => [row.match.homeId, row.match.awayId]));
  const team = (id: number): ObservedTeam => ({ id: teamIdFromSource(id), name: names.get(id) ?? null });
  return rows.map(({ match: { homeId, awayId, ...match }, ...row }) => ({
    ...row,
    match: { ...match, home: team(homeId), away: team(awayId) },
  }));
}
