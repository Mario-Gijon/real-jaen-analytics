"use client";

import Link from "next/link";
import { useRef, useState, type KeyboardEvent } from "react";
import { ApiRequestError, getCompetitions, getMatch } from "@/client/api";
import type { MatchDetail, TeamRef } from "@/contract";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { TeamCrest } from "@/ui/TeamCrest";
import { dateLabel } from "@/lib/format";
import { useApi } from "@/hooks/use-api";
import { teamsHref } from "@/lib/navigation";
import { MatchTimeline } from "./MatchTimeline";
import { TeamStatsComparison } from "./TeamStatsComparison";
import { MatchLineups } from "./MatchLineups";
import { MatchCommentsPanel } from "./MatchCommentsPanel";
import css from "./MatchDetailView.module.css";

const STATUS = { finished: "Finalizado", scheduled: "Programado", postponed: "Aplazado" };
const SECTIONS = ["summary", "timeline", "lineups", "comments"] as const;
const timeFormat = new Intl.DateTimeFormat("es-ES", {
  hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid",
});

/** `homeReturnHref`: the «Inicio» this match was opened from, to which «Volver» returns; otherwise Partidos. */
export function MatchDetailView({ matchId, initialSection = "summary", playerReturnHref = null, teamsReturnHref = null, homeReturnHref = null }: {
  matchId: string; initialSection?: (typeof SECTIONS)[number]; playerReturnHref?: string | null; teamsReturnHref?: string | null; homeReturnHref?: string | null;
}) {
  const [section, setSection] = useState<(typeof SECTIONS)[number]>(initialSection);
  const tabs = useRef<HTMLDivElement>(null);
  function moveTab(event: KeyboardEvent<HTMLButtonElement>) {
    const index = SECTIONS.indexOf(section);
    const next = event.key === "Home" ? SECTIONS[0] : event.key === "End" ? SECTIONS[SECTIONS.length - 1]
      : event.key === "ArrowLeft" ? SECTIONS[(index + SECTIONS.length - 1) % SECTIONS.length]
        : event.key === "ArrowRight" ? SECTIONS[(index + 1) % SECTIONS.length] : null;
    if (!next) return;
    event.preventDefault();
    setSection(next);
    tabs.current?.querySelector<HTMLButtonElement>(`#match-${next}-tab`)?.focus();
  }
  const detail = useApi(`match|${matchId}`, (signal) => getMatch(matchId, { signal }));
  const competitions = useApi("competitions", (signal) => getCompetitions({ signal }));
  const match = detail.data;
  const competition = competitions.data?.find((c) => c.seasons.some((s) => s.id === match?.seasonId));
  const season = competition?.seasons.find((s) => s.id === match?.seasonId);
  const notFound = detail.error instanceof ApiRequestError && detail.error.status === 404;

  return <div className={css.page}>
    {playerReturnHref ? <Link href={playerReturnHref} className={css.back}>‹ Volver al jugador</Link>
      : teamsReturnHref ? <Link href={teamsReturnHref} className={css.back}>‹ Volver al equipo</Link>
        : homeReturnHref ? <Link href={homeReturnHref} className={css.back}>‹ Volver a Inicio</Link>
          : <Link href="/matches" className={css.back}>‹ Volver a partidos</Link>}
    {detail.error ? <div role="alert" className={css.message}>
      <h1>{notFound ? "Partido no encontrado" : "No se pudo cargar el partido"}</h1>
      {!notFound ? <Button size="sm" onClick={detail.retry}>Reintentar</Button> : null}
    </div> : !match ? <section aria-label="Detalles del partido" aria-busy="true">
      <LoadingState variant="section" label="Cargando partido…" />
    </section> : <>
      <header className={css.header}>
        <h1 className={css.matchHeading}>{match.home.name} contra {match.away.name}</h1>
        {competition && season ? <p className={css.competition}>{competition.name} · {season.name}</p> : null}
        <p className={css.context}>
          <span>Jornada {match.round}</span>
          {match.kickoff ? <time dateTime={match.kickoff}>
            {dateLabel(match.kickoff)} · {timeFormat.format(new Date(match.kickoff))}
          </time> : <span>Fecha por definir</span>}
        </p>
        <div className={css.fixture}>
          <Team team={match.home} side="home" seasonId={match.seasonId} />
          <div className={css.center}>
            <strong className={css.result} data-state={match.status}>
              {match.status === "finished" && match.score
                ? `${match.score.home} – ${match.score.away}`
                : match.status === "postponed" ? "—"
                  : match.kickoff ? timeFormat.format(new Date(match.kickoff)) : "Por definir"}
            </strong>
            <span className={css.state}>{STATUS[match.status]}</span>
          </div>
          <Team team={match.away} side="away" seasonId={match.seasonId} />
        </div>
      </header>
      <div className={css.tabs} role="tablist" aria-label="Secciones del partido" ref={tabs}>
        <button type="button" role="tab" id="match-summary-tab" aria-selected={section === "summary"}
          tabIndex={section === "summary" ? 0 : -1} onClick={() => setSection("summary")} onKeyDown={moveTab}
          aria-controls="match-summary-panel" className={css.tab}>Resumen</button>
        <button type="button" role="tab" id="match-timeline-tab" aria-selected={section === "timeline"}
          tabIndex={section === "timeline" ? 0 : -1} onClick={() => setSection("timeline")} onKeyDown={moveTab}
          aria-controls="match-timeline-panel" className={css.tab}>Cronología</button>
        <button type="button" role="tab" id="match-lineups-tab" aria-selected={section === "lineups"}
          tabIndex={section === "lineups" ? 0 : -1} onClick={() => setSection("lineups")} onKeyDown={moveTab}
          aria-controls="match-lineups-panel" className={css.tab}>Alineaciones</button>
        <button type="button" role="tab" id="match-comments-tab" aria-selected={section === "comments"}
          tabIndex={section === "comments" ? 0 : -1} onClick={() => setSection("comments")} onKeyDown={moveTab}
          aria-controls="match-comments-panel" className={css.tab}>Eventos</button>
      </div>
      <section role="tabpanel" id={`match-${section}-panel`} aria-labelledby={`match-${section}-tab`} className={css.summary}>
        {section === "summary" ? <Summary match={match} />
          : section === "timeline" ? <MatchTimeline match={match} />
            : section === "lineups" ? <MatchLineups match={match} />
              : <MatchCommentsPanel matchId={match.id} seasonId={match.seasonId}
                homeName={match.home.name} awayName={match.away.name} />}
      </section>
    </>}
  </div>;
}

function Team({ team, side, seasonId }: { team: TeamRef; side: "home" | "away"; seasonId: string }) {
  return <div className={`${css.team} ${css[side]}`}>
    <TeamCrest team={team} size={52} />
    <Link className={css.teamName} href={teamsHref({ seasonId, teamId: team.id })} aria-label={`Ver equipo ${team.name}`}>{team.name}</Link>
  </div>;
}

function Summary({ match }: { match: MatchDetail }) {
  if (!match.teamStats) return <p className={css.empty} role="status">
    {match.status === "scheduled" ? "Las estadísticas estarán disponibles después del partido."
      : match.status === "postponed" ? "Partido aplazado. No hay estadísticas disponibles."
        : "No hay estadísticas disponibles para este partido."}
  </p>;

  return <>
    <h2 className={css.summaryTitle}>Estadísticas de equipo</h2>
    <TeamStatsComparison home={{ label: match.home.shortName, name: match.home.name, href: teamsHref({ seasonId: match.seasonId, teamId: match.home.id }), stats: match.teamStats.home }}
      away={{ label: match.away.shortName, name: match.away.name, href: teamsHref({ seasonId: match.seasonId, teamId: match.away.id }), stats: match.teamStats.away }} />
  </>;
}
