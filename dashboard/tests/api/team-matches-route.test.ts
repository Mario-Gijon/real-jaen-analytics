// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError, ObservedTeamMatches, SourceInfo } from "@/contract";
import { GET as source } from "@/app/api/v1/source/route";
import { GET } from "@/app/api/v1/teams/[teamId]/observed-matches/route";
import { clearTeamNameCache } from "@/server/local-api/local-api-source";

const call = (teamId: string) =>
  GET(new Request(`http://localhost/api/v1/teams/${teamId}/observed-matches`), {
    params: Promise.resolve({ teamId }),
  });
const sourceInfo = async () => (await source().json()) as SourceInfo;

/** `GET /api/v1/scope` of the local API: Primera Federación, Group 2 (94191) in 26/27 (97382). */
const scope = () => new Response(JSON.stringify({ data: { competitions: [
  { name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382 },
] } }));

/** Simulated local API: the real service is not reached by these tests. */
function localApi(handler: (url: string) => Response) {
  vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/api/v1/scope") ? scope() : handler(String(input)));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const realMatches = (url: string) =>
  url.endsWith("/teams/2841/matches")
    ? new Response(JSON.stringify({ data: [{ match_id: 1, kickoff_utc: "2026-08-30T17:15:00+00:00", status: "finished",
      tournament_id: 94191, competition: "Primera Federacion, Group 2", season_id: 97382, season: "26/27", round: 1,
      home_team_id: 135672, away_team_id: 2841,
      home_score: 0, away_score: 3 }] }))
    : new Response(JSON.stringify({ data: { team_id: Number(url.split("/").at(-1)), name: "Equipo" } }));

beforeEach(() => clearTeamNameCache());
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/teams/{teamId}/observed-matches", () => {
  it("has no real section nor real matches in the demonstration", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    expect((await sourceInfo()).realSections).toEqual([]);
    expect((await call("team-real-jaen")).status).toBe(404);
  });

  it("in real mode, returns the real matches and says every section is real", async () => {
    localApi(realMatches);
    const response = await call("team-real-jaen");
    expect(response.status).toBe(200);
    const body = (await response.json()) as ObservedTeamMatches;
    expect(body.matches.map((row) => [row.match.id, row.side, row.match.score])).toEqual([
      ["sofascore-match-1", "away", { home: 0, away: 3 }],
    ]);
    expect(await sourceInfo()).toEqual({ synthetic: false, label: "Datos reales", realSections: ["observedPlayers", "matches", "teams"] });
  });

  it("answers 404 for a team without correspondence and 503 when the source fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetch = localApi(() => {
      throw new TypeError("fetch failed");
    });
    expect((await call("team-montealto")).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
    const response = await call("team-real-jaen");
    expect(response.status).toBe(503);
    expect(((await response.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });

  it("answers 503 for a malformed response, never synthetic matches", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi(() => new Response(JSON.stringify({ data: [{ match_id: 1, home_team_id: 1, away_team_id: 2 }] })));
    expect((await call("team-real-jaen")).status).toBe(503);
  });

  it("answers 503 with the reason for an invalid DATA_MODE or a real mode without URL, never the demonstration", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    vi.stubEnv("DATA_MODE", "otra");
    vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
    const invalid = await call("team-real-jaen");
    expect(invalid.status).toBe(503);
    expect(((await invalid.json()) as ApiError).error.message).toContain("DATA_MODE=otra no es válido");
    vi.stubEnv("DATA_MODE", "real");
    vi.stubEnv("LOCAL_DATA_API_URL", "");
    const withoutUrl = await call("team-real-jaen");
    expect(withoutUrl.status).toBe(503);
    expect(((await withoutUrl.json()) as ApiError).error.message).toContain("LOCAL_DATA_API_URL");
    expect(fetch).not.toHaveBeenCalled();
  });
});
