import { CLUB_ID } from "@/lib/club";
import { SAFE_ID, matchesState, type ObservedMatchListOrigin } from "./state";

export type PlayerOrigin = { kind: "team"; id: string } | { kind: "match"; id: string; tab: "lineups" | "timeline" };

/** The demonstration player origin represented by an existing player URL. */
export function playerSeasonOrigin(query: { from?: unknown; origin?: unknown; originTab?: unknown }): PlayerOrigin | null {
  if (typeof query.origin !== "string" || !SAFE_ID.test(query.origin)) return null;
  if (query.from === "team") return { kind: "team", id: query.origin };
  if (query.from === "match") return { kind: "match", id: query.origin,
    tab: query.originTab === "lineups" ? "lineups" : "timeline" };
  return null;
}

export type PlayerTab = "season" | "matches";

/** A player reached from a match opens its Partidos tab on that match; any other origin opens Temporada. */
export function playerHref(playerId: string, seasonId: string, origin?: PlayerOrigin) {
  const query = new URLSearchParams({ season: seasonId });
  if (origin?.kind === "match") {
    query.set("tab", "matches");
    query.set("match", origin.id);
  }
  if (origin) {
    query.set("from", origin.kind);
    query.set("origin", origin.id);
    if (origin.kind === "match") query.set("originTab", origin.tab);
  }
  return `/players/${encodeURIComponent(playerId)}?${query}`;
}

/** Where a real profile was opened from: a team's observed players or a real match's line-ups. */
export type ProfileOrigin = { kind: "team"; id: string } | { kind: "observedMatch"; id: string; tab?: "lineups"; matches?: ObservedMatchListOrigin };

/**
 * Season-less personal profile of a player from a real source. `season` only
 * restores the team view on return; the profile itself does not depend on it.
 * `matchId` selects one of the player's real matches.
 */
export function playerProfileHref(playerId: string, seasonId: string | null, origin: ProfileOrigin | null, matchId: string | null = null) {
  const query = new URLSearchParams({ view: "profile" });
  if (seasonId) query.set("season", seasonId);
  if (origin) {
    query.set("from", origin.kind);
    query.set("origin", origin.id);
    if (origin.kind === "observedMatch" && origin.tab) query.set("originTab", origin.tab);
    if (origin.kind === "observedMatch" && origin.matches) setProfileMatches(query, origin.matches);
  }
  if (matchId) query.set("match", matchId);
  return `/players/${encodeURIComponent(playerId)}?${query}`;
}

/**
 * Real match selected in a real profile: the one in the URL, else the real
 * match whose line-ups opened the profile, else none (the profile picks).
 */
export function profileInitialMatch(query: { match?: unknown; from?: unknown; origin?: unknown }): string | null {
  if (typeof query.match === "string" && SAFE_ID.test(query.match)) return query.match;
  if (query.from === "observedMatch" && typeof query.origin === "string" && SAFE_ID.test(query.origin)) return query.origin;
  return null;
}

export type ProfileMatchesQuery = { matchFrom?: unknown; matchTeam?: unknown; matchState?: unknown };

export function setProfileMatches(query: URLSearchParams, origin: ObservedMatchListOrigin) {
  query.set("matchFrom", "matches");
  if (origin.teamId && origin.teamId !== CLUB_ID) query.set("matchTeam", origin.teamId);
  if (origin.state !== "all") query.set("matchState", origin.state);
}

/** The origin of a real profile from its URL; anything not built by `playerProfileHref` is dropped. */
export function profileOrigin(query: { from?: unknown; origin?: unknown; originTab?: unknown } & ProfileMatchesQuery): ProfileOrigin | null {
  if (typeof query.origin !== "string" || !SAFE_ID.test(query.origin)) return null;
  if (query.from === "team") return { kind: "team", id: query.origin };
  if (query.from === "observedMatch") {
    const origin: ProfileOrigin = { kind: "observedMatch", id: query.origin };
    if (query.originTab === "lineups") origin.tab = "lineups";
    if (query.matchFrom === "matches") origin.matches = { kind: "matches",
      teamId: typeof query.matchTeam === "string" && SAFE_ID.test(query.matchTeam) ? query.matchTeam : null,
      state: matchesState(query.matchState) };
    return origin;
  }
  return null;
}

/** Initial player tab and match from the URL; unknown values fall back to Temporada without a match. */
export function playerInitialView(query: { tab?: string | string[]; match?: string | string[] }): { tab: PlayerTab; matchId: string | null } {
  if (query.tab !== "matches") return { tab: "season", matchId: null };
  return { tab: "matches", matchId: typeof query.match === "string" && query.match ? query.match : null };
}
