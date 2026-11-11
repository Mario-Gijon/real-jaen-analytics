import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  getCompetitions,
  getAllEnrichedComments,
  getEnrichedComments,
  getMatch,
  getObservedMatchDetail,
  getObservedPlayers,
  getObservedTeam,
  getPlayer,
  getPlayerObservedMatches,
  getPlayerProfile,
  getSeasonMatches,
  getSeasonTeams,
  getSource,
  getTeamObservedMatches,
  getTeamSeason,
} from "@/client/api";

function answer(body: unknown, status = 200) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(body), { status }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const urls = (fetch: ReturnType<typeof answer>) =>
  fetch.mock.calls.map((call) => (call as unknown[])[0]);

afterEach(() => vi.unstubAllGlobals());

describe("typed client", () => {
  it("builds every /api/v1 URL, so screens never do", async () => {
    const fetch = answer([]);
    await getSource();
    await getCompetitions();
    await getSeasonMatches("s 1");
    await getSeasonMatches("s1", { round: 3, teamId: "team-a" });
    await getMatch("m1");
    await getEnrichedComments({ sourceEventId: "16600011", category: "shot", originalType: "shotSaved", limit: 25, offset: 50 });
    await getSeasonTeams("s1");
    await getTeamSeason("s1", "team-a");
    await getPlayer("p1", "s1");
    await getObservedPlayers("team a");
    await getObservedTeam("sofascore-team-135672");
    await getPlayerProfile("sofascore-player-1");
    await getPlayerObservedMatches("sofascore-player-1");
    await getTeamObservedMatches("team-real-jaen");
    await getObservedMatchDetail("sofascore-match-1");
    expect(urls(fetch)).toEqual([
      "/api/v1/source",
      "/api/v1/competitions",
      "/api/v1/seasons/s%201/matches",
      "/api/v1/seasons/s1/matches?round=3&team=team-a",
      "/api/v1/matches/m1",
      "/api/v1/enrichment/comments?source_event_id=16600011&category=shot&original_type=shotSaved&limit=25&offset=50",
      "/api/v1/seasons/s1/teams",
      "/api/v1/seasons/s1/teams/team-a",
      "/api/v1/players/p1?season=s1",
      "/api/v1/teams/team%20a/players",
      "/api/v1/teams/sofascore-team-135672",
      "/api/v1/players/sofascore-player-1/profile",
      "/api/v1/players/sofascore-player-1/observed-matches",
      "/api/v1/teams/team-real-jaen/observed-matches",
      "/api/v1/matches/sofascore-match-1/observed",
    ]);
  });

  it("passes the abort signal through", async () => {
    const fetch = answer([]);
    const controller = new AbortController();
    await getCompetitions({ signal: controller.signal });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/competitions",
      expect.objectContaining({ signal: controller.signal }),
    );
  });

  it("loads every result page internally and returns the full event list", async () => {
    const item = (id: number, captureId = 42) => ({
      id, captureId, position: id, text: `Evento ${id}`, originalType: "matchStarted", minute: null,
      periodName: null, isHome: null, score: null, category: "match_flow", attributes: {}, participants: [],
    });
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const offset = Number(new URL(String(input), "http://localhost").searchParams.get("offset"));
      const items = offset === 0 ? Array.from({ length: 100 }, (_, index) => item(index + 1))
        : Array.from({ length: 20 }, (_, index) => item(index + 101));
      return new Response(JSON.stringify({ enrichmentVersion: "rules-v1", total: 120, limit: 100, offset, items }));
    });
    vi.stubGlobal("fetch", fetch);

    const page = await getAllEnrichedComments("16600011");
    expect(page.items).toHaveLength(120);
    expect(page.items[0].id).toBe(1);
    expect(page.items[119].id).toBe(120);
    expect(fetch.mock.calls.map(([input]) => input)).toEqual([
      "/api/v1/enrichment/comments?source_event_id=16600011&limit=100&offset=0",
      "/api/v1/enrichment/comments?source_event_id=16600011&limit=100&offset=100",
    ]);
  });

  it("does not join pages from different selected captures", async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL) => {
      const offset = Number(new URL(String(input), "http://localhost").searchParams.get("offset"));
      const item = { id: offset ? "second" : "first", captureId: offset ? 43 : 42, position: offset,
        text: "Evento", originalType: "matchStarted", minute: null, periodName: null, isHome: null,
        score: null, category: "match_flow", attributes: {}, participants: [] };
      return new Response(JSON.stringify({ enrichmentVersion: "rules-v1", total: 2, limit: 1, offset, items: [item] }));
    });
    vi.stubGlobal("fetch", fetch);
    await expect(getAllEnrichedComments("16600011")).rejects.toMatchObject({ code: "ENRICHMENT_CHANGED" });
  });

  it("raises the contract error", async () => {
    answer({ error: { code: "NOT_FOUND", message: "Partido no encontrado." } }, 404);
    await expect(getMatch("x")).rejects.toEqual(
      expect.objectContaining({
        status: 404,
        code: "NOT_FOUND",
        message: "Partido no encontrado.",
      }),
    );
    await expect(getMatch("x")).rejects.toBeInstanceOf(ApiRequestError);
  });
});
