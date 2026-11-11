import type { Metadata } from "next";
import { MatchView } from "@/features/matches/MatchView";
import { homeReturnHref, observedMatchListReturnHref, observedMatchReturnHref, observedMatchTeamsReturnHref } from "@/features/players/navigation";

export const metadata: Metadata = { title: "Detalle de partido" };

export default async function MatchPage({ params, searchParams }: { params: Promise<{ matchId: string }>; searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const [{ matchId }, query] = await Promise.all([params, searchParams]);
  // The data mode is the API's; `view=observed` in older links decides nothing.
  return <MatchView matchId={matchId} tab={query.tab} playerReturnHref={observedMatchReturnHref(matchId, query)}
    teamsReturnHref={observedMatchTeamsReturnHref(query)} homeReturnHref={homeReturnHref(query)}
    matchesReturnHref={observedMatchListReturnHref(query)} originQuery={query} />;
}
