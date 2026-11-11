import { SAFE_ID } from "./state";

/** «Equipos» on the team, season and tab a real match was opened from, as its URL had them (`teamsHref`). */
export type ObservedMatchTeamsOrigin = { kind: "teams"; seasonId: string | null; teamId: string | null; tab: "performance" | "players" };

/** The «Equipos» view to return to from a real match opened there, or null when it was opened elsewhere. */
export function observedMatchTeamsReturnHref(query: { from?: unknown; season?: unknown; team?: unknown; tab?: unknown }) {
  if (query.from !== "teams") return null;
  const id = (value: unknown) => (typeof value === "string" && SAFE_ID.test(value) ? value : null);
  return teamsHref({ seasonId: id(query.season), teamId: id(query.team), tab: query.tab === "players" ? "players" : "performance" });
}

/**
 * «Equipos» on a team, season and tab: what the page keeps in its URL and what
 * links back to it build. The default tab (Rendimiento) is left out.
 */
export function teamsHref({ seasonId = null, teamId = null, tab = "performance" }: {
  seasonId?: string | null; teamId?: string | null; tab?: "performance" | "players";
}) {
  const query = new URLSearchParams();
  if (seasonId) query.set("season", seasonId);
  if (teamId) query.set("team", teamId);
  if (tab === "players") query.set("tab", "players");
  return query.size ? `/teams?${query}` : "/teams";
}

/** The team, season and tab a «Equipos» URL asks for (`teamsHref` writes them); anything else is left unset. */
export function teamsInitialView(query: { team?: unknown; season?: unknown; tab?: unknown }) {
  const text = (value: unknown) => (typeof value === "string" && value ? value : undefined);
  return {
    initialTeamId: text(query.team),
    initialSeasonId: text(query.season),
    initialTab: query.tab === "players" ? "players" as const : "performance" as const,
  };
}
