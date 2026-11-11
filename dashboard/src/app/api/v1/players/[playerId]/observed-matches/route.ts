import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ playerId: string }> };

/**
 * Matches in which a real source lists the player, newest first. Unknown ids
 * answer 404; a real source that fails answers 503, never synthetic matches.
 */
export async function GET(_request: Request, { params }: Context) {
  const { playerId } = await params;
  return jsonFromSource(
    () => getDataSource().playerObservedMatches(playerId),
    "Jugador no encontrado en la fuente de datos.",
    `partidos observados de ${playerId}`,
  );
}
