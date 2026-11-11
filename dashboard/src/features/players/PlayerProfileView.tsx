"use client";

import Link from "next/link";
import { ApiRequestError, getPlayerObservedMatches, getPlayerProfile } from "@/client/api";
import type { PlayerProfile } from "@/contract";
import { Button } from "@/ui/Button";
import { PlayerAvatar } from "@/ui/PlayerAvatar";
import { LoadingState } from "@/ui/LoadingState";
import { birthLabel, footLabel, heightLabel, positionLabel } from "@/lib/format";
import { countryLabel } from "./country-label";
import { useApi } from "@/hooks/use-api";
import { ObservedMatches } from "./ObservedMatches";
import { observedMatchHref } from "@/lib/navigation";
import type { ProfileOrigin } from "./navigation";
import css from "./PlayerDetailView.module.css";

/**
 * Profile of a player from a real source: personal data and the matches the
 * source lists them in, with where they come from and what they cover. It
 * loads nothing synthetic (no season, team or calendar of the app); unknown
 * values read «—». The matches load on their own, so their failure keeps the
 * personal data visible. The selected match is kept in the URL (`match`),
 * so a reload or a return from the match detail shows it again. Each match
 * links its real detail only when the real match source is active, carrying
 * this profile's context so the detail can return here and, from here, to
 * where the profile was opened.
 */
export function PlayerProfileView({ playerId, returnHref, context = { seasonId: null, origin: null }, initialMatchId = null }: {
  playerId: string; returnHref: string; context?: { seasonId: string | null; origin: ProfileOrigin | null }; initialMatchId?: string | null;
}) {
  const profile = useApi(`profile|${playerId}`, (signal) => getPlayerProfile(playerId, { signal }));
  const matches = useApi(`observed-matches|${playerId}`, (signal) => getPlayerObservedMatches(playerId, { signal }));
  const matchHref = (matchId: string) => observedMatchHref(matchId, { playerId, ...context });
  function rememberMatch(matchId: string) {
    // Same URL with the new selection: no navigation, no history entry.
    const query = new URLSearchParams(window.location.search);
    query.set("match", matchId);
    window.history.replaceState(window.history.state, "", `?${query}`);
  }
  const back = <Link className={css.back} href={returnHref}>← Volver</Link>;
  if (profile.error) {
    const notFound = profile.error instanceof ApiRequestError && profile.error.status === 404;
    return <div className={css.page}>
      {back}
      <div role="alert" className={css.message}>
        <h1>{notFound ? "Jugador no encontrado" : "No se pudo cargar la ficha del jugador"}</h1>
        <p>{notFound
          ? "La fuente de datos no tiene este jugador."
          : profile.error instanceof ApiRequestError ? profile.error.message : "Error de conexión."}</p>
        {!notFound ? <Button size="sm" onClick={profile.retry}>Reintentar</Button> : null}
      </div>
    </div>;
  }
  if (!profile.data) return <div className={css.page} aria-busy="true">
    {back}
    <LoadingState variant="page" label="Cargando jugador…" />
  </div>;
  return <div className={css.page}>
    {back}
    <Identity data={profile.data} />
    <div className={`${css.season} ${css.matches}`}>
      <ObservedMatches load={matches} usualPosition={profile.data.player.position} initialMatchId={initialMatchId}
        matchHref={matchHref} onSelect={rememberMatch} />
    </div>
  </div>;
}

function Identity({ data: { player } }: { data: PlayerProfile }) {
  return <header className={css.header}>
    <div className={css.identity}>
      <PlayerAvatar name={player.name} size={48} />
      <div className={css.identityText}>
        <div className={css.name}><h1>{player.name}</h1></div>
        <p className={css.context}>{positionLabel(player.position)}</p>
      </div>
    </div>
    <dl className={css.personal}>
      <div><dt>Nacimiento</dt><dd>{birthLabel(player.birthDate)}</dd></div>
      <div><dt>Estatura</dt><dd>{heightLabel(player.heightCm)}</dd></div>
      <div><dt>Pierna dominante</dt><dd>{footLabel(player.preferredFoot)}</dd></div>
      <div><dt>País</dt><dd>{countryLabel(player.country)}</dd></div>
    </dl>
  </header>;
}
