import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";

/**
 * Shared read access (`RJ_ACCESS_PASSWORD_FILE`) and optional update access (`RJ_UPDATE_PASSWORD_FILE`) are
 * exchanged for signed, HttpOnly, SameSite=Strict cookies. Each cookie is bound to its granted capability and
 * the password that granted it. Rotating that password or `RJ_SESSION_SECRET_FILE` revokes the matching sessions.
 * The internal control secret (`RJ_CONTROL_SECRET_FILE`) is only used for calls to Sports Data.
 */

export const SESSION_COOKIE = "rj_session";
export const SESSION_SECONDS = 90 * 24 * 60 * 60;
const VERSION = "v2";
const FAILURES_ALLOWED = 5;
const FAILURE_WINDOW_MS = 15 * 60 * 1000;
const failures = new Map<string, { count: number; until: number }>();

/** A secret file of `min` printable ASCII characters, or `null` (missing, unreadable or malformed). */
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

/** Read password: typed by people; keep the existing 12-character configuration minimum. */
const accessPassword = () => secretFile("RJ_ACCESS_PASSWORD_FILE", 12);
/** Update password: separately configured and specifically allowed to have the requested 9-character minimum. */
function updatePassword(): Buffer | null {
  const normal = accessPassword();
  const update = secretFile("RJ_UPDATE_PASSWORD_FILE", 9);
  if (!normal || !update || timingSafeEqual(digest(normal), digest(update))) return null;
  return update;
}
const sessionSecret = () => secretFile("RJ_SESSION_SECRET_FILE", 32);
export const controlSecret = () => secretFile("RJ_CONTROL_SECRET_FILE", 32);

export function accessConfigured(): boolean {
  return accessPassword() !== null && sessionSecret() !== null;
}

/** Whether the distinct update password is configured and can grant administrative access. */
export function updateAccessConfigured(): boolean {
  return accessConfigured() && updatePassword() !== null;
}

/**
 * Real data are never served without access control; the demonstration (`DATA_MODE=demo`, invented data) is open
 * unless access is configured.
 */
export function accessRequired(): boolean {
  return (process.env.DATA_MODE || "real") === "real" || accessConfigured();
}

const digest = (value: Buffer | string) => createHash("sha256").update(value).digest();

type Capability = "read" | "update";

/** A capability-specific HMAC key: cookies cannot be upgraded by editing their capability field. */
function signingKey(capability: Capability): Buffer | null {
  const secret = sessionSecret();
  const password = capability === "update" ? updatePassword() : accessPassword();
  if (!secret || !password) return null;
  return createHmac("sha256", secret)
    .update(`rj-session|${VERSION}|${capability}|`)
    .update(digest(password))
    .digest();
}

/** Returns the capability granted by the configured password, or null for an invalid password. */
export function passwordCapability(candidate: unknown): boolean | null {
  const normal = accessPassword();
  const update = updatePassword();
  if (!normal || typeof candidate !== "string" || candidate.length > 256) return null;
  const candidateDigest = digest(candidate);
  const normalMatch = timingSafeEqual(digest(normal), candidateDigest);
  const updateMatch = update !== null && timingSafeEqual(digest(update), candidateDigest);
  if (normalMatch) return false;
  return updateMatch ? true : null;
}

function secure(request: Request): boolean {
  return new URL(request.url).protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
}

/** A capability-bound 90-day cookie. Version v2 intentionally invalidates earlier all-powerful v1 sessions. */
export function sessionCookie(request: Request, canUpdate: boolean): string {
  const capability: Capability = canUpdate ? "update" : "read";
  const key = signingKey(capability);
  if (!key) throw new Error("Access not configured");
  const payload = `${VERSION}.${Math.floor(Date.now() / 1000) + SESSION_SECONDS}.${randomBytes(16).toString("base64url")}.${capability}`;
  const signature = createHmac("sha256", key).update(payload).digest("base64url");
  return `${SESSION_COOKIE}=${payload}.${signature}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${SESSION_SECONDS}${secure(request) ? "; Secure" : ""}`;
}

export function clearedCookie(request: Request): string {
  return `${SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0${secure(request) ? "; Secure" : ""}`;
}

/** The signed capability, or null when the cookie is absent, invalid, expired or revoked. */
export function sessionCapability(request: Request): boolean | null {
  const cookie = (request.headers.get("cookie") ?? "")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  const [version, expires, nonce, capability, signature, ...rest] =
    (cookie?.slice(SESSION_COOKIE.length + 1) ?? "").split(".");
  if (version !== VERSION || !expires || !nonce || !signature || rest.length
      || (capability !== "read" && capability !== "update") || !/^\d+$/.test(expires)) return null;
  const key = signingKey(capability);
  if (!key) return null;
  const expected = Buffer.from(createHmac("sha256", key).update(`${version}.${expires}.${nonce}.${capability}`).digest("base64url"));
  const given = Buffer.from(signature);
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) return null;
  const now = Date.now() / 1000;
  if (Number(expires) <= now || Number(expires) > now + SESSION_SECONDS + 60) return null;
  return capability === "update";
}

/** Whether the request carries a valid, unexpired session of either capability. */
export function hasSession(request: Request): boolean {
  return sessionCapability(request) !== null;
}

/** Whether the request carries a valid session signed with the update password. */
export function canUpdate(request: Request): boolean {
  return sessionCapability(request) === true;
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
 * default and every failed attempt counts for the whole installation. Behind a proxy that overwrites one header,
 * `RJ_TRUSTED_CLIENT_IP_HEADER` names it and the limit becomes per address.
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
