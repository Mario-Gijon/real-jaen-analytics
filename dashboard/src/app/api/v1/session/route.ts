import type { NextResponse } from "next/server";
import type { AccessSession, ApiError } from "@/contract";
import {
  accessConfigured,
  accessRequired,
  clearedCookie,
  hasSession,
  recordFailure,
  sameOrigin,
  sessionCookie,
  throttled,
  validPassword,
} from "@/server/auth/session";
import { empty, failure, noStore } from "@/server/updates/responses";

/**
 * Whether this installation asks for the password and whether the request has a session (no secret in it).
 * 503 when it asks for one but access is not configured: the container's healthcheck relies on it.
 */
export async function GET(request: Request): Promise<NextResponse<AccessSession | ApiError>> {
  if (!accessRequired()) return noStore<AccessSession>({ required: false, signedIn: true });
  if (!accessConfigured()) return failure(503, "UPSTREAM_UNAVAILABLE", "El acceso no está configurado en esta instalación.");
  return noStore<AccessSession>({ required: true, signedIn: hasSession(request) });
}

/** Opens a 90-day session with the shared password (`{"password": "…"}`); the password is never stored. */
export async function POST(request: Request): Promise<NextResponse<ApiError> | Response> {
  if (!accessConfigured()) return failure(503, "UPSTREAM_UNAVAILABLE", "El acceso no está configurado en esta instalación.");
  if (!sameOrigin(request)) return failure(403, "FORBIDDEN", "Origen no permitido.");
  if (throttled(request)) return failure(429, "TOO_MANY_REQUESTS", "Demasiados intentos. Espera unos minutos.");
  const body = (await request.json().catch(() => null)) as { password?: unknown } | null;
  if (!validPassword(body?.password)) {
    recordFailure(request);
    return failure(401, "UNAUTHORIZED", "Contraseña incorrecta.");
  }
  return empty({ "Set-Cookie": sessionCookie(request) });
}

/** Closes this browser's session. Every session is revoked by rotating the session secret. */
export async function DELETE(request: Request): Promise<NextResponse<ApiError> | Response> {
  if (!sameOrigin(request)) return failure(403, "FORBIDDEN", "Origen no permitido.");
  return empty({ "Set-Cookie": clearedCookie(request) });
}
