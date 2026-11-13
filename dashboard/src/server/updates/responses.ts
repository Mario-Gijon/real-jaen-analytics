import { NextResponse } from "next/server";
import type { ApiError, ApiErrorCode } from "@/contract";
import { ControlConflictError, ControlUnavailableError } from "./control-client";
import { controlSecret, sessionCapability, sameOrigin, updateAccessConfigured } from "../auth/session";
import { dataMode } from "../data-source";

/** Updates exist only with real data, a backend, the access control and the control secret configured. */
export function updatesConfigured(): boolean {
  try {
    if (dataMode() !== "real") return false;
  } catch {
    return false;
  }
  return Boolean(process.env.LOCAL_DATA_API_URL) && updateAccessConfigured() && controlSecret() !== null;
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

/** Require a signed administrative session; repeated at the route even when the proxy already checked it. */
export function refusePermission(request: Request): NextResponse<ApiError> | null {
  const capability = sessionCapability(request);
  if (capability === null) return failure(401, "UNAUTHORIZED", "Inicia sesión.");
  if (!capability) return failure(403, "FORBIDDEN", "No tienes permiso para actualizar.");
  return null;
}

/** Checks session, capability, CSRF origin for mutations, and service configuration. */
export function refuse(request: Request, { changesState }: { changesState: boolean }): NextResponse<ApiError> | null {
  const permission = refusePermission(request);
  if (permission) return permission;
  if (changesState && !sameOrigin(request)) return failure(403, "FORBIDDEN", "Origen no permitido.");
  if (!updatesConfigured()) return unavailable();
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
