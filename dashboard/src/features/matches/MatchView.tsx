"use client";

import Link from "next/link";
import { getSource } from "@/client/api";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { failureReason, useApi } from "@/hooks/use-api";
import { MatchDetailView } from "./MatchDetailView";
import { RealMatchDetailView } from "./RealMatchDetailView";
import css from "./MatchDetailView.module.css";

/**
 * A match page: the real detail, or the demonstration's when the API says it
 * is one. The mode is the API's, never the link's: an old `view=observed`
 * only keeps its return context, so a real link in the demonstration (or an
 * old synthetic one in real mode) finds no match instead of switching data.
 */
export function MatchView({ matchId, tab, playerReturnHref, teamsReturnHref, homeReturnHref, matchesReturnHref = null, originQuery }: {
  matchId: string; tab: unknown; playerReturnHref: string | null; teamsReturnHref: string | null; homeReturnHref: string | null;
  matchesReturnHref?: string | null;
  originQuery: Record<string, unknown>;
}) {
  const source = useApi("source", (signal) => getSource({ signal }));
  if (source.error) return <div className={css.page}>
    <Link href={matchesReturnHref ?? "/matches"} className={css.back}>‹ Volver a partidos</Link>
    <div role="alert" className={css.message}>
      <h1>No se pudo determinar la fuente de los datos</h1>
      <p>{failureReason(source.error)}</p>
      <Button size="sm" onClick={source.retry}>Reintentar</Button>
    </div>
  </div>;
  if (!source.data) return <div className={css.page}>
    <Link href={matchesReturnHref ?? "/matches"} className={css.back}>‹ Volver a partidos</Link>
    <LoadingState variant="page" label="Cargando partido…" />
  </div>;
  return source.data.synthetic
    ? <MatchDetailView matchId={matchId} initialSection={tab === "timeline" || tab === "lineups" || tab === "comments" ? tab : "summary"}
      playerReturnHref={playerReturnHref} teamsReturnHref={teamsReturnHref} homeReturnHref={homeReturnHref} />
    : <RealMatchDetailView matchId={matchId} initialSection={tab === "lineups" || tab === "comments" ? tab : "summary"}
      playerReturnHref={playerReturnHref} teamsReturnHref={teamsReturnHref} homeReturnHref={homeReturnHref}
      matchesReturnHref={matchesReturnHref} originQuery={originQuery} />;
}
