import { sourceMatchId } from "@/server/local-api/identifiers";
import { InvalidLocalApiResponseError } from "@/server/local-api/validation";
import { UpstreamUnavailableError } from "@/server/local-api/local-api-source";
import { hasSession, sameOrigin } from "@/server/auth/session";
import { DataConfigurationError, getDataSource } from "@/server/data-source";
import type { MatchComments } from "@/contract";
import { failure, noStore } from "@/server/updates/responses";
import {
  commentsToVideoSportsEvents,
  SportsCommentsInvalidError,
  SportsCommentsUnavailableError,
  type VideoSportsEventsV1,
} from "@/server/video-sports-events";
import {
  getCurrentMatchVideoJob,
  startMatchVideoJob,
  VideoServiceRequestError,
  VideoServiceUnavailableError,
} from "@/server/video-service";

type Context = { params: Promise<{ matchId: string }> };

function operationError(error: unknown) {
  if (error instanceof VideoServiceUnavailableError) {
    return failure(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo.");
  }
  if (error instanceof VideoServiceRequestError) {
    const code = error.status === 404 ? "NOT_FOUND"
      : error.status === 409 ? "CONFLICT"
        : error.status === 429 ? "TOO_MANY_REQUESTS"
          : error.status === 422 ? "BAD_REQUEST" : "UPSTREAM_UNAVAILABLE";
    return failure(error.status, code, error.message);
  }
  throw error;
}

function canonicalYouTubeUrl(value: string): string | null {
  try {
    const url = new URL(value.trim());
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return null;
    let videoId: string | null = null;
    if (host === "youtu.be" || host === "www.youtu.be") {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length === 1) videoId = parts[0];
    } else if (["youtube.com", "www.youtube.com", "m.youtube.com"].includes(host) && url.pathname === "/watch") {
      const values = url.searchParams.getAll("v");
      if (values.length === 1) videoId = values[0];
    } else if (["youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"].includes(host)) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length === 2 && ["embed", "live", "shorts"].includes(parts[0])) videoId = parts[1];
    }
    return videoId && /^[A-Za-z0-9_-]{11}$/.test(videoId)
      ? "https://www.youtube.com/watch?v=" + videoId : null;
  } catch {
    return null;
  }
}

export async function GET(_request: Request, { params }: Context) {
  const { matchId } = await params;
  const sourceId = sourceMatchId(matchId);
  if (sourceId === null) return noStore(null);
  try {
    return noStore(await getCurrentMatchVideoJob(String(sourceId)));
  } catch (error) {
    return operationError(error);
  }
}

/** The browser supplies only the YouTube URL; the match identity comes from this route. */
export async function POST(request: Request, { params }: Context) {
  if (!sameOrigin(request)) return failure(403, "FORBIDDEN", "Origen no permitido.");
  if (!hasSession(request)) return failure(401, "UNAUTHORIZED", "Inicia sesión.");
  const { matchId } = await params;
  const sourceId = sourceMatchId(matchId);
  if (sourceId === null) return failure(404, "NOT_FOUND", "No existe este partido.");

  let body: unknown;
  try { body = await request.json(); } catch { return failure(400, "BAD_REQUEST", "La petición no contiene un JSON válido."); }
  if (typeof body !== "object" || body === null || Array.isArray(body)
      || Object.keys(body).length !== 1 || typeof (body as { source_url?: unknown }).source_url !== "string"
      || !(body as { source_url: string }).source_url.trim()
      || (body as { source_url: string }).source_url.length > 2048) {
    return failure(400, "BAD_REQUEST", "Indica únicamente una URL de YouTube válida.");
  }
  const normalizedUrl = canonicalYouTubeUrl((body as { source_url: string }).source_url);
  if (!normalizedUrl) return failure(400, "BAD_REQUEST", "Pega un enlace HTTPS válido de YouTube.");

  let comments: MatchComments | null;
  try {
    comments = await getDataSource().matchComments(matchId);
  } catch (error) {
    if (error instanceof InvalidLocalApiResponseError) {
      return failure(502, "UPSTREAM_UNAVAILABLE", "La API deportiva devolvió una respuesta no válida.");
    }
    if (error instanceof UpstreamUnavailableError || error instanceof DataConfigurationError) {
      return failure(503, "UPSTREAM_UNAVAILABLE", "La API deportiva no está disponible temporalmente.");
    }
    throw error;
  }
  if (comments === null) return failure(404, "NOT_FOUND", "El partido no existe en la API deportiva.");
  if (comments.matchId !== matchId) {
    return failure(502, "UPSTREAM_UNAVAILABLE", "La API deportiva no confirmó la identidad exacta del partido.");
  }

  let sportsEvents: VideoSportsEventsV1 | null;
  try {
    const sourceRef = String(sourceId);
    sportsEvents = commentsToVideoSportsEvents(sourceRef, comments);
  } catch (error) {
    if (error instanceof SportsCommentsUnavailableError) {
      return failure(503, "UPSTREAM_UNAVAILABLE", "Los comentarios narrativos no están disponibles temporalmente.");
    }
    if (error instanceof SportsCommentsInvalidError) {
      return failure(502, "UPSTREAM_UNAVAILABLE", "Los comentarios narrativos recibidos no son válidos.");
    }
    throw error;
  }
  try {
    return noStore(await startMatchVideoJob(String(sourceId), normalizedUrl, sportsEvents), 202);
  } catch (error) {
    return operationError(error);
  }
}
