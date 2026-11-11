"use client";

import { ApiRequestError, getPlayer } from "@/client/api";
import type { PlayerSeason, PlayerSeasonStats } from "@/contract";
import { Button } from "@/ui/Button";
import { PlayerAvatar } from "@/ui/PlayerAvatar";
import { LoadingState } from "@/ui/LoadingState";
import { Tabs, tabId, tabPanelId } from "@/ui/Tabs";
import { useState } from "react";
import Link from "next/link";
import { playerMatchHref, playerReturnHref } from "@/lib/navigation";
import { teamsHref } from "@/lib/navigation";
import type { PlayerOrigin, PlayerTab } from "./navigation";
import { FIELD_GROUPS, KEEPER_GROUPS } from "./player-metrics";
import { PlayerMatches } from "./PlayerMatches";
import { birthLabel, footLabel, heightLabel, positionLabel } from "@/lib/format";
import { useApi } from "@/hooks/use-api";
import { PlayerContributions } from "./PlayerContributions";
import { PlayerSeasonValue } from "./PlayerSeasonValue";
import css from "./PlayerDetailView.module.css";

type Metric = { key: keyof PlayerSeasonStats; label: string; percent?: boolean };
const PARTICIPATION: readonly Metric[] = [
  { key: "appearances", label: "PJ" }, { key: "starts", label: "Titularidades" }, { key: "minutes", label: "Minutos" },
];


export function PlayerDetailView({ playerId, seasonId, returnHref, origin = null, initialTab = "season", initialMatchId = null }: {
  playerId: string; seasonId: string | null; returnHref?: string; origin?: PlayerOrigin | null;
  initialTab?: PlayerTab; initialMatchId?: string | null;
}) {
  const [tab, setTab] = useState<PlayerTab>(initialTab);
  const profile = useApi(seasonId ? `player|${playerId}|${seasonId}` : null,
    (signal) => getPlayer(playerId, seasonId!, { signal }));
  if (!seasonId) return <div role="alert" className={css.message}>
    <h1>Selecciona una temporada</h1>
    <p>La ficha del jugador requiere el parámetro season en la dirección.</p>
  </div>;
  if (profile.error) {
    const notFound = profile.error instanceof ApiRequestError && profile.error.status === 404;
    return <div role="alert" className={css.message}>
      <h1>{notFound ? "Jugador no encontrado en esta temporada" : "No se pudo cargar la ficha del jugador"}</h1>
      {!notFound ? <Button size="sm" onClick={profile.retry}>Reintentar</Button> : null}
    </div>;
  }
  if (!profile.data) return <div className={css.page} aria-busy="true">
    <Link className={css.back} href={returnHref ?? playerReturnHref(seasonId, {})}>← Volver</Link>
    <LoadingState variant="page" label="Cargando jugador…" />
  </div>;
  const data = profile.data;
  const noParticipation = data.stats.appearances.value === 0;
  const matchHref = (matchId: string) => playerMatchHref(matchId, { playerId: data.player.id, seasonId: data.season.id, origin });
  return <div className={css.page}>
    <Link className={css.back} href={returnHref ?? playerReturnHref(seasonId, {})}>← Volver</Link>
    <Identity data={data} />
    <Tabs items={[{ id: "season", label: "Temporada" }, { id: "matches", label: "Partidos" }]}
      selected={tab} onSelect={setTab} label="Secciones del jugador" prefix="player" />
    <section role="tabpanel" id={tabPanelId("player")} aria-labelledby={tabId("player", tab)} className={`${css.season} ${tab === "matches" ? css.matches : ""}`}>
      {tab === "matches" ? <PlayerMatches key={`${data.player.id}|${data.season.id}`} data={data} initialMatchId={initialMatchId}
        matchHref={matchHref} /> : <>
      <section aria-labelledby="player-participation" className={css.block}>
        <h2 id="player-participation">Participación</h2>
        <Metrics metrics={PARTICIPATION} stats={data.stats} participation />
      </section>
      <section aria-labelledby="player-performance" className={css.block}>
        <h2 id="player-performance">Rendimiento</h2>
        {noParticipation ? <p className={css.message}>Sin participación esta temporada.</p> : <div className={css.performance}>
          {(data.player.position === "goalkeeper" ? KEEPER_GROUPS : FIELD_GROUPS).map(group =>
            <section key={group.title} className={css.group} aria-label={group.title}>
              <h3>{group.title}</h3><Metrics metrics={group.metrics} stats={data.stats} />
            </section>,
          )}
        </div>}
      </section>
      {!noParticipation ? <section aria-labelledby="player-contributions" className={css.block}>
        <h2 id="player-contributions">Aportación</h2>
        <PlayerContributions data={data} matchHref={matchHref} />
      </section> : null}
      </>}
    </section>
  </div>;
}

function Identity({ data }: { data: PlayerSeason }) {
  const { player, team, shirtNumber, season } = data;
  return <header className={css.header}>
    <div className={css.identity}>
      <PlayerAvatar name={player.name} size={48} />
      <div className={css.identityText}>
        <div className={css.name}>
          {shirtNumber !== null ? <span className={css.shirt} aria-label={`Dorsal ${shirtNumber}`}>{shirtNumber}</span> : null}
          <h1>{player.name}</h1>
        </div>
        <p className={css.context}>{positionLabel(player.position)} · <Link className={css.teamLink}
          href={teamsHref({ seasonId: season.id, teamId: team.id })}>{team.name}</Link> · {season.name}</p>
      </div>
    </div>
    <dl className={css.personal}>
      <div><dt>Nacimiento</dt><dd>{birthLabel(player.birthDate)}</dd></div>
      <div><dt>Estatura</dt><dd>{heightLabel(player.heightCm)}</dd></div>
      <div><dt>Pierna dominante</dt><dd>{footLabel(player.preferredFoot)}</dd></div>
    </dl>
  </header>;
}

function Metrics({ metrics, stats, participation = false }: {
  metrics: readonly Metric[]; stats: PlayerSeasonStats; participation?: boolean;
}) {
  const coverageGroups = new Map<string, { matches: number; of: number; labels: string[] }>();
  for (const { key, label } of metrics) {
    const stat = stats[key];
    if (stat.coverage !== "partial") continue;
    const coverageKey = `${stat.matches}/${stat.of}`;
    const group = coverageGroups.get(coverageKey);
    if (group) group.labels.push(label);
    else coverageGroups.set(coverageKey, { matches: stat.matches, of: stat.of, labels: [label] });
  }
  return <><dl className={participation ? css.participation : css.metrics}>
    {metrics.map(({ key, label, percent }) => <div key={key}>
      <dt>{label}</dt><dd><PlayerSeasonValue stat={stats[key]} percent={percent} /></dd>
    </div>)}
  </dl>
    {[...coverageGroups.entries()].map(([key, group]) => <p key={key} className={css.coverageNote}>
      * {coverageGroups.size > 1 ? `${group.labels.join(" / ")}: ` : ""}Datos disponibles en {group.matches} de {group.of} partidos
    </p>)}
  </>;
}
