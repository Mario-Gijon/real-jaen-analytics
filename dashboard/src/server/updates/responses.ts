import { NextResponse } from "next/server";
import type { ApiError, ApiErrorCode } from "@/contract";
import { ControlConflictError, ControlUnavailableError } from "./control-client";
import { accessConfigured, controlSecret, hasSession, sameOrigin } from "../auth/session";
import { dataMode } from "../data-source";

/** Updates exist only with real data, a backend, the access control and the control secret configured. */
export function updatesConfigured(): boolean {
  try {
    if (dataMode() !== "real") return false;
  } catch {
    return false;
  }
  return Boolean(process.env.LOCAL_DATA_API_URL) && accessConfigured() && controlSecret() !== null;
}

/** Responses of the update routes: never cached, errors in the contract's shape. */
export function noStore<T>(body: T, status = 200, headers: Record<string, string> = {}): NextResponse<T> {
  return NextResponse.json<T>(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

export function failure(status: number, code: ApiErrorCode, message: string): NextResponse<ApiError> {
  return noStore<ApiError>({ error: { code, message } }, status);
}

export const empty = (headers: Record<string, string> = {}) =>
  new NextResponse(null, { status: 204, headers: { "Cache-Control": "no-store", ...headers } });

export const unavailable = () =>
  failure(409, "UPDATE_UNAVAILABLE", "La actualización no está disponible en esta instalación.");

/**
 * The checks of every update operation, in order: configured, same origin (state changes only), session. The
 * access gate (src/proxy.ts) already requires the session; this is the second barrier. `null` when it may go on.
 */
export function refuse(request: Request, { changesState }: { changesState: boolean }): NextResponse<ApiError> | null {
  if (!updatesConfigured()) return unavailable();
  if (changesState && !sameOrigin(request)) return failure(403, "FORBIDDEN", "Origen no permitido.");
  if (!hasSession(request)) return failure(401, "UNAUTHORIZED", "Inicia sesión.");
  return null;
}

/** A backend failure as a contract error; anything unexpected is not hidden. */
export function fromControl(error: unknown, context: string): NextResponse<ApiError> {
  if (error instanceof ControlConflictError) {
    return error.reason === "not_cancellable"
      ? failure(409, "CONFLICT", "La actualización ya no se puede cancelar.")
      : failure(409, "CONFLICT", "La actualización no está disponible ahora mismo.");
  }
  if (error instanceof ControlUnavailableError) {
    console.error(`[updates] ${context}:`, error.message);
    return failure(503, "UPSTREAM_UNAVAILABLE", "El servicio de actualización no responde.");
  }
  throw error;
}
