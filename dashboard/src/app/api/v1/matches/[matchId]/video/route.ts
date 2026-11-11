import type { MatchVideoReference } from "@/contract";
import { hasSession, sameOrigin } from "@/server/auth/session";
import { sourceMatchId } from "@/server/local-api/identifiers";
import { failure, noStore } from "@/server/updates/responses";
import {
  deleteMatchVideoReference,
  getMatchVideoReference,
  VideoServiceRequestError,
  VideoServiceUnavailableError,
} from "@/server/video-service";

type Context = { params: Promise<{ matchId: string }> };

/** Optional match-to-video lookup; the browser only talks to this same-origin BFF. */
export async function GET(_request: Request, { params }: Context) {
  const { matchId } = await params;
  const sourceId = sourceMatchId(matchId);
  if (sourceId === null) return noStore<MatchVideoReference>({ available: false });
  try {
    return noStore(await getMatchVideoReference(String(sourceId)));
  } catch (error) {
    if (!(error instanceof VideoServiceUnavailableError)) throw error;
    return failure(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo.");
  }
}

export async function DELETE(request: Request, { params }: Context) {
  if (!sameOrigin(request)) return failure(403, "FORBIDDEN", "Origen no permitido.");
  if (!hasSession(request)) return failure(401, "UNAUTHORIZED", "Inicia sesión.");
  const { matchId } = await params;
  const sourceId = sourceMatchId(matchId);
  if (sourceId === null) return failure(404, "NOT_FOUND", "No existe este partido.");
  try {
    await deleteMatchVideoReference(String(sourceId));
    return noStore<MatchVideoReference>({ available: false });
  } catch (error) {
    if (error instanceof VideoServiceUnavailableError) {
      return failure(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo.");
    }
    if (error instanceof VideoServiceRequestError) {
      const code = error.status === 404 ? "NOT_FOUND" : error.status === 409 ? "CONFLICT" : "UPSTREAM_UNAVAILABLE";
      return failure(error.status, code, error.message);
    }
    throw error;
  }
}
