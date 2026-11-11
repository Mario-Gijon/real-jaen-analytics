import { getDataSource } from "@/server/data-source";
import { guarded, jsonOrNotFound } from "@/server/http";

type Context = { params: Promise<{ matchId: string }> };

/** Full match sheet: line-ups, events and team and player stats. */
export async function GET(_request: Request, { params }: Context) {
  const { matchId } = await params;
  return guarded(async () => jsonOrNotFound(await getDataSource().match(matchId), "Partido no encontrado."));
}
