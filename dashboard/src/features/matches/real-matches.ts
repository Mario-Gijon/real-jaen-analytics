import type { ObservedCompetition, ObservedMatch, ObservedTeam, ObservedTeamMatches } from "@/contract";
import { CLUB_ID } from "@/lib/club";

/** «Todos los equipos» in the team selector and in the URL (`?team=all`). */
export const ALL_TEAMS = "all";

/** The teams to choose from, as the real catalog gives them: the club first, then by name. */
export function teamOptions(teams: readonly ObservedTeam[]): ObservedTeam[] {
  return [...teams].sort((a, b) => Number(b.id === CLUB_ID) - Number(a.id === CLUB_ID)
    || (a.name ?? "").localeCompare(b.name ?? "", "es"));
}

const time = (match: ObservedMatch) => (match.kickoff ? Date.parse(match.kickoff) : NaN);

/**
 * The matches of several teams as one list: a match that appears in both
 * participants' lists (same id) is listed once, by kickoff, undated last.
 * Each list already holds only what the source serves (its scope, without
 * replaced events), so nothing is added or recounted here.
 */
export function mergeTeamMatches(lists: readonly ObservedTeamMatches[]): { matches: ObservedMatch[]; competitions: ObservedCompetition[] } {
  const byId = new Map<string, ObservedMatch>();
  for (const list of lists) for (const { match } of list.matches) if (!byId.has(match.id)) byId.set(match.id, match);
  const matches = [...byId.values()].sort((a, b) => {
    const [ta, tb] = [time(a), time(b)];
    if (Number.isNaN(ta) !== Number.isNaN(tb)) return Number.isNaN(ta) ? 1 : -1;
    return (ta - tb || 0) || a.id.localeCompare(b.id);
  });
  return { matches, competitions: lists[0]?.competitions ?? [] };
}
