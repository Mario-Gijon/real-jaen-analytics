import { sourceMatchId } from "@/server/local-api/identifiers";
import { failure, noStore } from "@/server/updates/responses";
import { locateMatchVideoEvent, MatchVideoNotFoundError, VideoServiceUnavailableError } from "@/server/video-service";

type Context = { params: Promise<{ matchId: string }> };

/** Resolve a SofaScore football minute through Video Service; only `resolved` carries a position. */
export async function GET(request: Request, { params }: Context) {
  const { matchId } = await params;
  const sourceId = sourceMatchId(matchId);
  if (sourceId === null) return failure(404, "NOT_FOUND", "No existe una referencia de vídeo para este partido.");

  const query = new URL(request.url).searchParams;
  const period = query.get("period");
  const minute = query.get("minute");
  const eventKind = query.get("event_kind") ?? "other";
  const eventSecondText = query.get("event_second");
  const eventSecond = eventSecondText === null ? undefined : Number(eventSecondText);
  const isStart = eventKind === "match_start" || eventKind === "period_start";
  if ((period !== "first" && period !== "second")
      || !["other", "match_start", "period_start"].includes(eventKind)
      || (minute !== null && !/^\d{1,3}(?:\+\d{1,2})?$/.test(minute))
      || (!minute && !isStart)
      || (eventSecondText !== null && (!Number.isInteger(eventSecond) || eventSecond! < 0 || eventSecond! > 59))) {
    return failure(400, "BAD_REQUEST", "Indica un periodo y un minuto futbolístico válidos.");
  }

  try {
    return noStore(await locateMatchVideoEvent(String(sourceId), period, minute, eventKind as "other" | "match_start" | "period_start", eventSecond));
  } catch (error) {
    if (error instanceof MatchVideoNotFoundError) {
      return failure(404, "NOT_FOUND", "No existe una referencia de vídeo disponible para este partido.");
    }
    if (error instanceof VideoServiceUnavailableError) {
      return failure(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo.");
    }
    throw error;
  }
}
