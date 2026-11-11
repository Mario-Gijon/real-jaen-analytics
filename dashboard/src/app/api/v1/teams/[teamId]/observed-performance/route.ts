import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ teamId: string }> };

/**
 * A team's results in each competition of the real source's scope. A team
 * without correspondence answers 404; a real source that fails answers 503,
 * never synthetic figures in their place.
 */
export async function GET(_request: Request, { params }: Context) {
  const { teamId } = await params;
  return jsonFromSource(
    () => getDataSource().teamObservedPerformance(teamId),
    "Este equipo no está disponible en la fuente real de equipos: sin correspondencia explícita o sin exportación propia.",
    `rendimiento real de ${teamId}`,
  );
}
