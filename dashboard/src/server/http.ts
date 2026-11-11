import { NextResponse } from "next/server";
import type { ApiError, ApiErrorCode } from "@/contract";
import { DataConfigurationError } from "./data-source";
import { UpstreamUnavailableError } from "./local-api/local-api-source";

/** Shared responses of the `/api/v1` route handlers. */
export function json<T>(body: T): NextResponse<T> {
  return NextResponse.json<T>(body);
}

function error(status: number, code: ApiErrorCode, message: string): NextResponse<ApiError> {
  return NextResponse.json<ApiError>({ error: { code, message } }, { status });
}

export const notFound = (message: string) => error(404, "NOT_FOUND", message);
export const badRequest = (message: string) => error(400, "BAD_REQUEST", message);
export const upstreamUnavailable = (message: string) =>
  error(503, "UPSTREAM_UNAVAILABLE", message);

/** `json(body)` or a 404 when the source found nothing. */
export function jsonOrNotFound<T>(body: T | null, message: string): NextResponse<T | ApiError> {
  return body === null ? notFound(message) : json(body);
}

/** A wrong data configuration (`DATA_MODE`, `LOCAL_DATA_API_URL`): 503 with its reason, logged; no source stands in. */
export function misconfigured(failure: DataConfigurationError) {
  console.error("[config]", failure.message);
  return upstreamUnavailable(`Configuración de datos no válida: ${failure.message}`);
}

/** Any route: a wrong data configuration answers 503 with its reason instead of failing blindly. */
export async function guarded<T>(handler: () => Promise<NextResponse<T>> | NextResponse<T>): Promise<NextResponse<T | ApiError>> {
  try {
    return await handler();
  } catch (failure) {
    if (failure instanceof DataConfigurationError) return misconfigured(failure);
    throw failure;
  }
}

/**
 * `jsonOrNotFound` for a section a real source may serve: when that source
 * fails it answers 503 (logged on the server), never other data in its place;
 * a wrong data configuration too, with its reason.
 */
export async function jsonFromSource<T>(
  load: () => Promise<T | null>,
  notFoundMessage: string,
  context: string,
): Promise<NextResponse<T | ApiError>> {
  try {
    return jsonOrNotFound(await load(), notFoundMessage);
  } catch (failure) {
    if (failure instanceof DataConfigurationError) return misconfigured(failure);
    if (!(failure instanceof UpstreamUnavailableError)) throw failure;
    console.error(`[api] ${context}:`, failure.message);
    return upstreamUnavailable("La fuente de datos real no está disponible.");
  }
}
