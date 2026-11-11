// @vitest-environment node
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AccessSession, ApiError } from "@/contract";
import { DELETE as signOut, GET as session, POST as signIn } from "@/app/api/v1/session/route";
import { config, proxy } from "@/proxy";
import { returnPath } from "@/contract/return-path";
import { clearFailures, hasSession } from "@/server/auth/session";

const PASSWORD = "contrasena-compartida-de-pruebas";
const ORIGIN = "http://localhost:3200";
let folder: string;

function request(path: string, { method = "GET", cookie, origin = ORIGIN, headers = {}, body }:
  { method?: string; cookie?: string; origin?: string | null; headers?: Record<string, string>; body?: unknown } = {}) {
  return new NextRequest(`${ORIGIN}${path}`, {
    method,
    headers: { host: "localhost:3200", ...(origin ? { origin } : {}), ...(cookie ? { cookie } : {}), ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function login(password = PASSWORD, headers: Record<string, string> = {}) {
  return signIn(request("/api/v1/session", { method: "POST", body: { password }, headers }));
}

async function cookie(): Promise<string> {
  const response = await login();
  expect(response.status).toBe(204);
  return response.headers.get("set-cookie")!.split(";")[0];
}

beforeEach(() => {
  folder = mkdtempSync(join(tmpdir(), "rj-access-"));
  writeFileSync(join(folder, "password"), PASSWORD);
  writeFileSync(join(folder, "session-secret"), "a".repeat(64));
  vi.stubEnv("DATA_MODE", "real");
  vi.stubEnv("RJ_ACCESS_PASSWORD_FILE", join(folder, "password"));
  vi.stubEnv("RJ_SESSION_SECRET_FILE", join(folder, "session-secret"));
  vi.stubEnv("RJ_TRUSTED_CLIENT_IP_HEADER", "");
  clearFailures();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  rmSync(folder, { recursive: true, force: true });
});

describe("access session", () => {
  it("lasts 90 days in an HttpOnly, SameSite=Strict cookie for the whole application, Secure under HTTPS", async () => {
    const plain = (await login()).headers.get("set-cookie")!;
    expect(plain).toMatch(/^rj_session=v1\.\d+\.[\w-]+\.[\w-]+; HttpOnly; SameSite=Strict; Path=\/; Max-Age=7776000$/);
    expect(plain).not.toContain(PASSWORD);
    expect((await login(PASSWORD, { "x-forwarded-proto": "https" })).headers.get("set-cookie")).toMatch(/; Secure$/);
    const closed = await signOut(request("/api/v1/session", { method: "DELETE" }));
    expect(closed.headers.get("set-cookie")).toMatch(/^rj_session=; HttpOnly; SameSite=Strict; Path=\/; Max-Age=0$/);
  });

  it("survives a restart: the signing key comes from the secret files, not from the process", async () => {
    const value = await cookie();
    vi.resetModules();
    const restarted = await import("@/server/auth/session");
    expect(restarted.hasSession(request("/", { cookie: value }))).toBe(true);
  });

  it("expires after 90 days and rejects forged or truncated cookies", async () => {
    vi.useFakeTimers({ now: new Date("2026-10-29T12:00:00Z") });
    const value = await cookie();
    vi.setSystemTime(new Date("2027-01-26T11:59:00Z"));
    expect(hasSession(request("/", { cookie: value }))).toBe(true);
    vi.setSystemTime(new Date("2027-01-27T12:00:01Z"));
    expect(hasSession(request("/", { cookie: value }))).toBe(false);
    vi.setSystemTime(new Date("2026-10-29T12:00:00Z"));
    const [name, token] = value.split("=");
    const [version, expires, nonce, signature] = token.split(".");
    for (const forged of [`${name}=${version}.${Number(expires) + 86400}.${nonce}.${signature}`, `${name}=${version}.${expires}.${nonce}.x${signature}`,
      `${name}=${version}.${expires}.${nonce}`, `${name}=`, `other=${token}`]) {
      expect(hasSession(request("/", { cookie: forged })), forged).toBe(false);
    }
  });

  it("revokes every session when the session secret is rotated, or the password changes", async () => {
    const before = await cookie();
    writeFileSync(join(folder, "session-secret"), "b".repeat(64));
    expect(hasSession(request("/", { cookie: before }))).toBe(false);
    const again = await cookie();
    writeFileSync(join(folder, "password"), "otra-contrasena-compartida");
    expect(hasSession(request("/", { cookie: again }))).toBe(false);
  });

  it("accepts only the password, from the same origin, and limits failed attempts for the whole installation", async () => {
    expect((await signIn(request("/api/v1/session", { method: "POST", origin: "https://evil.example", body: { password: PASSWORD } }))).status).toBe(403);
    expect((await signIn(request("/api/v1/session", { method: "POST", origin: null, body: { password: PASSWORD } }))).status).toBe(403);
    for (let attempt = 0; attempt < 5; attempt++) {
      // A different X-Forwarded-For each time: a header the client sets is never trusted to split the limit.
      const failed = await login("wrong-password", { "x-forwarded-for": `10.0.0.${attempt}` });
      expect(failed.status).toBe(401);
      expect(((await failed.json()) as ApiError).error.message).toBe("Contraseña incorrecta.");
    }
    expect((await login(PASSWORD, { "x-forwarded-for": "10.9.9.9" })).status).toBe(429);
  });

  it("splits the limit per client only by the header a trusted proxy overwrites", async () => {
    vi.stubEnv("RJ_TRUSTED_CLIENT_IP_HEADER", "X-Real-IP");
    for (let attempt = 0; attempt < 5; attempt++) await login("wrong-password", { "x-real-ip": "192.0.2.1" });
    expect((await login(PASSWORD, { "x-real-ip": "192.0.2.1" })).status).toBe(429);
    expect((await login(PASSWORD, { "x-real-ip": "192.0.2.2" })).status).toBe(204);
  });

  it("says whether it is required and signed in; 503 when required but not configured", async () => {
    expect(await (await session(request("/api/v1/session"))).json()).toEqual<AccessSession>({ required: true, signedIn: false });
    expect(await (await session(request("/api/v1/session", { cookie: await cookie() }))).json()).toEqual({ required: true, signedIn: true });
    vi.stubEnv("RJ_SESSION_SECRET_FILE", join(folder, "missing"));
    expect((await session(request("/api/v1/session"))).status).toBe(503);
    expect((await login()).status).toBe(503);
    vi.stubEnv("DATA_MODE", "demo");
    vi.stubEnv("RJ_ACCESS_PASSWORD_FILE", "");
    expect(await (await session(request("/api/v1/session"))).json()).toEqual({ required: false, signedIn: true });
  });

  it("returns only to a relative path of this application", () => {
    expect(returnPath("/matches?team=sofascore-team-1")).toBe("/matches?team=sofascore-team-1");
    for (const value of [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "/acceso?next=/x", "matches"]) {
      expect(returnPath(value), String(value)).toBe("/");
    }
  });
});

describe("access gate (proxy)", () => {
  const location = (response: Response) => response.headers.get("location");

  it("sends pages to the access screen with the way back, and refuses data and update routes", async () => {
    expect(location(proxy(request("/")))).toBe(`${ORIGIN}/acceso`);
    expect(location(proxy(request("/matches/sofascore-match-1?view=observed")))).toBe(
      `${ORIGIN}/acceso?next=%2Fmatches%2Fsofascore-match-1%3Fview%3Dobserved`);
    for (const path of ["/api/v1/source", "/api/v1/teams/team-real-jaen/observed-performance", "/api/v1/teams/sofascore-team-135672", "/api/v1/updates/current"]) {
      const refused = proxy(request(path));
      expect(refused.status, path).toBe(401);
      expect(((await refused.json()) as ApiError).error.code).toBe("UNAUTHORIZED");
    }
    expect(proxy(request("/api/v1/updates", { method: "POST" })).status).toBe(401);
  });

  it("lets a session through, and the access screen and session route without one", async () => {
    const value = await cookie();
    for (const path of ["/", "/teams", "/api/v1/source", "/api/v1/updates/current"]) {
      expect(proxy(request(path, { cookie: value })).headers.get("x-middleware-next"), path).toBe("1");
    }
    expect(proxy(request("/acceso")).headers.get("x-middleware-next")).toBe("1");
    expect(proxy(request("/api/v1/session", { method: "POST" })).headers.get("x-middleware-next")).toBe("1");
    expect(location(proxy(request("/acceso?next=%2Fteams", { cookie: value })))).toBe(`${ORIGIN}/teams`);
  });

  it("refuses a state change from another origin before anything else", () => {
    const value = "rj_session=whatever";
    for (const origin of ["https://evil.example", null]) {
      expect(proxy(request("/api/v1/updates", { method: "POST", cookie: value, origin })).status).toBe(403);
      expect(proxy(request("/api/v1/session", { method: "POST", origin })).status).toBe(403);
    }
  });

  it("never serves real data without access configured, and leaves the demonstration open", () => {
    vi.stubEnv("RJ_SESSION_SECRET_FILE", "");
    expect(proxy(request("/api/v1/source")).status).toBe(503);
    expect(location(proxy(request("/teams")))).toBe(`${ORIGIN}/acceso`);
    vi.stubEnv("DATA_MODE", "demo");
    vi.stubEnv("RJ_ACCESS_PASSWORD_FILE", "");
    expect(proxy(request("/api/v1/source")).headers.get("x-middleware-next")).toBe("1");
  });

  it("covers every route but static build files and public images", () => {
    expect(config.matcher).toEqual(["/((?!_next/static|_next/image|favicon\\.ico|icon\\.svg|crests/).*)"]);
    const pattern = /^\/(?!_next\/static|_next\/image|favicon\.ico|icon\.svg|crests\/).*$/;  // the same, as a RegExp
    for (const path of ["/", "/acceso", "/matches", "/api/v1/source", "/api/v1/updates", "/operador"]) expect(pattern.test(path), path).toBe(true);
    for (const path of ["/_next/static/chunk.js", "/crests/real-jaen.webp", "/icon.svg"]) expect(pattern.test(path), path).toBe(false);
  });
});
