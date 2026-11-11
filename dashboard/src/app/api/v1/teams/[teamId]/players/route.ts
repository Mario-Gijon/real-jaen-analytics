import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ teamId: string }> };

/**
 * Players seen with a team, in no season. A real source that fails answers
 * 503: never synthetic players in its place.
 */
export async function GET(_request: Request, { params }: Context) {
  const { teamId } = await params;
  return jsonFromSource(
    () => getDataSource().observedPlayers(teamId),
    "No hay jugadores observados de este equipo en la fuente de datos.",
    `jugadores observados de ${teamId}`,
  );
}
