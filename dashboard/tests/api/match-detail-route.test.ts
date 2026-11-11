// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiError, MatchDetail, ObservedMatchDetail } from "@/contract";
import { GET as syntheticMatch } from "@/app/api/v1/matches/[matchId]/route";
import { GET } from "@/app/api/v1/matches/[matchId]/observed/route";

const call = (matchId: string) =>
  GET(new Request(`http://localhost/api/v1/matches/${matchId}/observed`), { params: Promise.resolve({ matchId }) });

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
const realMatch = (url: string) => url.endsWith("/statistics") || url.endsWith("/lineups") || url.endsWith("/incidents")
  ? new Response(JSON.stringify({ data: null, meta: { availability: "not_stored", fetched_at: null } }))
  : new Response(JSON.stringify({ data: { match_id: 16599386, kickoff_utc: "2026-11-07T15:30:00+00:00", status: "notstarted",
    tournament_id: 94191, competition: "Primera Federacion, Group 2", season_id: 97382, season: "26/27", round: 11,
    home_team_id: 24322, away_team_id: 2841,
    home_score: null, away_score: null, home_team_name: "AD Alcorcón", away_team_name: "Real Jaén" } }));

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/matches/{matchId}/observed", () => {
  it("has no real matches in the demonstration, and the demonstration's detail is untouched", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    expect((await call("sofascore-match-16599386")).status).toBe(404);
    const matches = await import("@/server/data-source").then(({ getDataSource }) => getDataSource().seasonMatches("season-demo-2026-27", {}));
    const synthetic = await syntheticMatch(new Request("http://localhost"), { params: Promise.resolve({ matchId: matches![0].id }) });
    expect(((await synthetic.json()) as MatchDetail).id).toBe(matches![0].id);
  });

  it("in real mode, returns the real match and the state of its statistics", async () => {
    localApi(realMatch);
    const response = await call("sofascore-match-16599386");
    expect(response.status).toBe(200);
    const body = (await response.json()) as ObservedMatchDetail;
    expect(body.match.home).toEqual({ id: "sofascore-team-24322", name: "AD Alcorcón" });
    expect(body.match.score).toBeNull();
    expect(body.statistics).toEqual({ state: "not_stored", fetchedAt: null });
    expect(body.lineups).toEqual({ state: "not_stored", fetchedAt: null });
    expect(body.incidents).toEqual({ state: "not_stored", fetchedAt: null });
  });

  it("answers 503 for a match outside the scope the source declares, never showing it", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi((url) => url.endsWith("/matches/17259821")
      ? new Response(JSON.stringify({ data: { match_id: 17259821, tournament_id: 150, season_id: 104108, competition: "Copa del Rey",
        status: "notstarted", home_team_id: 493345, away_team_id: 2841 } }))
      : realMatch(url));
    expect((await call("sofascore-match-17259821")).status).toBe(503);
  });

  it("answers 404 when the source refuses a match outside its scope", async () => {
    localApi(() => new Response(JSON.stringify({ error: "Partido fuera del ámbito de esta versión" }), { status: 404 }));
    expect((await call("sofascore-match-17259821")).status).toBe(404);
  });

  it("never reads a synthetic id as real, and answers 503 when the source fails", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetch = localApi(() => {
      throw new TypeError("fetch failed");
    });
    expect((await call("match-r01-01")).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
    const response = await call("sofascore-match-16599386");
    expect(response.status).toBe(503);
    expect(((await response.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });
});
