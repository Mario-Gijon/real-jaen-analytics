import { sourceMatchId } from "@/server/local-api/identifiers";
import { failure, noStore } from "@/server/updates/responses";
import { getMatchVideoJob, VideoServiceRequestError, VideoServiceUnavailableError } from "@/server/video-service";

type Context = { params: Promise<{ matchId: string; jobId: string }> };

export async function GET(_request: Request, { params }: Context) {
  const { matchId, jobId } = await params;
  const sourceId = sourceMatchId(matchId);
  if (sourceId === null) return failure(404, "NOT_FOUND", "No existe este partido.");
  try {
    return noStore(await getMatchVideoJob(String(sourceId), jobId));
  } catch (error) {
    if (error instanceof VideoServiceUnavailableError) {
      return failure(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo.");
    }
    if (error instanceof VideoServiceRequestError) {
      return failure(error.status === 404 ? 404 : 503, error.status === 404 ? "NOT_FOUND" : "UPSTREAM_UNAVAILABLE", error.message);
    }
    throw error;
  }
}
