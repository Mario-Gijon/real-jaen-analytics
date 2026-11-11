import type { MatchFilter } from "@/contract";
import { getDataSource } from "@/server/data-source";
import { badRequest, guarded, jsonOrNotFound } from "@/server/http";

type Context = { params: Promise<{ seasonId: string }> };

/** Matches of a season, optionally of one round (`?round=`) and/or one team (`?team=`). */
export async function GET(request: Request, { params }: Context) {
  const { seasonId } = await params;
  const query = new URL(request.url).searchParams;
  const filter: MatchFilter = {};

  const round = query.get("round");
  if (round !== null) {
    if (!/^[1-9]\d*$/.test(round)) return badRequest("`round` debe ser un número de jornada.");
    filter.round = Number(round);
  }

  return guarded(async () => {
    const source = getDataSource();
    const teamId = query.get("team");
    if (teamId !== null) {
      const teams = await source.seasonTeams(seasonId);
      if (teams && !teams.some((row) => row.team.id === teamId)) {
        return badRequest("`team` no es un equipo de esta temporada.");
      }
      filter.teamId = teamId;
    }
    return jsonOrNotFound(await source.seasonMatches(seasonId, filter), "Temporada no encontrada.");
  });
}
