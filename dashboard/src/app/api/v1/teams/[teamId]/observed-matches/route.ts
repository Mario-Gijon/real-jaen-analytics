import { getDataSource } from "@/server/data-source";
import { jsonFromSource } from "@/server/http";

type Context = { params: Promise<{ teamId: string }> };

/**
 * A team's matches as a real source lists them. A team without
 * correspondence answers 404; a real source that fails answers 503, never
 * synthetic matches in their place.
 */
export async function GET(_request: Request, { params }: Context) {
  const { teamId } = await params;
  return jsonFromSource(
    () => getDataSource().teamObservedMatches(teamId),
    "Este equipo no tiene correspondencia con la fuente real de partidos.",
    `partidos reales de ${teamId}`,
  );
}
