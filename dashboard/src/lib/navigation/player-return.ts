import { observedMatchHref } from "./matches";
import { SAFE_ID } from "./state";
import { profileOrigin, type ProfileMatchesQuery } from "./players";
import { teamsHref } from "./teams";

export function playerReturnHref(seasonId: string | null, query: { from?: string; origin?: string; originTab?: string } & ProfileMatchesQuery) {
  const validId = query.origin && SAFE_ID.test(query.origin);
  // A real match return keeps its originating profile context and selected tab.
  if (query.from === "observedMatch" && validId) {
    const origin = profileOrigin(query);
    const href = observedMatchHref(query.origin!, origin?.kind === "observedMatch" ? origin.matches : undefined);
    return query.originTab === "lineups" ? `${href}${href.includes("?") ? "&" : "?"}tab=lineups` : href;
  }
  if (query.from === "match" && validId) {
    const tab = query.originTab === "lineups" || query.originTab === "timeline" ? `?tab=${query.originTab}` : "";
    return `/matches/${encodeURIComponent(query.origin!)}${tab}`;
  }
  return teamsHref({ seasonId, teamId: query.from === "team" && validId ? query.origin! : null, tab: "players" });
}
