// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { createLocalApiMatchComments, UpstreamUnavailableError } from "@/server/local-api/local-api-source";

const baseUrl = "http://127.0.0.1:8001";
const publicId = "sofascore-match-16600011";
const body = {
  matchId: publicId,
  availability: "available",
  capturedAt: "2026-11-03T16:53:19+00:00",
  responseSha256: "e2eb4e64d75ced718cc1f329b90a51cefd804a9bd71993a7b96532736ada85bb",
  lastAttempt: { availability: "available", occurredAt: "2026-11-03T16:53:19+00:00", httpStatus: 200, errorCode: null },
  comments: [{ id: "41234598", position: 0, text: "Final", type: "matchEnded", minute: null, periodName: null,
    isHome: null, score: null, incidentId: null, participants: {} }],
};

describe("local API adapter: match comments", () => {
  it("requests the public match route and validates the separate narrative contract", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(body)));
    const load = createLocalApiMatchComments({ baseUrl, fetch });
    expect(await load(publicId)).toEqual(body);
    expect(fetch).toHaveBeenCalledWith(`${baseUrl}/api/v1/matches/${publicId}/comments`, expect.objectContaining({ cache: "no-store" }));
  });

  it("does not request a synthetic id and rejects malformed narrative data", async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify(body)));
    const load = createLocalApiMatchComments({ baseUrl, fetch });
    expect(await load("match-r01-01")).toBeNull();
    expect(fetch).not.toHaveBeenCalled();
    const malformed = createLocalApiMatchComments({ baseUrl, fetch: async () => new Response(JSON.stringify({ ...body, comments: [] })) });
    await expect(malformed(publicId)).rejects.toBeInstanceOf(UpstreamUnavailableError);
  });
});
