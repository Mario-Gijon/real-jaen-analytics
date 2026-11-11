import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";

/**
 * Access to the whole application (README.md): one shared password in a server file
 * (`RJ_ACCESS_PASSWORD_FILE`) exchanged for a signed, HttpOnly, SameSite=Strict session cookie that lasts 90 days.
 * The signing key derives from a persistent secret file (`RJ_SESSION_SECRET_FILE`) and the password, so sessions
 * survive restarts and recreated containers, and rotating that secret, or changing the password, revokes every
 * session at once. Nothing but the signed cookie reaches the browser; the password is never stored there.
 * The internal control secret (`RJ_CONTROL_SECRET_FILE`) is read here only for the server's calls to the backend.
 */

export const SESSION_COOKIE = "rj_session";
export const SESSION_SECONDS = 90 * 24 * 60 * 60;
const VERSION = "v1";
const FAILURES_ALLOWED = 5;
const FAILURE_WINDOW_MS = 15 * 60 * 1000;
const failures = new Map<string, { count: number; until: number }>();

/** A secret file of `min`-256 printable ASCII characters, or `null` (missing, unreadable or malformed). */
function secretFile(variable: string, min: number): Buffer | null {
  const path = process.env[variable];
  if (!path) return null;
  try {
    const value = readFileSync(path, "latin1").trim();
    if (value.length < min || value.length > 256 || !/^[\x21-\x7e]+$/.test(value)) return null;
    return Buffer.from(value, "ascii");
  } catch {
    return null;
  }
}

/** The shared password: typed by people, so 12 characters at least (the attempt limit does the rest). */
const accessPassword = () => secretFile("RJ_ACCESS_PASSWORD_FILE", 12);
const sessionSecret = () => secretFile("RJ_SESSION_SECRET_FILE", 32);
export const controlSecret = () => secretFile("RJ_CONTROL_SECRET_FILE", 32);

export function accessConfigured(): boolean {
  return accessPassword() !== null && sessionSecret() !== null;
}

/**
 * Real data are never served without access control; the demonstration (`DATA_MODE=demo`, invented data) is open
 * unless access is configured.
 */
export function accessRequired(): boolean {
  return (process.env.DATA_MODE || "real") === "real" || accessConfigured();
}

const digest = (value: Buffer | string) => createHash("sha256").update(value).digest();

/** HMAC key of the sessions: the persistent secret bound to the current password. `null` when not configured. */
function signingKey(): Buffer | null {
  const secret = sessionSecret();
  const password = accessPassword();
  if (!secret || !password) return null;
  return createHmac("sha256", secret).update("rj-session|").update(digest(password)).digest();
}

/** Constant-time comparison with the configured password. */
export function validPassword(candidate: unknown): boolean {
  const password = accessPassword();
  if (!password || typeof candidate !== "string" || candidate.length > 256) return false;
  return timingSafeEqual(digest(password), digest(candidate));
}

function secure(request: Request): boolean {
  return new URL(request.url).protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
}

/** A new session cookie (`v1.<expires>.<nonce>.<signature>`), valid for 90 days. */
export function sessionCookie(request: Request): string {
  const key = signingKey();
  if (!key) throw new Error("Access not configured");
  const payload = `${VERSION}.${Math.floor(Date.now() / 1000) + SESSION_SECONDS}.${randomBytes(16).toString("base64url")}`;
  const signature = createHmac("sha256", key).update(payload).digest("base64url");
  return `${SESSION_COOKIE}=${payload}.${signature}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_SECONDS}${secure(request) ? "; Secure" : ""}`;
}

export function clearedCookie(request: Request): string {
  return `${SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0${secure(request) ? "; Secure" : ""}`;
}

/** Whether the request carries a valid, unexpired session signed with the current key. */
export function hasSession(request: Request): boolean {
  const key = signingKey();
  if (!key) return false;
  const cookie = (request.headers.get("cookie") ?? "")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  const [version, expires, nonce, signature, ...rest] = (cookie?.slice(SESSION_COOKIE.length + 1) ?? "").split(".");
  if (version !== VERSION || !expires || !nonce || !signature || rest.length || !/^\d+$/.test(expires)) return false;
  const expected = Buffer.from(createHmac("sha256", key).update(`${version}.${expires}.${nonce}`).digest("base64url"));
  const given = Buffer.from(signature);
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return false;
  const now = Date.now() / 1000;
  return Number(expires) > now && Number(expires) <= now + SESSION_SECONDS + 60;
}

/** CSRF: a state change must come from this same origin (in addition to the SameSite cookie). */
export function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/**
 * Who the attempt limit counts. A header such as X-Forwarded-For can be set by any client, so none is trusted by
 * default and every failed attempt counts for the whole installation. Behind a proxy that overwrites one header
 * with the client's address, `RJ_TRUSTED_CLIENT_IP_HEADER` names it and the limit becomes per address.
 */
function client(request: Request): string {
  const header = process.env.RJ_TRUSTED_CLIENT_IP_HEADER?.trim().toLowerCase();
  if (!header) return "installation";
  return `${header}:${request.headers.get(header)?.trim() || "unknown"}`;
}

/** Too many failed passwords in the last 15 minutes. */
export function throttled(request: Request): boolean {
  const entry = failures.get(client(request));
  return entry !== undefined && entry.until > Date.now() && entry.count >= FAILURES_ALLOWED;
}

export function recordFailure(request: Request) {
  const key = client(request);
  const entry = failures.get(key);
  const now = Date.now();
  failures.set(key, entry && entry.until > now ? { count: entry.count + 1, until: entry.until } : { count: 1, until: now + FAILURE_WINDOW_MS });
}

/** Tests start from no recorded failures. */
export function clearFailures() {
  failures.clear();
}
