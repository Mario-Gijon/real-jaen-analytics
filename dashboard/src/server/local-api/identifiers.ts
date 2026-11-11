/** Stable public identifiers and their source ID mappings. */
/**
 * Our team ids and their ids in the source. Explicit on purpose: a team
 * without an entry has no real data, and nothing is ever matched by name.
 * A team other than the club keeps the id the match lists already derive for
 * it (`sofascore-team-{id}`), so its links do not change. Having an entry is
 * not enough to analyse a team: the source must also declare an export of
 * its own (see `analysableSourceTeam`).
 */
export const SOURCE_TEAM_IDS: Readonly<Record<string, number>> = {
  "team-real-jaen": 2841,
  // Primera Federación, Group 2, 26/27: ids checked against the scraper's catalog (tournament 94191, season 97382).
  "sofascore-team-24368": 24368, // UD Ibiza
  "sofascore-team-24322": 24322, // AD Alcorcón
  "sofascore-team-4489": 4489, // Algeciras CF
  "sofascore-team-24323": 24323, // Antequera
  "sofascore-team-24326": 24326, // Atlético Madrileño
  "sofascore-team-43751": 43751, // CD Teruel
  "sofascore-team-217210": 217210, // CDA Águilas FC
  "sofascore-team-135672": 135672, // CE Europa
  "sofascore-team-24329": 24329, // FC Cartagena
  "sofascore-team-2840": 2840, // Gimnàstic de Tarragona
  "sofascore-team-2877": 2877, // Hércules CF
  "sofascore-team-24265": 24265, // Huesca
  "sofascore-team-263407": 263407, // Juventud Torremolinos CF
  "sofascore-team-202074": 202074, // Rayo Majadahonda
  "sofascore-team-5069": 5069, // Real Madrid Castilla U21
  "sofascore-team-4487": 4487, // Real Murcia
  "sofascore-team-2815": 2815, // Real Zaragoza
  "sofascore-team-24363": 24363, // UE Sant Andreu
  "sofascore-team-24338": 24338, // Villarreal B U23
};

const PLAYER_ID_PREFIX = "sofascore-player-";

/** Our id for a source team: the explicit one when mapped, else one derived from the provider's. */
export function teamIdFromSource(sourceId: number): string {
  const mapped = Object.entries(SOURCE_TEAM_IDS).find(([, id]) => id === sourceId);
  return mapped ? mapped[0] : `sofascore-team-${sourceId}`;
}

/** The source id behind one of our public team ids; `null` for an unknown format. */
export function sourceTeamId(teamId: string): number | null {
  const mapped = SOURCE_TEAM_IDS[teamId];
  if (mapped !== undefined) return mapped;
  const match = /^sofascore-team-([1-9]\d*)$/.exec(teamId);
  if (!match) return null;
  const id = Number(match[1]);
  return Number.isSafeInteger(id) ? id : null;
}

const MATCH_ID_PREFIX = "sofascore-match-";
export const matchIdFromSource = (sourceId: number) => `${MATCH_ID_PREFIX}${sourceId}`;

/** Our match id for the provider's id (the update scope declares its matches with the provider's ids). */
export const publicMatchId = matchIdFromSource;

/** The provider's id behind one of our derived match ids; `null` for any other id (e.g. a synthetic one). */
export function sourceMatchId(matchId: string): number | null {
  if (!matchId.startsWith(MATCH_ID_PREFIX)) return null;
  const digits = matchId.slice(MATCH_ID_PREFIX.length);
  if (!/^[1-9]\d*$/.test(digits)) return null;
  const id = Number(digits);
  return Number.isSafeInteger(id) ? id : null;
}

/**
 * Public player id derived from the provider's id. Stable while this provider
 * is used, but NOT an identity independent of it: another source would need
 * an explicit correspondence.
 */
export const playerIdFromSource = (sourceId: number) => `${PLAYER_ID_PREFIX}${sourceId}`;

/** The provider's id behind one of our derived ids; `null` for any other id. */
export function sourcePlayerId(playerId: string): number | null {
  if (!playerId.startsWith(PLAYER_ID_PREFIX)) return null;
  const digits = playerId.slice(PLAYER_ID_PREFIX.length);
  if (!/^[1-9]\d*$/.test(digits)) return null;
  const id = Number(digits);
  return Number.isSafeInteger(id) ? id : null;
}
