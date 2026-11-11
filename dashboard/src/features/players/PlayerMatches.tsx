"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp } from "lucide-react";
import { getSeasonMatches } from "@/client/api";
import type { MatchEvent, MatchSummary, PlayerMatch, PlayerSeason, Stat } from "@/contract";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { dateLabel, statText } from "@/lib/format";
import { useApi } from "@/hooks/use-api";
import { matchHref as buildMatchHref, teamsHref } from "@/lib/navigation";
import { PlayerLink } from "./PlayerLink";
import { GoalIcon } from "../matches/GoalIcon";
import { AssistIcon } from "../matches/AssistIcon";
import { FIELD_GROUPS, KEEPER_GROUPS } from "./player-metrics";
import shared from "./PlayerDetailView.module.css";
import { revealMatch } from "./reveal-match";
import css from "./PlayerMatches.module.css";

/** `initialMatchId` comes from navigation (e.g. a match context); it is selected only if it is a finished team match. */
export function PlayerMatches({ data, initialMatchId = null, matchHref = buildMatchHref }: {
  data: PlayerSeason; initialMatchId?: string | null; matchHref?: (matchId: string) => string;
}) {
  const calendar = useApi(`player-calendar|${data.season.id}|${data.team.id}`,
    signal => getSeasonMatches(data.season.id, { teamId: data.team.id }, { signal }));
  const [selectedId, setSelectedId] = useState<string | null>(initialMatchId);
  const historyViewport = useRef<HTMLUListElement>(null);
  const initialMatch = useRef(initialMatchId);
  useEffect(() => {
    // Reveal the requested match once, inside the history viewport only.
    if (calendar.data && initialMatch.current) revealMatch(historyViewport.current, initialMatch.current);
    if (calendar.data) initialMatch.current = null;
  }, [calendar.data]);
  if (calendar.error) return <div role="alert" className={shared.message}>
    <p>No se pudo cargar el calendario del equipo.</p><Button size="sm" onClick={calendar.retry}>Reintentar</Button>
  </div>;
  if (!calendar.data) return <section aria-label="Histórico de partidos" aria-busy="true">
    <LoadingState variant="section" label="Cargando partidos…" />
  </section>;
  const matches = calendar.data.filter(match => match.status === "finished")
    .sort((a, b) => (a.kickoff && b.kickoff ? a.kickoff.localeCompare(b.kickoff) : 0) || a.round - b.round);
  const appearances = new Map(data.matches.map(appearance => [appearance.match.id, appearance]));
  const recent = [...matches].reverse();
  // An explicit selection may be a match without appearance; an unknown one selects nothing.
  const requested = selectedId === null ? null : matches.find(match => match.id === selectedId) ?? null;
  const selected = selectedId === null
    ? recent.map(match => appearances.get(match.id)).find(appearance => appearance !== undefined)
    : requested ? appearances.get(requested.id) : undefined;
  const absent = requested && !selected ? requested : null;
  const selectedMatchId = selected?.match.id ?? absent?.id ?? null;
  return <div className={css.masterDetail}>
    <section className={`${shared.block} ${css.master}`} aria-labelledby="player-match-history">
      <h2 id="player-match-history">Partidos</h2>
      {selectedId !== null && !requested ? <p role="status" className={shared.message}>
        El partido indicado no figura entre los partidos finalizados de {data.team.name} en {data.season.name}.
      </p> : null}
      {!matches.length ? <p className={shared.message}>Todavía no hay partidos finalizados del equipo.</p> : null}
      <ul ref={historyViewport} className={css.history} aria-label="Histórico de partidos">
        {recent.map(match => {
          const appearance = appearances.get(match.id);
          const active = selectedMatchId === match.id;
          const content = <HistoryRow match={match} appearance={appearance} data={data} />;
          return <li key={match.id}><div className={css.historyRow} data-match-id={match.id} data-selected={active || undefined}
            aria-current={!appearance && active ? "true" : undefined}
            data-selectable={appearance ? "true" : undefined}>
            {appearance ? <button type="button" className={css.matchSelect}
              aria-label={"Seleccionar partido J" + match.round + ": " + match.home.name + " contra " + match.away.name}
              aria-pressed={active} aria-expanded={active} aria-controls={active ? "player-match-detail-panel player-inline-detail" : undefined}
              onClick={() => setSelectedId(match.id)} /> : null}
            {content}
          </div>
            {active ? <section id="player-inline-detail" className={css.inlineDetail} aria-label="Detalle inline del partido">
              <h3 className={css.detailHeading}>Detalle del partido</h3>
              {appearance ? <AppearanceDetail appearance={appearance} data={data} matchHref={matchHref} />
                : <AbsentDetail match={match} data={data} matchHref={matchHref} />}
            </section> : null}
          </li>;
        })}
      </ul>
    </section>
    <section id="player-match-detail-panel" className={`${shared.block} ${css.desktopDetail}`} aria-labelledby="player-match-detail">
      <h2 id="player-match-detail">Detalle del partido</h2>
      {selected ? <AppearanceDetail appearance={selected} data={data} matchHref={matchHref} />
        : absent ? <AbsentDetail match={absent} data={data} matchHref={matchHref} />
        : <p className={shared.message}>{selectedId !== null && data.matches.length ? "Selecciona un partido del histórico." : "Sin apariciones esta temporada."}</p>}
    </section>
  </div>;
}


function MatchValue({ stat, percent = false }: { stat: Stat; percent?: boolean }) {
  if (stat.value === null) {
    const reason = stat.reason === "not_collected" ? "Dato no recogido" : "Dato no disponible";
    return <span className={shared.missing} title={reason} aria-label={reason}>—</span>;
  }
  return <>{statText(stat, percent ? 1 : 0)}{percent ? " %" : null}</>;
}
function Score({ match }: { match: MatchSummary }) {
  return <span className={css.score} aria-label={match.score ? `Resultado local–visitante: ${match.score.home}–${match.score.away}` : "Resultado no disponible"}>
    {match.score ? `${match.score.home}–${match.score.away}` : "—"}
  </span>;
}
function HistoryRow({ match, appearance, data }: { match: MatchSummary; appearance?: PlayerMatch; data: PlayerSeason }) {
  const side = match.home.id === data.team.id ? "Local" : "Visitante";
  const goals = appearance?.events.filter(event => event.type === "goal" && event.player.id === data.player.id && !event.ownGoal).length ?? 0;
  const assists = appearance?.events.filter(event => event.type === "goal" && event.assist?.id === data.player.id).length ?? 0;
  return <>
    <span className={css.round}>J{match.round}</span>
    <span className={css.fixture}>
      <span className={css.teams}><Link href={teamsHref({ seasonId: match.seasonId, teamId: match.home.id })}>{match.home.name}</Link>
        {" · "}<Link href={teamsHref({ seasonId: match.seasonId, teamId: match.away.id })}>{match.away.name}</Link></span>
      <span className={css.meta}>{side} · {dateLabel(match.kickoff)}</span>
    </span>
    <Score match={match} />
    <span className={css.playerContext}>
    <span className={css.role}>{appearance ? <>{appearance.started ? "Titular" : "Suplente"} · <MatchValue stat={appearance.stats.minutes} />{appearance.stats.minutes.value !== null ? "′" : null}</> : "No participó"}</span>
    <span className={css.contribution}>
      {goals > 0 ? <span aria-label={`${goals} ${goals === 1 ? "gol" : "goles"}`}><GoalIcon />{goals > 1 ? goals : null}</span> : null}
      {assists > 0 ? <span aria-label={`${assists} ${assists === 1 ? "asistencia" : "asistencias"}`}><AssistIcon />{assists > 1 ? assists : null}</span> : null}
    </span>
    </span>
  </>;
}

function AppearanceDetail({ appearance, data, matchHref }: { appearance: PlayerMatch; data: PlayerSeason; matchHref: (matchId: string) => string }) {
  const { match } = appearance;
  const opponent = appearance.side === "home" ? match.away : match.home;
  const events = appearance.events.filter(event => event.type === "goal"
    ? event.player.id === data.player.id || event.assist?.id === data.player.id
    : event.type === "card" ? event.player.id === data.player.id
      : event.playerIn.id === data.player.id || event.playerOut.id === data.player.id)
    .slice().sort((a, b) => a.minute - b.minute || (a.addedTime ?? 0) - (b.addedTime ?? 0));
  return <>
    <div className={css.detailContext}>
      <p className={css.teams}>J{match.round} · vs <Link href={teamsHref({ seasonId: match.seasonId, teamId: opponent.id })}>{opponent.name}</Link> <Score match={match} /></p>
      <p className={css.meta}>{appearance.side === "home" ? "Local" : "Visitante"} · {dateLabel(match.kickoff)} · {appearance.started ? "Titular" : "Suplente"} · <MatchValue stat={appearance.stats.minutes} />{appearance.stats.minutes.value !== null ? "′" : null}</p>
    </div>
    <div className={shared.performance}>
      {(data.player.position === "goalkeeper" ? KEEPER_GROUPS : FIELD_GROUPS).map(group =>
        <section className={shared.group} key={group.title} aria-label={group.title}>
          <h3>{group.title}</h3>
          <dl className={shared.metrics}>{group.metrics.map(({ key, label, percent }) =>
            <div key={key}><dt>{label}</dt><dd><MatchValue stat={appearance.stats[key]} percent={percent} /></dd></div>)}</dl>
        </section>)}
    </div>
    <section className={css.actionsBlock} aria-label="Acciones">
      <h3>Acciones</h3>
      {events.length ? <ol className={css.actions} aria-label="Acciones del jugador en el partido">
        {events.map(event => <li key={event.id}>
          <span className={css.minute}>{event.minute}{event.addedTime ? `+${event.addedTime}` : ""}′</span>
          <PlayerAction event={event} playerId={data.player.id} seasonId={data.season.id} matchId={match.id} />
        </li>)}
      </ol> : <p className={shared.message}>Sin acciones registradas en este partido.</p>}
    </section>
    <Link className={css.fullMatch} href={matchHref(match.id)}>Ver partido completo</Link>
  </>;
}
/** A team match the player did not play: context only, no minutes or stats, and no assumed reason. */
function AbsentDetail({ match, data, matchHref }: { match: MatchSummary; data: PlayerSeason; matchHref: (matchId: string) => string }) {
  const home = match.home.id === data.team.id;
  return <>
    <div className={css.detailContext}>
      <p className={css.teams}>J{match.round} · vs <Link href={teamsHref({ seasonId: match.seasonId, teamId: (home ? match.away : match.home).id })}>
        {(home ? match.away : match.home).name}</Link> <Score match={match} /></p>
      <p className={css.meta}>{home ? "Local" : "Visitante"} · {dateLabel(match.kickoff)} · No participó</p>
    </div>
    <p className={shared.message}>Sin minutos ni estadísticas individuales en este partido.</p>
    <Link className={css.fullMatch} href={matchHref(match.id)}>Ver partido completo</Link>
  </>;
}
function PlayerAction({ event, playerId, seasonId, matchId }: { event: MatchEvent; playerId: string; seasonId: string; matchId: string }) {
  if (event.type === "goal") {
    const assist = event.assist?.id === playerId && event.player.id !== playerId;
    return <span className={css.actionText}>
      {assist ? <AssistIcon /> : <GoalIcon />}
      <span>{assist ? <>Asistencia · Gol de <PlayerLink player={event.player} seasonId={seasonId}
        origin={{ kind: "match", id: matchId, tab: "lineups" }} /></> : event.ownGoal ? "Gol en propia puerta" : event.penalty ? "Gol · Penalti" : "Gol"}
        {assist ? <span className={css.meta}>Minuto del gol asistido</span> : null}</span>
    </span>;
  }
  if (event.type === "card") return <span className={css.actionText}>
    <span className={css.cardIcon} data-card={event.card} aria-hidden="true"><span />{event.card === "second_yellow" ? <span /> : null}</span>
    <span>{event.card === "yellow" ? "Amarilla" : event.card === "second_yellow" ? "Segunda amarilla" : "Roja"}</span>
  </span>;
  const enters = event.playerIn.id === playerId;
  return <span className={css.actionText}>{enters ? <ArrowUp size={16} aria-hidden="true" /> : <ArrowDown size={16} aria-hidden="true" />}
    <span>{enters ? "Entra" : "Sale"}</span></span>;
}
