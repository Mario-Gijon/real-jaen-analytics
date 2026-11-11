import { getDataSource } from "@/server/data-source";
import { badRequest, guarded, jsonOrNotFound } from "@/server/http";

type Context = { params: Promise<{ playerId: string }> };

/** A player's season (`?season=` is required): profile, totals and matches. */
export async function GET(request: Request, { params }: Context) {
  const { playerId } = await params;
  const seasonId = new URL(request.url).searchParams.get("season");
  if (!seasonId) return badRequest("Falta el parámetro `season`.");
  return guarded(async () => jsonOrNotFound(
    await getDataSource().player(playerId, seasonId),
    "Jugador no encontrado en esa temporada.",
  ));
}
