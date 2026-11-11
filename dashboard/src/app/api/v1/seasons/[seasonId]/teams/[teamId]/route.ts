import { getDataSource } from "@/server/data-source";
import { guarded, jsonOrNotFound } from "@/server/http";

type Context = { params: Promise<{ seasonId: string; teamId: string }> };

/** A team's season: record, calendar, accumulated stats and squad. */
export async function GET(_request: Request, { params }: Context) {
  const { seasonId, teamId } = await params;
  return guarded(async () => jsonOrNotFound(
    await getDataSource().teamSeason(seasonId, teamId),
    "Equipo o temporada no encontrados.",
  ));
}
