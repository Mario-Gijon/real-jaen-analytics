"use client";

import Link from "next/link";
import { getSource } from "@/client/api";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { failureReason, useApi } from "@/hooks/use-api";
import type { PlayerOrigin, PlayerTab, ProfileOrigin } from "./navigation";
import { PlayerDetailView } from "./PlayerDetailView";
import { PlayerProfileView } from "./PlayerProfileView";
import css from "./PlayerDetailView.module.css";

/**
 * A player page: the real profile, or the demonstration's season of the
 * player when the API says it is one. The mode is the API's, never the
 * link's: an old `view=profile` changes nothing, so an id of the other mode
 * is simply not found.
 */
export function PlayerView({ playerId, returnHref, seasonId, origin, seasonOrigin = null, profileMatchId, initialTab, seasonMatchId }: {
  playerId: string; returnHref: string; seasonId: string | null; origin: ProfileOrigin | null;
  seasonOrigin?: PlayerOrigin | null;
  /** The real match selected in the profile. */
  profileMatchId: string | null;
  /** The demonstration's tab and match. */
  initialTab: PlayerTab; seasonMatchId: string | null;
}) {
  const source = useApi("source", (signal) => getSource({ signal }));
  if (source.error) return <div className={css.page}>
    <Link className={css.back} href={returnHref}>← Volver</Link>
    <div role="alert" className={css.message}>
      <h1>No se pudo determinar la fuente de los datos</h1>
      <p>{failureReason(source.error)}</p>
      <Button size="sm" onClick={source.retry}>Reintentar</Button>
    </div>
  </div>;
  if (!source.data) return <div className={css.page} aria-busy="true">
    <Link className={css.back} href={returnHref}>← Volver</Link>
    <LoadingState variant="page" label="Cargando jugador…" />
  </div>;
  return source.data.synthetic
    ? <PlayerDetailView playerId={playerId} seasonId={seasonId} returnHref={returnHref} origin={seasonOrigin}
      initialTab={initialTab} initialMatchId={seasonMatchId} />
    : <PlayerProfileView playerId={playerId} returnHref={returnHref} context={{ seasonId, origin }} initialMatchId={profileMatchId} />;
}
