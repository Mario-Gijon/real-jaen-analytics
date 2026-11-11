// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ApiError, PlayerProfile } from "@/contract";
import { GET } from "@/app/api/v1/players/[playerId]/profile/route";

const call = (playerId: string) =>
  GET(new Request(`http://localhost/api/v1/players/${playerId}/profile`), {
    params: Promise.resolve({ playerId }),
  });

/** Simulated local API: the real service is not reached by these tests. */
function localApi(response: () => Response | Promise<Response>) {
  vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
  const fetch = vi.fn(async () => response());
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/players/{playerId}/profile", () => {
  it("has no profiles in the demonstration, not even for its synthetic players", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    expect((await call("player-0001")).status).toBe(404);
    expect((await call("sofascore-player-966303")).status).toBe(404);
  });

  it("in real mode, asks the API for the provider id and returns only personal data", async () => {
    const fetch = localApi(
      () =>
        new Response(
          JSON.stringify({
            data: {
              source: "Sofascore", player_id: 966303, name: "Moha Keita", usual_position: "M",
              country: "Senegal", birth_date: "1999-09-18", height_cm: 165, preferred_foot: null,
              fetched_at: "2026-10-21T12:07:39+00:00",
            },
          }),
        ),
    );
    const response = await call("sofascore-player-966303");
    expect(response.status).toBe(200);
    const body = (await response.json()) as PlayerProfile;
    expect(body.player).toEqual({
      id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder",
      birthDate: "1999-09-18", heightCm: 165, preferredFoot: null, country: "Senegal",
    });
    expect(body.source).toEqual({ synthetic: false, label: "Datos reales" });
    expect(JSON.stringify(body)).not.toMatch(/fetched_at|2026-10-21|Sofascore"/);
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8001/api/v1/players/966303", expect.anything());
  });

  it("answers 404 for an unknown player and for ids not derived from the source", async () => {
    const fetch = localApi(() => new Response(JSON.stringify({ error: "Entidad no encontrada" }), { status: 404 }));
    expect((await call("sofascore-player-1")).status).toBe(404);
    expect((await call("player-0001")).status).toBe(404);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("answers 503 when the real source fails, never synthetic data", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi(() => {
      throw new TypeError("fetch failed");
    });
    const response = await call("sofascore-player-966303");
    expect(response.status).toBe(503);
    expect(((await response.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });
});
