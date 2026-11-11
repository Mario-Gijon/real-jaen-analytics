// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError, ObservedTeam } from "@/contract";
import { GET } from "@/app/api/v1/teams/[teamId]/route";
import { clearTeamNameCache } from "@/server/local-api/local-api-source";

const call = (teamId: string) => GET(new Request(`http://localhost/api/v1/teams/${teamId}`), {
  params: Promise.resolve({ teamId }),
});

beforeEach(() => clearTeamNameCache());
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/teams/{teamId}", () => {
  it("serves a known team's identity through the adapter without requiring an export", async () => {
    vi.stubEnv("DATA_MODE", "real");
    vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
    const fetch = vi.fn(async () => new Response(JSON.stringify({ data: { team_id: 135672, name: "CE Europa" } })));
    vi.stubGlobal("fetch", fetch);
    const response = await call("sofascore-team-135672");
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual<ObservedTeam>({ id: "sofascore-team-135672", name: "CE Europa" });
    expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:8001/api/v1/teams/135672", expect.anything());
  });

  it("returns not found for an unknown identity and 503 for a failing local API", async () => {
    vi.stubEnv("DATA_MODE", "real");
    vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
    vi.stubGlobal("fetch", vi.fn(async () => new Response("Not found", { status: 404 })));
    expect((await call("team-unknown")).status).toBe(404);
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    vi.spyOn(console, "error").mockImplementation(() => {});
    const failed = await call("sofascore-team-135672");
    expect(failed.status).toBe(503);
    expect(((await failed.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });

  it("has no real team identity in demonstration mode", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    expect((await call("sofascore-team-135672")).status).toBe(404);
  });
});
