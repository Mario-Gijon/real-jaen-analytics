// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError, ObservedTeamPerformance, SourceInfo } from "@/contract";
import { GET as source } from "@/app/api/v1/source/route";
import { GET } from "@/app/api/v1/teams/[teamId]/observed-performance/route";
import { clearTeamNameCache } from "@/server/local-api/local-api-source";

const call = (teamId: string) =>
  GET(new Request(`http://localhost/api/v1/teams/${teamId}/observed-performance`), {
    params: Promise.resolve({ teamId }),
  });
const sourceInfo = async () => (await source().json()) as SourceInfo;

/** `GET /api/v1/scope` of the local API: Primera Federación, Group 2 (94191) in 26/27 (97382). */
const scope = () => new Response(JSON.stringify({ data: { competitions: [
  { name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382 },
], teams_with_own_export: [{ team_id: 2841, name: "Real Jaén", exports: [] }] } }));
const performance = () => new Response(JSON.stringify({ data: { team_id: 2841, competitions: [{
  name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382,
  matches: { in_scope: 2, finished: 1, pending: 1, counted: 1, finished_without_valid_score: 0, excluded_by_version_conflict: 0, unknown_status: 0 },
  record: { played: 1, wins: 1, draws: 0, losses: 0, goals_for: 3, goals_against: 0, goal_difference: 3 },
  venues: { home: { wins: 0, draws: 0, losses: 0 }, away: { wins: 1, draws: 0, losses: 0 } },
  output: {
    season: Object.fromEntries(["goals", "xg", "shots", "shotsOnTarget", "goalsAgainst", "xgAgainst", "shotsAgainst", "shotsOnTargetAgainst", "possession", "corners"].map((key) =>
      [key, key === "goals" ? { value: 3, matches: 1, of: 1 } : key === "goalsAgainst" ? { value: 0, matches: 1, of: 1 } : { value: null, matches: 0, of: 1 }])),
    last3: { match_ids: [1], values: Object.fromEntries(["goals", "xg", "shots", "shotsOnTarget", "goalsAgainst", "xgAgainst", "shotsAgainst", "shotsOnTargetAgainst", "possession", "corners"].map((key) =>
      [key, key === "goals" ? { value: 3, matches: 1, of: 1 } : key === "goalsAgainst" ? { value: 0, matches: 1, of: 1 } : { value: null, matches: 0, of: 1 }])) },
    undated: 0,
  },
  results: [{ side: "away", opponent_team_id: 135672, goals_for: 3, goals_against: 0, result: "win", match: {
    match_id: 1, kickoff_utc: "2026-08-30T17:15:00+00:00", status: "finished", tournament_id: 94191, season_id: 97382,
    competition: "Primera Federacion, Group 2", season: "26/27", round: 1, home_team_id: 135672, away_team_id: 2841,
    home_score: 0, away_score: 3 } }],
}] } }));

/** Simulated local API: the real service is not reached by these tests. */
function localApi(handler: (url: string) => Response) {
  vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/api/v1/scope") ? scope() : handler(String(input)));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const realTeams = (url: string) => url.endsWith("/performance")
  ? performance()
  : new Response(JSON.stringify({ data: { team_id: Number(url.split("/").at(-1)), name: "Equipo" } }));

beforeEach(() => clearTeamNameCache());
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/teams/{teamId}/observed-performance", () => {
  it("has no real section nor real performance in the demonstration", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    expect((await sourceInfo()).realSections).toEqual([]);
    expect((await call("team-real-jaen")).status).toBe(404);
  });

  it("in real mode, returns the real results and says the data are real", async () => {
    localApi(realTeams);
    expect((await sourceInfo()).synthetic).toBe(false);
    const response = await call("team-real-jaen");
    expect(response.status).toBe(200);
    const body = (await response.json()) as ObservedTeamPerformance;
    expect(body.competitions[0].id).toBe("sofascore-scope-94191-97382");
    expect(body.competitions[0].record.goalDifference).toBe(3);
    expect(body.competitions[0].output!.season.goals).toEqual({ value: 3, coverage: "complete", matches: 1, of: 1 });
    expect(body.competitions[0].output!.season.xg.value).toBeNull();
    expect(body.source.synthetic).toBe(false);
  });

  it("answers 404 for a team without correspondence, without asking the source", async () => {
    const fetch = localApi(realTeams);
    expect((await call("team-montealto")).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("answers 503 when the source fails or answers something unusable, never synthetic figures", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi(() => {
      throw new TypeError("fetch failed");
    });
    const failed = await call("team-real-jaen");
    expect(failed.status).toBe(503);
    expect(((await failed.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
    localApi(() => new Response(JSON.stringify({ data: { team_id: 2841, competitions: [{ tournament_id: 150, season_id: 104108 }] } })));
    expect((await call("team-real-jaen")).status).toBe(503);
  });

  it("answers 503 with the reason for a real mode without URL", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubEnv("LOCAL_DATA_API_URL", "");
    const response = await call("team-real-jaen");
    expect(response.status).toBe(503);
    expect(((await response.json()) as ApiError).error.message).toContain("DATA_MODE=real requiere LOCAL_DATA_API_URL");
  });
});
