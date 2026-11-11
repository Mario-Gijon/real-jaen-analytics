import type {
  Competition,
  EnrichedCommentsFilter,
  EnrichedCommentsPage,
  MatchDetail,
  MatchFilter,
  MatchComments,
  MatchSummary,
  ObservedMatchDetail,
  ObservedPlayerMatches,
  ObservedPlayers,
  ObservedSquad,
  ObservedTeamMatches,
  ObservedTeam,
  ObservedTeamPerformance,
  PlayerProfile,
  RealSection,
  PlayerSeason,
  SourceInfo,
  TeamSeason,
  TeamStanding,
} from "@/contract";
import {
  createLocalApiObservedMatches,
  createLocalApiObservedPlayers,
  createLocalApiObservedSquad,
  createLocalApiTeamPerformance,
  createLocalApiTeamIdentity,
  createLocalApiPlayerProfile,
  createLocalApiMatchDetail,
  createLocalApiTeamMatches,
  createLocalApiMatchComments,
  createLocalApiEnrichedComments,
} from "./local-api/local-api-source";
import { syntheticSource } from "./synthetic/synthetic-source";

/**
 * Where the analyst data come from. Route handlers only talk to this
 * interface; each source (synthetic files today, a sports API later) is an
 * adapter that returns contract types. Read-only: `null` means "not found".
 */
export interface DataSource {
  info(): SourceInfo;
  competitions(): Promise<Competition[]>;
  /** Matches of a season, by round and kickoff; `null` if the season does not exist. */
  seasonMatches(seasonId: string, filter: MatchFilter): Promise<MatchSummary[] | null>;
  match(matchId: string): Promise<MatchDetail | null>;
  /** Teams of a season with their table record, by rank. */
  seasonTeams(seasonId: string): Promise<TeamStanding[] | null>;
  teamSeason(seasonId: string, teamId: string): Promise<TeamSeason | null>;
  /** `null` if the player is not in a squad of that season. */
  player(playerId: string, seasonId: string): Promise<PlayerSeason | null>;
  /**
   * Players seen with a team, in no season; `null` if the source has nothing
   * for that team. A real source that fails throws `UpstreamUnavailableError`.
   */
  observedPlayers(teamId: string): Promise<ObservedPlayers | null>;
  /** Public identity of a real team, including teams without a statistics export. */
  teamIdentity(teamId: string): Promise<ObservedTeam | null>;
  /**
   * A team's observed players with their figures in each competition of a
   * real source's scope; `null` without a real source or correspondence.
   * Throws like `observedPlayers`.
   */
  observedSquad(teamId: string): Promise<ObservedSquad | null>;
  /**
   * Personal data of a player from a real source, in no season; `null` if
   * that source does not know the id. Throws like `observedPlayers`.
   */
  playerProfile(playerId: string): Promise<PlayerProfile | null>;
  /**
   * Matches in which a real source lists the player, newest first; `null` if
   * that source does not know the id. Throws like `observedPlayers`.
   */
  playerObservedMatches(playerId: string): Promise<ObservedPlayerMatches | null>;
  /**
   * A team's matches as a real source lists them; `null` if that team has no
   * correspondence in it. Throws like `observedPlayers`.
   */
  teamObservedMatches(teamId: string): Promise<ObservedTeamMatches | null>;
  /**
   * A team's results in each competition of a real source's scope; `null` if
   * that team has no correspondence in it. Throws like `observedPlayers`.
   */
  teamObservedPerformance(teamId: string): Promise<ObservedTeamPerformance | null>;
  /**
   * A real match with its team stats; `null` for an id that is not a real
   * match id or a match the source does not know. Throws like `observedPlayers`.
   */
  matchObservedDetail(matchId: string): Promise<ObservedMatchDetail | null>;
  /** Narrative SofaScore comments of a match; `null` when the match is unknown. */
  matchComments(matchId: string): Promise<MatchComments | null>;
  /** Rule labels for one SofaScore event; `null` when the selected source has no event. */
  enrichedComments(filter: EnrichedCommentsFilter): Promise<EnrichedCommentsPage | null>;
}

/** A configuration that leaves no usable source: said as such, never replaced by other data. */
export class DataConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "DataConfigurationError";
  }
}

export type DataMode = "real" | "demo";

/** `DATA_MODE`: `real` when absent, `demo` only when set; anything else is refused. */
export function dataMode(): DataMode {
  const value = process.env.DATA_MODE || "real";
  if (value !== "real" && value !== "demo") {
    throw new DataConfigurationError(`DATA_MODE=${value} no es válido: usa real o demo.`);
  }
  return value;
}

/** Former per-section switches: no longer read, only reported once if still set. */
const RETIRED = ["DATA_SOURCE", "OBSERVED_PLAYERS_SOURCE", "MATCHES_SOURCE", "TEAMS_SOURCE"] as const;
let retiredReported = false;
function reportRetired() {
  const set = RETIRED.filter((name) => process.env[name] !== undefined);
  if (retiredReported || !set.length) return;
  retiredReported = true;
  console.warn(`[config] ${set.join(", ")} ya no se usan y se ignoran: el modo lo decide DATA_MODE (real por defecto, o demo).`);
}

const REAL_SECTIONS: RealSection[] = ["observedPlayers", "matches", "teams"];

/**
 * The real source: every section from the local API at `baseUrl`. The
 * synthetic season does not exist here: its methods find nothing, so no
 * route, not even an old link, can serve invented data in real mode.
 */
export function createRealSource(baseUrl: string): DataSource {
  const local = { baseUrl };
  return {
    info: () => ({ synthetic: false, label: "Datos reales", realSections: REAL_SECTIONS }),
    competitions: async () => [],
    seasonMatches: async () => null,
    match: async () => null,
    seasonTeams: async () => null,
    teamSeason: async () => null,
    player: async () => null,
    observedPlayers: createLocalApiObservedPlayers(local),
    teamIdentity: createLocalApiTeamIdentity(local),
    observedSquad: createLocalApiObservedSquad(local),
    playerProfile: createLocalApiPlayerProfile(local),
    playerObservedMatches: createLocalApiObservedMatches(local),
    teamObservedMatches: createLocalApiTeamMatches(local),
    teamObservedPerformance: createLocalApiTeamPerformance(local),
    matchObservedDetail: createLocalApiMatchDetail(local),
    matchComments: createLocalApiMatchComments(local),
    enrichedComments: createLocalApiEnrichedComments(local),
  };
}

/**
 * The configured source, by `DATA_MODE`: `real` (the default) reads the local
 * API at `LOCAL_DATA_API_URL`, which it requires; `demo`, set on purpose,
 * serves only the synthetic season and never asks the local API. A wrong
 * configuration throws `DataConfigurationError`: it never falls back to the
 * other mode.
 */
export function getDataSource(): DataSource {
  reportRetired();
  if (dataMode() === "demo") return syntheticSource;
  const baseUrl = process.env.LOCAL_DATA_API_URL;
  if (!baseUrl) {
    throw new DataConfigurationError("DATA_MODE=real requiere LOCAL_DATA_API_URL (la URL de la API local de datos).");
  }
  return createRealSource(baseUrl);
}
