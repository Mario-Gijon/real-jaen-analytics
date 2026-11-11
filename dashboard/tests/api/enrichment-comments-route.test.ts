// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/enrichment/comments/route";

const payload = {
  enrichment_version: "rules-v1", total: 1, limit: 25, offset: 0,
  items: [{ comment: { source_comment_id: "1", source_position: 0, text: "Comentario original", type: "shotSaved", minute: 12,
    period_name: "1ST", is_home: 0, home_score: 0, away_score: 1 },
    provenance: { capture_id: 8 },
    enrichment: { category: "shot", attributes: { surface: "right_foot" }, participants: [
      { source_role: "player", role: "shooter", normalizer_role: "player", source_id: 51, original: { name: "Jugador" } },
    ] } }],
};

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/enrichment/comments BFF", () => {
  it("forwards the event and filters, then returns the typed no-store page", async () => {
    vi.stubEnv("DATA_MODE", "real");
    vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
    const fetch = vi.fn(async () => new Response(JSON.stringify(payload)));
    vi.stubGlobal("fetch", fetch);
    const response = await GET(new Request(
      "http://localhost/api/v1/enrichment/comments?source_event_id=16600011&category=shot&original_type=shotSaved&limit=25&offset=0",
    ));
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({
      enrichmentVersion: "rules-v1", total: 1, limit: 25, offset: 0,
      items: [{ id: "1", captureId: 8, position: 0, text: "Comentario original", originalType: "shotSaved", minute: 12,
        periodName: "1ST", isHome: false, score: { home: 0, away: 1 }, category: "shot", attributes: { surface: "right_foot" }, participants: [
          { sourceRole: "player", role: "shooter", normalizerRole: "player", sourceId: 51, name: "Jugador",
            player: { id: "sofascore-player-51", name: "Jugador", position: null } },
        ] }],
    });
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8001/api/v1/enrichment/comments?source_event_id=16600011&category=shot&original_type=shotSaved&limit=25&offset=0",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("rejects invalid event IDs before contacting sports-data", async () => {
    vi.stubEnv("DATA_MODE", "real");
    vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const response = await GET(new Request("http://localhost/api/v1/enrichment/comments?source_event_id=unknown"));
    expect(response.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
});
