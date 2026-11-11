// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { createLocalApiEnrichedComments } from "@/server/local-api/enrichment-comments";

const response = {
  enrichment_version: "rules-v1",
  total: 1,
  limit: 25,
  offset: 0,
  items: [{
    comment: {
      source_comment_id: "narrative-1", source_position: 4,
      text: " ¡Gol! Real Jaén 1, Algeciras 0. ", type: "scoreChange", minute: 27,
      period_name: "1ST", is_home: 1, home_score: 1, away_score: 0,
    },
    provenance: { capture_id: 31 },
    enrichment: {
      category: "goal",
      attributes: { goal_status: "valid", surface: null, contexts: [] },
      participants: [{ source_role: "player", role: "scorer", normalizer_role: "scorer", source_id: 597500,
        original: { name: "Delantero" } }],
    },
  }],
};

describe("local API adapter: enriched narrative comments", () => {
  it("maps the paginated REST response without losing original text or nullable attributes", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(response)));
    const load = createLocalApiEnrichedComments({ baseUrl: "http://127.0.0.1:8001/", fetch });
    await expect(load({ sourceEventId: "16600011", category: "goal", limit: 25, offset: 0 })).resolves.toEqual({
      enrichmentVersion: "rules-v1", total: 1, limit: 25, offset: 0,
      items: [{ id: "narrative-1", captureId: 31, position: 4, text: " ¡Gol! Real Jaén 1, Algeciras 0. ",
        originalType: "scoreChange", minute: 27, periodName: "1ST", isHome: true, score: { home: 1, away: 0 }, category: "goal",
        attributes: { goal_status: "valid", surface: null, contexts: [] },
        participants: [{ sourceRole: "player", role: "scorer", normalizerRole: "scorer", sourceId: 597500,
          name: "Delantero", player: { id: "sofascore-player-597500", name: "Delantero", position: null } }] }],
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/v1/enrichment/comments?source_event_id=16600011&category=goal&limit=25&offset=0",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("rejects malformed original records or attributes", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({
      ...response,
      items: [{ ...response.items[0], enrichment: { category: "goal", attributes: { surface: 4 } } }],
    })));
    const load = createLocalApiEnrichedComments({ baseUrl: "http://127.0.0.1:8001", fetch });
    await expect(load({ sourceEventId: "16600011" })).rejects.toThrow("Respuesta no válida");
  });

  it("links only valid structured numeric player IDs and leaves text-only names unlinked", async () => {
    const payload: { items: Array<{ enrichment: { participants: unknown[] } }> } = structuredClone(response);
    payload.items[0].enrichment.participants = [
      { source_role: "player", role: "scorer", normalizer_role: "scorer", source_id: "597501", original: { name: "Goleador" } },
      { source_role: "player", role: null, normalizer_role: "foul_committed", source_id: "not-an-id", original: { name: "Nombre sin ID" } },
    ];
    const fetch = vi.fn(async () => new Response(JSON.stringify(payload)));
    const load = createLocalApiEnrichedComments({ baseUrl: "http://127.0.0.1:8001", fetch });
    const page = await load({ sourceEventId: "16600011" });
    expect(page?.items[0].participants).toEqual([
      { sourceRole: "player", role: "scorer", normalizerRole: "scorer", sourceId: "597501", name: "Goleador",
        player: { id: "sofascore-player-597501", name: "Goleador", position: null } },
      { sourceRole: "player", role: null, normalizerRole: "foul_committed", sourceId: null, name: "Nombre sin ID", player: null },
    ]);
  });
});
