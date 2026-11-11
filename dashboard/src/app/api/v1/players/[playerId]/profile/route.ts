import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ playerId: string }> };

/**
 * A real player's personal data, in no season. Unknown ids answer 404; a real
 * source that fails answers 503, never synthetic data in its place.
 */
export async function GET(_request: Request, { params }: Context) {
  const { playerId } = await params;
  return jsonFromSource(
    () => getDataSource().playerProfile(playerId),
    "Jugador no encontrado en la fuente de datos.",
    `ficha de ${playerId}`,
  );
}
