// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiError, ObservedSquad } from "@/contract";
import { GET } from "@/app/api/v1/teams/[teamId]/observed-squad/route";

const call = (teamId: string) =>
  GET(new Request(`http://localhost/api/v1/teams/${teamId}/observed-squad`), {
    params: Promise.resolve({ teamId }),
  });

/** `GET /api/v1/scope` of the local API: Primera Federación, Group 2 (94191) in 26/27 (97382). */
const scope = () => new Response(JSON.stringify({ data: { competitions: [
  { name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382 },
], teams_with_own_export: [{ team_id: 2841, name: "Real Jaén", exports: [] }] } }));
const figure = (value: number | null, matches: number, of: number) => ({ value, matches, of });
const playerStats = () => new Response(JSON.stringify({ data: { team_id: 2841, competitions: [{
  name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382,
  matches: { counted: 1, with_call_ups: 1 },
  call_ups: { total: 1, played: 1, unused: 0, unknown: 0, contradictions: 0, mismatches: 0, team_warnings: 0, duplicate_records: 0, discarded_records: 0 },
  players: [{ player_id: 966303, name: "Moha Keita", usual_position: "M", height_cm: 165, preferred_foot: null, last_shirt_number: 19,
    call_ups: 1, starts: figure(1, 1, 1), appearances: figure(1, 1, 1), minutes: figure(90, 1, 1), goals: figure(null, 0, 1),
    assists: figure(0, 1, 1) }],
}] } }));

/** Simulated local API: the real service is not reached by these tests. */
function localApi(handler: () => Response) {
  vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/api/v1/scope") ? scope() : handler());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/teams/{teamId}/observed-squad", () => {
  it("has nothing synthetic: 404 in the demonstration", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    expect((await call("team-real-jaen")).status).toBe(404);
  });

  it("in real mode, returns the real figures of each competition", async () => {
    const fetch = localApi(playerStats);
    const response = await call("team-real-jaen");
    expect(response.status).toBe(200);
    const body = (await response.json()) as ObservedSquad;
    expect(body.competitions[0].id).toBe("sofascore-scope-94191-97382");
    expect(body.competitions[0].players[0].stats.goals).toEqual({ value: null, coverage: "none", reason: "not_available", matches: 0, of: 1 });
    expect(body.source.synthetic).toBe(false);
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8001/api/v1/teams/2841/player-stats", expect.anything());
  });

  it("answers 404 without correspondence and 503 when the source fails, never synthetic figures", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi(() => {
      throw new TypeError("fetch failed");
    });
    expect((await call("team-montealto")).status).toBe(404);
    const failed = await call("team-real-jaen");
    expect(failed.status).toBe(503);
    expect(((await failed.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });
});
