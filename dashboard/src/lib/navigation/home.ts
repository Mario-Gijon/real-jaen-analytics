import { SAFE_ID } from "./state";

/** «Inicio» on the season and target match a match was opened from (`homeHref`); a real Inicio has no season. */
export type HomeOrigin = { kind: "home"; seasonId: string | null; matchId: string };

/** «Inicio» on a season and target match: what the page keeps in its URL and what links back to it build. */
export function homeHref({ seasonId = null, matchId = null }: { seasonId?: string | null; matchId?: string | null }) {
  const query = new URLSearchParams();
  if (seasonId) query.set("season", seasonId);
  if (matchId) query.set("match", matchId);
  return query.size ? `/?${query}` : "/";
}

/** The «Inicio» to return to from a match opened there, or null when it was opened elsewhere. */
export function homeReturnHref(query: { from?: unknown; season?: unknown; match?: unknown }) {
  if (query.from !== "home") return null;
  const id = (value: unknown) => (typeof value === "string" && SAFE_ID.test(value) ? value : null);
  return homeHref({ seasonId: id(query.season), matchId: id(query.match) });
}

export function setHomeOrigin(query: URLSearchParams, from: HomeOrigin) {
  query.set("from", "home");
  if (from.seasonId) query.set("season", from.seasonId);
  query.set("match", from.matchId);
}
