import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ teamId: string }> };

/**
 * A team's observed players with their figures in each competition of the
 * real source's scope. Only a real source has them: otherwise, or for a team
 * without correspondence, 404; a real source that fails answers 503, never
 * synthetic figures in their place.
 */
export async function GET(_request: Request, { params }: Context) {
  const { teamId } = await params;
  return jsonFromSource(
    () => getDataSource().observedSquad(teamId),
    "No hay acumulados reales de jugadores de este equipo.",
    `acumulados reales de jugadores de ${teamId}`,
  );
}
