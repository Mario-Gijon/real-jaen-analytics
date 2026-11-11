import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ matchId: string }> };

/**
 * A real match with its team stats. A synthetic id, or one the source does
 * not know, answers 404; a real source that fails answers 503, never
 * synthetic data in its place.
 */
export async function GET(_request: Request, { params }: Context) {
  const { matchId } = await params;
  return jsonFromSource(
    () => getDataSource().matchObservedDetail(matchId),
    "Partido no encontrado en la fuente real de partidos.",
    `detalle real del partido ${matchId}`,
  );
}
