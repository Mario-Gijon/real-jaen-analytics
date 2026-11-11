// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiError, ObservedPlayers, SourceInfo } from "@/contract";
import { GET as source } from "@/app/api/v1/source/route";
import { GET } from "@/app/api/v1/teams/[teamId]/players/route";

const call = (teamId: string) =>
  GET(new Request(`http://localhost/api/v1/teams/${teamId}/players`), {
    params: Promise.resolve({ teamId }),
  });

/** `GET /api/v1/scope`: Group 2 in 26/27, with the Real Jaén's own export declared (or `teams`). */
const scope = (teams = [2841]) => new Response(JSON.stringify({ data: { competitions: [
  { name: "Primera Federación, Grupo 2", season: "26/27", unique_tournament_id: 17073, tournament_id: 94191, season_id: 97382 },
], teams_with_own_export: teams.map((team_id) => ({ team_id, exports: [] })) } }));

/** Simulated local API: the real service is not reached by these tests. */
function localApi(response: () => Response | Promise<Response>, teams?: number[]) {
  vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
  const fetch = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith("/api/v1/scope") ? scope(teams) : response());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/teams/{teamId}/players", () => {
  it("in the demonstration, the synthetic squad, labelled as such", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    const response = await call("team-real-jaen");
    expect(response.status).toBe(200);
    const body = (await response.json()) as ObservedPlayers;
    expect(body.source).toEqual({ synthetic: true, label: "Demostración · datos sintéticos" });
    expect(body.players.length).toBeGreaterThan(0);
    expect((await call("team-x")).status).toBe(404);
  });

  it("in real mode, returns only real players and says so", async () => {
    const fetch = localApi(
      () =>
        new Response(
          JSON.stringify({
            data: [{ player_id: 966303, name: "Moha Keita", usual_position: "M" }],
            meta: { count: 1, scope: "Jugadores observados en convocatorias." },
          }),
        ),
    );
    const body = (await (await call("team-real-jaen")).json()) as ObservedPlayers;
    expect(body).toEqual({
      teamId: "team-real-jaen",
      players: [{ id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder" }],
      scope: "Jugadores observados en convocatorias.",
      source: { synthetic: false, label: "Datos reales" },
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/v1/teams/2841/players",
      expect.anything(),
    );
    expect(((await source().json()) as SourceInfo).label).toBe("Datos reales");
  });

  it("answers 503 when the real source fails, never synthetic players", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi(() => {
      throw new TypeError("fetch failed");
    });
    const response = await call("team-real-jaen");
    expect(response.status).toBe(503);
    expect(((await response.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });

  it("answers 404 for a team without correspondence in the real source", async () => {
    const fetch = localApi(() => new Response("{}"));
    expect((await call("team-alto")).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("answers 404 for a team with correspondence but no export of its own, without asking for its players", async () => {
    const fetch = localApi(() => new Response("{}"), [2841]);
    expect((await call("sofascore-team-24368")).status).toBe(404);
    expect(fetch.mock.calls.map((c) => String(c[0]))).toEqual(["http://127.0.0.1:8001/api/v1/scope"]);
  });

  it("answers 503 with the reason for a real mode without URL or an invalid DATA_MODE, never synthetic players", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubEnv("LOCAL_DATA_API_URL", "");
    const withoutUrl = await call("team-real-jaen");
    expect(withoutUrl.status).toBe(503);
    expect(((await withoutUrl.json()) as ApiError).error.message).toContain("LOCAL_DATA_API_URL");
    vi.stubEnv("DATA_MODE", "otra");
    expect((await call("team-real-jaen")).status).toBe(503);
  });
});
