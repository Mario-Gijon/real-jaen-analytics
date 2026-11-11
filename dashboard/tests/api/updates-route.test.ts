// @vitest-environment node
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ApiError, UpdateJob, UpdateStart, UpdateStatus } from "@/contract";
import { GET as current } from "@/app/api/v1/updates/current/route";
import { POST as start } from "@/app/api/v1/updates/route";
import { GET as job } from "@/app/api/v1/updates/[updateId]/route";
import { POST as cancel } from "@/app/api/v1/updates/[updateId]/cancel/route";
import { DELETE as logout, POST as login } from "@/app/api/v1/session/route";
import { clearFailures } from "@/server/auth/session";

const PASSWORD = "shared-password-for-tests";
const SESSION_SECRET = "session-secret-for-tests-0123456789abcdef";
const CONTROL_SECRET = "control-secret-for-tests-ABCDEFGHIJKLMNOP";
const ORIGIN = "http://localhost:3000";
const JOB_ID = "7f1c2a3b-4d5e-4f60-8a1b-2c3d4e5f6a7b";

const backendJob = (over: Record<string, unknown> = {}) => ({
  id: JOB_ID, status: "running", phase: "acquiring", cancellable: true, cancel_requested: false, published: false,
  requested_at: "2026-10-28T17:00:00+00:00", finished_at: null, poll_after_seconds: 3, error: null, result: null,
  request_id: "never-forwarded", ...over,
});
const backendStatus = {
  scope: { kind: "pilot", recipe: "sofascore-pilot-16599363-v1", round: 1, match_ids: [16599363], action: "Actualizar piloto",
    label: "Actualización piloto: calendario de la jornada 1 y un partido", description: "Consulta el calendario de la jornada 1 y un partido. No actualiza el resto de la jornada ni de la temporada: los demás partidos se conservan tal como están publicados." },
  active_job: backendJob(), last_job: null, last_published: { at: "2026-10-27T10:00:00+00:00" },
};

let folder: string;
let calls: { url: string; method: string; headers: Headers }[];

function backend(handler: (url: string, method: string) => Response) {
  calls = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, method: init?.method ?? "GET", headers: new Headers(init?.headers) });
    return handler(url, init?.method ?? "GET");
  }));
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function request(path: string, { method = "GET", cookie, origin = ORIGIN, headers = {}, body }:
  { method?: string; cookie?: string; origin?: string | null; headers?: Record<string, string>; body?: unknown } = {}) {
  return new Request(`${ORIGIN}${path}`, {
    method,
    headers: { host: "localhost:3000", ...(origin ? { origin } : {}), ...(cookie ? { cookie } : {}), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function session(): Promise<string> {
  const response = await login(request("/api/v1/session", { method: "POST", body: { password: PASSWORD } }));
  expect(response.status).toBe(204);
  return response.headers.get("set-cookie")!.split(";")[0];
}

beforeEach(() => {
  folder = mkdtempSync(join(tmpdir(), "rj-updates-"));
  writeFileSync(join(folder, "password"), PASSWORD);
  writeFileSync(join(folder, "session"), SESSION_SECRET);
  writeFileSync(join(folder, "control"), CONTROL_SECRET);
  vi.stubEnv("DATA_MODE", "real");
  vi.stubEnv("LOCAL_DATA_API_URL", "http://backend:8001");
  vi.stubEnv("RJ_ACCESS_PASSWORD_FILE", join(folder, "password"));
  vi.stubEnv("RJ_SESSION_SECRET_FILE", join(folder, "session"));
  vi.stubEnv("RJ_CONTROL_SECRET_FILE", join(folder, "control"));
  clearFailures();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  rmSync(folder, { recursive: true, force: true });
});

describe("update routes", () => {
  it("are unavailable in the demonstration or without configuration, without asking the backend", async () => {
    backend(() => json(backendStatus));
    for (const env of [{ DATA_MODE: "demo" }, { RJ_ACCESS_PASSWORD_FILE: join(folder, "missing") }, { RJ_SESSION_SECRET_FILE: "" },
      { RJ_CONTROL_SECRET_FILE: "" }]) {
      for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
      expect(await (await current(request("/api/v1/updates/current"))).json()).toEqual(
        { available: false, scope: null, lastPublished: null, activeJob: null, lastJob: null, lastCoverage: null });
      const refused = await start(request("/api/v1/updates", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() } }));
      expect(refused.status).toBe(409);
      expect(((await refused.json()) as ApiError).error.code).toBe("UPDATE_UNAVAILABLE");
      vi.stubEnv("DATA_MODE", "real");
      vi.stubEnv("RJ_ACCESS_PASSWORD_FILE", join(folder, "password"));
      vi.stubEnv("RJ_SESSION_SECRET_FILE", join(folder, "session"));
      vi.stubEnv("RJ_CONTROL_SECRET_FILE", join(folder, "control"));
    }
    expect(calls).toEqual([]);
  });

  it("show nothing without a session, and with one the real scope with public ids and the jobs", async () => {
    backend(() => json(backendStatus));
    const anonymous = await current(request("/api/v1/updates/current"));
    expect(anonymous.status).toBe(401);
    expect(calls).toEqual([]);
    const response = await current(request("/api/v1/updates/current", { cookie: await session() }));
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = (await response.json()) as UpdateStatus;
    expect(body).toMatchObject({ available: true, lastPublished: { at: "2026-10-27T10:00:00+00:00" }, lastJob: null, lastCoverage: null,
      scope: { kind: "pilot", label: "Actualización piloto: calendario de la jornada 1 y un partido", round: 1,
        description: "Consulta el calendario de la jornada 1 y un partido. No actualiza el resto de la jornada ni de la temporada: los demás partidos se conservan tal como están publicados.", actionLabel: "Actualizar piloto", matchIds: ["sofascore-match-16599363"] } });
    expect(calls[0].url).toBe("http://backend:8001/internal/v1/updates/current");
    expect(calls[0].headers.get("authorization")).toBe(`Bearer ${CONTROL_SECRET}`);
  });

  it("open a signed HttpOnly 90-day session only with the password, from the same origin, and throttle guesses", async () => {
    const wrongOrigin = await login(request("/api/v1/session", { method: "POST", origin: "https://evil.example", body: { password: PASSWORD } }));
    expect(wrongOrigin.status).toBe(403);
    const cookie = (await login(request("/api/v1/session", { method: "POST", body: { password: PASSWORD } }))).headers.get("set-cookie")!;
    expect(cookie).toMatch(/^rj_session=v1\.\d+\.[^;]+; HttpOnly; SameSite=Strict; Path=\/; Max-Age=7776000$/);
    expect(cookie).not.toContain(PASSWORD);
    for (let attempt = 0; attempt < 5; attempt++) {
      expect((await login(request("/api/v1/session", { method: "POST", body: { password: "wrong" } }))).status).toBe(401);
    }
    expect((await login(request("/api/v1/session", { method: "POST", body: { password: PASSWORD } }))).status).toBe(429);
    const closed = await logout(request("/api/v1/session", { method: "DELETE" }));
    expect(closed.headers.get("set-cookie")).toContain("Max-Age=0");
  });

  it("start one update per key with the access session, never forwarding the secret or internal fields", async () => {
    backend((url, method) => method === "POST" ? json({ job: backendJob(), reused: false }, 202) : json(backendStatus));
    const key = crypto.randomUUID();
    expect((await start(request("/api/v1/updates", { method: "POST", headers: { "Idempotency-Key": key } }))).status).toBe(401);
    const cookie = await session();
    expect((await start(request("/api/v1/updates", { method: "POST", cookie, origin: "https://evil.example", headers: { "Idempotency-Key": key } }))).status).toBe(403);
    expect((await start(request("/api/v1/updates", { method: "POST", cookie, headers: { "Idempotency-Key": "x" } }))).status).toBe(400);
    expect(calls).toEqual([]);
    const response = await start(request("/api/v1/updates", { method: "POST", cookie, headers: { "Idempotency-Key": key } }));
    expect(response.status).toBe(202);
    expect(response.headers.get("location")).toBe(`/api/v1/updates/${JOB_ID}`);
    const text = await response.text();
    expect(text).not.toContain(CONTROL_SECRET);
    expect(text).not.toContain("never-forwarded");
    const body = JSON.parse(text) as UpdateStart;
    expect(body.job).toMatchObject({ id: JOB_ID, status: "running", phase: "acquiring", cancellable: true, published: false });
    expect(calls[0]).toMatchObject({ url: "http://backend:8001/internal/v1/updates", method: "POST" });
    expect(calls[0].headers.get("idempotency-key")).toBe(key);
    const status = (await (await current(request("/api/v1/updates/current", { cookie }))).json()) as UpdateStatus;
    expect(status.activeJob?.id).toBe(JOB_ID);
  });

  it("follow and cancel a job; a job past acquiring answers 409, a missing one 404, a silent backend 503", async () => {
    const cookie = await session();
    backend((url) => url.endsWith("/cancel") ? json({ error: "not_cancellable" }, 409) : url.endsWith(JOB_ID) ? json(backendJob()) : json({ error: "not_found" }, 404));
    const params = (updateId: string) => ({ params: Promise.resolve({ updateId }) });
    expect(((await (await job(request(`/api/v1/updates/${JOB_ID}`, { cookie }), params(JOB_ID))).json()) as UpdateJob).phase).toBe("acquiring");
    expect((await job(request("/api/v1/updates/other", { cookie }), params("other"))).status).toBe(404);
    const refused = await cancel(request(`/api/v1/updates/${JOB_ID}/cancel`, { method: "POST", cookie }), params(JOB_ID));
    expect(refused.status).toBe(409);
    expect(((await refused.json()) as ApiError).error.code).toBe("CONFLICT");
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("connect ECONNREFUSED"); }));
    const down = await job(request(`/api/v1/updates/${JOB_ID}`, { cookie }), params(JOB_ID));
    expect(down.status).toBe(503);
    expect(((await down.json()) as ApiError).error.code).toBe("UPSTREAM_UNAVAILABLE");
  });

  it("map a group update's coverage to public ids, keeping new, kept and pending apart", async () => {
    const cookie = await session();
    const done = backendJob({ status: "completed", phase: "finished", cancellable: false, published: true,
      finished_at: "2026-10-28T18:00:00+00:00", result: { published_at: "2026-10-28T18:00:00+00:00", reused_bundle: false,
        coverage: { new: 35, kept: 608, deferred: [16599970], restricted: [16600001], detail_rounds: [9],
          new_matches: [{ id: 17000001, replaces: [16599919] }],
          pending: [{ event_id: 16599967, resources: [{ resource: "statistics", reason: "empty", kept: false }] }],
          comments: { new: [16599967], empty: [16599967], kept: [], pending: [], failed: [] },
          names: { "16599967": "Real Jaén – CE Europa" } } } });
    backend(() => json(done));
    const params = { params: Promise.resolve({ updateId: JOB_ID }) };
    const body = (await (await job(request(`/api/v1/updates/${JOB_ID}`, { cookie }), params)).json()) as UpdateJob;
    expect(body.result?.coverage).toEqual({ newResources: 35, keptResources: 608, deferredMatchIds: ["sofascore-match-16599970"],
      restrictedMatchIds: ["sofascore-match-16600001"], detailRounds: [9],
      newMatches: [{ matchId: "sofascore-match-17000001", replaces: ["sofascore-match-16599919"] }],
      pending: [{ matchId: "sofascore-match-16599967", resources: [{ resource: "statistics", reason: "empty", kept: false }] }],
      comments: { newMatchIds: [], emptyMatchIds: ["sofascore-match-16599967"], keptMatchIds: [], pendingMatchIds: [], failed: [] },
      matchNames: { "sofascore-match-16599967": "Real Jaén – CE Europa" } });
  });

  it("show the last publication's coverage after a later update failed, only with a session", async () => {
    const failed = backendJob({ status: "failed", phase: "finished", cancellable: false, finished_at: "2026-10-28T19:00:00+00:00",
      error: { code: "ACQUISITION_FAILED", message: "rechazada", retryable: false } });
    backend(() => json({ ...backendStatus, active_job: null, last_job: failed, last_published: { at: "2026-10-28T18:00:00+00:00",
      coverage: { new: 35, kept: 608, deferred: [16599970], new_matches: [], pending: [], names: { "16599970": "Huesca – UD Ibiza" } } } }));
    expect((await current(request("/api/v1/updates/current"))).status).toBe(401);
    const cookie = await session();
    const status = (await (await current(request("/api/v1/updates/current", { cookie }))).json()) as UpdateStatus;
    expect(status.lastJob?.status).toBe("failed");
    expect(status.lastCoverage).toMatchObject({ deferredMatchIds: ["sofascore-match-16599970"],
      matchNames: { "sofascore-match-16599970": "Huesca – UD Ibiza" } });
  });

  it("reject a forged or expired session cookie", async () => {
    backend(() => json(backendStatus));
    const cookie = await session();
    const [name, value] = cookie.split("=");
    const [version, expires, nonce, signature] = value.split(".");
    for (const forged of [`${name}=${version}.${expires}.${nonce}.AAAA`, `${name}=${version}.1.${nonce}.${signature}`,
      `${name}=v0.${expires}.${nonce}.${signature}`, `${name}=`]) {
      expect((await start(request("/api/v1/updates", { method: "POST", cookie: forged, headers: { "Idempotency-Key": crypto.randomUUID() } }))).status).toBe(401);
    }
  });
});
