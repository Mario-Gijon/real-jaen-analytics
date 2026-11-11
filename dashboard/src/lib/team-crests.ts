import type { ObservedTeam } from "@/contract";

/**
 * Crest images of real teams, by our public team id: local files served from
 * `public/`, never fetched from a provider by the browser. A team appears here
 * only with a file of known provenance (see README, «Escudos»); any other team,
 * or a file that fails to load, draws TeamCrest's neutral shield.
 */
const TEAM_CRESTS: Readonly<Record<string, string>> = {
  // Primera Federación, Group 2, 26/27: each file checked against the club its crest names and the source's team name.
  "team-real-jaen": "/crests/real-jaen.webp",
  "sofascore-team-24322": "/crests/ad-alcorcon.webp",
  "sofascore-team-4489": "/crests/algeciras-cf.webp",
  "sofascore-team-24323": "/crests/antequera.webp",
  "sofascore-team-24326": "/crests/atletico-madrileno.webp",
  "sofascore-team-43751": "/crests/cd-teruel.webp",
  "sofascore-team-217210": "/crests/cda-aguilas-fc.webp",
  "sofascore-team-135672": "/crests/ce-europa.webp",
  "sofascore-team-24329": "/crests/fc-cartagena.webp",
  "sofascore-team-2840": "/crests/gimnastic-de-tarragona.webp",
  "sofascore-team-2877": "/crests/hercules-cf.webp",
  "sofascore-team-24265": "/crests/huesca.webp",
  "sofascore-team-263407": "/crests/juventud-torremolinos-cf.webp",
  "sofascore-team-202074": "/crests/rayo-majadahonda.webp",
  "sofascore-team-5069": "/crests/real-madrid-castilla-u21.webp",
  "sofascore-team-4487": "/crests/real-murcia.webp",
  "sofascore-team-2815": "/crests/real-zaragoza.webp",
  "sofascore-team-24363": "/crests/ue-sant-andreu.webp",
  "sofascore-team-24368": "/crests/ud-ibiza.webp",
  "sofascore-team-24338": "/crests/villarreal-b-u23.webp",
};

/** The crest of a team, `null` when there is none: never another team's. */
export function crestUrlOf(teamId: string): string | null {
  return TEAM_CRESTS[teamId] ?? null;
}

/** No colours are invented for a real team: its crest file, or a neutral shield. */
const NEUTRAL = { primary: "#e3deea", secondary: "#5e6172" };

/** What TeamCrest needs for a real team: its source name and its crest file (`crestUrlOf`), or a neutral shield. */
export function observedCrest(team: ObservedTeam) {
  const name = team.name ?? "Equipo sin identificar";
  return { name, shortName: name, crestUrl: crestUrlOf(team.id), colors: NEUTRAL };
}
