// @vitest-environment node
import { createHash } from "node:crypto";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/players/[playerId]/photo/[sha256]/route";

function webpFixture() {
  const bytes = new Uint8Array(20);
  bytes.set([0x52, 0x49, 0x46, 0x46]); // RIFF
  new DataView(bytes.buffer).setUint32(4, 12, true);
  bytes.set([0x57, 0x45, 0x42, 0x50], 8); // WEBP
  bytes.set([0x56, 0x50, 0x38, 0x4c], 12); // VP8L chunk marker
  return bytes;
}

const bytes = webpFixture();
const sha256 = createHash("sha256").update(bytes).digest("hex");
const playerId = "sofascore-player-966303";
const call = (id = playerId, hash = sha256, headers?: HeadersInit) =>
  GET(new Request(`http://localhost/api/v1/players/${id}/photo/${hash}`, { headers }), {
    params: Promise.resolve({ playerId: id, sha256: hash }),
  });

function localApi(response: () => Response | Promise<Response>) {
  vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001/");
  const fetch = vi.fn(async (...args: Parameters<typeof globalThis.fetch>) => {
    void args;
    return response();
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("GET /api/v1/players/{playerId}/photo/{sha256}", () => {
  it("serves the exact verified WebP through Sports Data with private cache headers", async () => {
    const fetch = localApi(() => new Response(bytes, { headers: { "content-type": "image/webp" } }));
    const response = await call();

    expect(response.status).toBe(200);
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(bytes);
    expect(response.headers.get("content-type")).toBe("image/webp");
    expect(response.headers.get("cache-control")).toBe("private, max-age=31536000, immutable");
    expect(response.headers.get("etag")).toBe(`"${sha256}"`);
    expect(response.headers.get("x-content-type-options")).toBe("nosniff");
    expect(fetch).toHaveBeenCalledOnce();
    expect(fetch.mock.calls[0][0]).toBe(`http://127.0.0.1:8001/api/v1/players/966303/photo/${sha256}`);
    const init = fetch.mock.calls[0][1]!;
    expect(init.cache).toBe("no-store");
    expect(new Headers(init.headers).get("accept")).toBe("image/webp");
  });

  it("does not fetch for an invalid public id or hash", async () => {
    const fetch = localApi(() => new Response(bytes, { headers: { "content-type": "image/webp" } }));
    expect((await call("player-0001")).status).toBe(404);
    expect((await call(playerId, "A".repeat(64))).status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("does not query Sports Data in demo mode", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    expect((await call()).status).toBe(404);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("returns 404 when the current photo does not exist", async () => {
    const fetch = localApi(() => new Response("", { status: 404 }));
    expect((await call()).status).toBe(404);
    expect(fetch).toHaveBeenCalledOnce();
  });

  it.each([
    ["unsupported media type", () => new Response(bytes, { headers: { "content-type": "image/png" } })],
    ["bytes with a different hash", () => new Response(new Uint8Array(20), { headers: { "content-type": "image/webp" } })],
    ["malformed WebP bytes", () => new Response(new Uint8Array([1, 2, 3]), { headers: { "content-type": "image/webp" } })],
  ])("returns 503 for %s from the upstream", async (_case, response) => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    localApi(response);
    expect((await call()).status).toBe(503);
  });

  it("returns 503 when Sports Data is unavailable or not configured", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const fetch = localApi(() => new Response("", { status: 500 }));
    expect((await call()).status).toBe(503);
    expect(fetch).toHaveBeenCalledOnce();
    vi.stubEnv("LOCAL_DATA_API_URL", "");
    expect((await call()).status).toBe(503);
  });

  it("answers 304 only after confirming the current source bytes match the requested version", async () => {
    const fetch = localApi(() => new Response(bytes, { headers: { "content-type": "image/webp" } }));
    const response = await call(playerId, sha256, { "if-none-match": `"${sha256}"` });
    expect(response.status).toBe(304);
    expect(response.headers.get("etag")).toBe(`"${sha256}"`);
    expect(fetch).toHaveBeenCalledOnce();
    expect(new Headers(fetch.mock.calls[0][1]!.headers).get("if-none-match")).toBe(`"${sha256}"`);
  });
});
