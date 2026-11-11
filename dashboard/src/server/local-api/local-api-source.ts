/**
 * Public entry point for the real local API adapter. Keep consumers on this
 * module so the internal parsing and transport modules can evolve independently.
 */
export { DEFAULT_TIMEOUT_MS, UpstreamUnavailableError } from "./http";
export { publicMatchId, sourceMatchId, playerIdFromSource, sourcePlayerId, sourceTeamId } from "./identifiers";
export { clearTeamNameCache } from "./cache";
export { createLocalApiObservedPlayers, createLocalApiPlayerProfile, createLocalApiObservedSquad } from "./players";
export { createLocalApiObservedMatches, createLocalApiTeamMatches } from "./matches";
export { createLocalApiTeamIdentity, createLocalApiTeamPerformance } from "./teams";
export { createLocalApiMatchDetail } from "./match-detail";
export { createLocalApiMatchComments } from "./match-comments";
export { createLocalApiEnrichedComments } from "./enrichment-comments";
