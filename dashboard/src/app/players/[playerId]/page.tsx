import type { Metadata } from "next";
import { PlayerView } from "@/features/players/PlayerView";
import { playerInitialView, playerReturnHref, playerSeasonOrigin, profileInitialMatch, profileOrigin } from "@/features/players/navigation";

export const metadata: Metadata = { title: "Ficha de jugador" };

export default async function PlayerPage({ params, searchParams }: {
  params: Promise<{ playerId: string }>;
  searchParams: Promise<{ view?: string; season?: string | string[]; from?: string; origin?: string; originTab?: string; tab?: string | string[]; match?: string | string[] }>;
}) {
  const [{ playerId }, query] = await Promise.all([params, searchParams]);
  const seasonId = typeof query.season === "string" && query.season ? query.season : null;
  const initial = playerInitialView(query);
  // The data mode is the API's; `view=profile` in older links decides nothing.
  return <PlayerView playerId={playerId} returnHref={playerReturnHref(seasonId, query)} seasonId={seasonId}
    origin={profileOrigin(query)} seasonOrigin={playerSeasonOrigin(query)} profileMatchId={profileInitialMatch(query)}
    initialTab={initial.tab} seasonMatchId={initial.matchId} />;
}
