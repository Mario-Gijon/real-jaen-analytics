import { CLUB_ID } from "@/lib/club";
import { SAFE_ID, matchesState, type MatchesState, type ObservedMatchListOrigin } from "./state";
import { setHomeOrigin, type HomeOrigin } from "./home";
import type { ObservedMatchTeamsOrigin } from "./teams";
import { playerProfileHref, profileOrigin, setProfileMatches, type ProfileMatchesQuery, type ProfileOrigin } from "./players";

/**
 * «Partidos» on a team (`all`: every team) and status filter: what the page
 * keeps in its URL and what links back to it build. The club and «Todos»
 * are the defaults and are left out.
 */
export function matchesHref({ teamId = null, state = "all" }: { teamId?: string | null; state?: MatchesState }) {
  const query = new URLSearchParams();
  if (teamId && teamId !== CLUB_ID) query.set("team", teamId);
  if (state !== "all") query.set("state", state);
  return query.size ? `/matches?${query}` : "/matches";
}

/** A synthetic season's whole match calendar for one team. */
export function seasonMatchesHref(seasonId: string, teamId: string) {
  const query = new URLSearchParams({ season: seasonId, team: teamId, round: "all" });
  return `/matches?${query}`;
}

/** The status filter a «Partidos» URL asks for; anything else is «Todos». */
export function matchesInitialState(query: { state?: unknown }): MatchesState {
  return matchesState(query.state);
}

/**
 * A real profile a real match was opened from, with that profile's own
 * origin so it can still return there. One level only: a profile opened from
 * this match's line-ups returns to the match, never further back.
 */
export type ObservedMatchOrigin = { playerId: string; seasonId: string | null; origin: ProfileOrigin | null };

/** Synthetic match detail; opened from «Inicio», it returns there. */
export function matchHref(matchId: string, from?: HomeOrigin | ObservedMatchTeamsOrigin) {
  const query = new URLSearchParams();
  if (from && from.kind === "home") setHomeOrigin(query, from);
  else if (from) {
    query.set("from", "teams");
    if (from.seasonId) query.set("season", from.seasonId);
    if (from.teamId) query.set("team", from.teamId);
    if (from.tab === "players") query.set("tab", "players");
  }
  return query.size ? `/matches/${encodeURIComponent(matchId)}?${query}` : `/matches/${encodeURIComponent(matchId)}`;
}

/** A synthetic player's selected match, with a shareable return to the same profile selection. */
export function playerMatchHref(matchId: string, { playerId, seasonId, origin }: {
  playerId: string; seasonId: string; origin: { kind: "team"; id: string } | { kind: "match"; id: string; tab: "lineups" | "timeline" } | null;
}) {
  const query = new URLSearchParams({ from: "player", player: playerId, season: seasonId });
  if (origin) {
    query.set("playerFrom", origin.kind);
    query.set("playerOrigin", origin.id);
    if (origin.kind === "match") query.set("playerOriginTab", origin.tab);
  }
  return `/matches/${encodeURIComponent(matchId)}?${query}`;
}

/** Real match detail; its origin query preserves the route back to the previous view. */
export function observedMatchHref(matchId: string, from?: ObservedMatchOrigin | ObservedMatchTeamsOrigin | HomeOrigin | ObservedMatchListOrigin) {
  const query = new URLSearchParams();
  if (from && "kind" in from && from.kind === "home") {
    setHomeOrigin(query, from);
  } else if (from && "kind" in from && from.kind === "matches") {
    query.set("from", "matches");
    if (from.teamId && from.teamId !== CLUB_ID) query.set("team", from.teamId);
    if (from.state !== "all") query.set("state", from.state);
  } else if (from && "kind" in from) {
    query.set("from", "teams");
    if (from.seasonId) query.set("season", from.seasonId);
    if (from.teamId) query.set("team", from.teamId);
    if (from.tab === "players") query.set("tab", "players");
  } else if (from) {
    query.set("from", "player");
    query.set("player", from.playerId);
    if (from.seasonId) query.set("season", from.seasonId);
    if (from.origin) {
      query.set("playerFrom", from.origin.kind);
      query.set("playerOrigin", from.origin.id);
      if (from.origin.kind === "observedMatch" && from.origin.tab) query.set("playerOriginTab", from.origin.tab);
      if (from.origin.kind === "observedMatch" && from.origin.matches) setProfileMatches(query, from.origin.matches);
    }
  }
  const href = `/matches/${encodeURIComponent(matchId)}`;
  return query.size ? `${href}?${query}` : href;
}

/** The real profile to return to from a real match, with that match selected, or null for the match list. */
export function observedMatchReturnHref(matchId: string, query: {
  from?: unknown; player?: unknown; season?: unknown; playerFrom?: unknown; playerOrigin?: unknown; playerOriginTab?: unknown;
} & ProfileMatchesQuery) {
  if (query.from !== "player" || typeof query.player !== "string" || !SAFE_ID.test(query.player)) return null;
  const seasonId = typeof query.season === "string" && SAFE_ID.test(query.season) ? query.season : null;
  const href = playerProfileHref(query.player, seasonId,
    profileOrigin({ ...query, from: query.playerFrom, origin: query.playerOrigin, originTab: query.playerOriginTab }),
    SAFE_ID.test(matchId) ? matchId : null);
  const [path, search] = href.split("?");
  const params = new URLSearchParams(search);
  // Synthetic profiles need an explicit tab to restore the selected season
  // match; real profiles select their match with `match` alone.
  if (seasonId && !query.player.startsWith("sofascore-player-")) params.set("tab", "matches");
  if (!query.player.startsWith("sofascore-player-") && query.playerFrom === "match"
    && typeof query.playerOrigin === "string" && SAFE_ID.test(query.playerOrigin)) {
    params.set("from", "match");
    params.set("origin", query.playerOrigin);
    if (query.playerOriginTab === "lineups" || query.playerOriginTab === "timeline") params.set("originTab", query.playerOriginTab);
  }
  return `${path}?${params}`;
}

/**
 * Another real match opened from this one (e.g. the event that replaces it),
 * keeping the origin this one was opened from, so «Volver» still leads there.
 */
export function relatedObservedMatchHref(matchId: string, query: Record<string, unknown>) {
  const id = (value: unknown) => (typeof value === "string" && SAFE_ID.test(value) ? value : null);
  const homeMatch = query.from === "home" ? id(query.match) : null;
  if (homeMatch) return observedMatchHref(matchId, { kind: "home", seasonId: id(query.season), matchId: homeMatch });
  if (query.from === "matches") return observedMatchHref(matchId, { kind: "matches", teamId: id(query.team), state: matchesState(query.state) });
  if (query.from === "teams") {
    return observedMatchHref(matchId, { kind: "teams", seasonId: id(query.season), teamId: id(query.team),
      tab: query.tab === "players" ? "players" : "performance" });
  }
  const player = query.from === "player" ? id(query.player) : null;
  if (player) {
    return observedMatchHref(matchId, { playerId: player, seasonId: id(query.season),
      origin: profileOrigin({ ...query, from: query.playerFrom, origin: query.playerOrigin, originTab: query.playerOriginTab }) });
  }
  return observedMatchHref(matchId);
}

/**
 * «Partidos» on the team and status a real match was opened from, or null
 * when it was opened elsewhere (the match page then returns to plain
 * «Partidos»: the club, all statuses).
 */
export function observedMatchListReturnHref(query: { from?: unknown; team?: unknown; state?: unknown }) {
  if (query.from !== "matches") return null;
  const teamId = typeof query.team === "string" && SAFE_ID.test(query.team) ? query.team : null;
  return matchesHref({ teamId, state: matchesState(query.state) });
}
