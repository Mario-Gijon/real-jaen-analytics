import { getDataSource } from "@/server/data-source";
import { guarded, jsonOrNotFound } from "@/server/http";

type Context = { params: Promise<{ seasonId: string }> };

/** Teams of a season with their table record, by rank. */
export async function GET(_request: Request, { params }: Context) {
  const { seasonId } = await params;
  return guarded(async () => jsonOrNotFound(await getDataSource().seasonTeams(seasonId), "Temporada no encontrada."));
}
