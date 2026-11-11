"use client";

import Link from "next/link";
import { useRef, useState, type KeyboardEvent } from "react";
import { ApiRequestError, getObservedMatchDetail, getObservedSquad } from "@/client/api";
import type { LocalReplacement, MatchStatus, ObservedMatch, ObservedMatchDetail, ObservedSectionKey, ObservedSectionReview, ObservedSquad, ObservedTeam, PlayerRef } from "@/contract";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { ProvenanceNotice } from "@/ui/ProvenanceNotice";
import { TeamCrest } from "@/ui/TeamCrest";
import { dateLabel } from "@/lib/format";
import { relatedObservedMatchHref, matchesInitialState, teamsHref, type ObservedMatchListOrigin } from "@/lib/navigation";
import { useApi } from "@/hooks/use-api";
import { TeamStatsComparison } from "./TeamStatsComparison";
import { ObservedLineups } from "./ObservedLineups";
import { MatchCommentsPanel } from "./MatchCommentsPanel";
import { observedCrest } from "@/lib/team-crests";
import css from "./MatchDetailView.module.css";

const STATUS: Record<MatchStatus, string> = { finished: "Finalizado", scheduled: "Programado", postponed: "Aplazado" };
/** The synthetic detail's tabs without «Cronología»: no real timeline yet. */
const SECTIONS = ["summary", "lineups", "comments"] as const;
type Section = (typeof SECTIONS)[number];
const LABEL: Record<Section, string> = { summary: "Resumen", lineups: "Alineaciones", comments: "Eventos" };
const timeFormat = new Intl.DateTimeFormat("es-ES", {
  hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid",
});
const publicPlayerIdPattern = /^sofascore-player-[1-9]\d*$/;

function playersForMatch(squad: ObservedSquad | null, teamId: string, match: ObservedMatch): PlayerRef[] {
  if (!squad || squad.teamId !== teamId || !match.competition || !match.season) return [];
  const competitions = squad.competitions.filter((entry) => entry.name === match.competition && entry.season === match.season);
  return competitions.length === 1 ? competitions[0].players.map(({ player }) => player) : [];
}

/**
 * Detail of a real match (reached from the real match list, a real profile,
 * a team in «Equipos» or «Inicio», to which «Volver» then returns), organised like
 * the synthetic detail: header, then «Resumen» (team stats) and
 * «Alineaciones» (line-ups marked with the recorded goals, assists and
 * substitutions). Each section keeps its own state and retry, with
 * provenance. Loads nothing synthetic; an id the real source does not know is
 * said so, never looked up among the synthetic matches.
 */
export function RealMatchDetailView({ matchId, initialSection = "summary", playerReturnHref = null, teamsReturnHref = null, homeReturnHref = null,
  matchesReturnHref = null, originQuery = {} }: {
  matchId: string; initialSection?: Section; playerReturnHref?: string | null; teamsReturnHref?: string | null; homeReturnHref?: string | null;
  /** «Partidos» on the team and status the match was opened from; without it, plain «Partidos». */
  matchesReturnHref?: string | null;
  /** The query this match was opened with: a related match keeps its origin. */
  originQuery?: Record<string, unknown>;
}) {
  const detail = useApi(`observed-match|${matchId}`, (signal) => getObservedMatchDetail(matchId, { signal }));
  const notFound = detail.error instanceof ApiRequestError && detail.error.status === 404;
  // Kept here so a section retry, which reloads the detail, stays on the same tab.
  const [section, setSection] = useState<Section>(initialSection);

  return <div className={css.page}>
    {playerReturnHref
      ? <Link href={playerReturnHref} className={css.back}>‹ Volver al jugador</Link>
      : teamsReturnHref ? <Link href={teamsReturnHref} className={css.back}>‹ Volver al equipo</Link>
      : homeReturnHref ? <Link href={homeReturnHref} className={css.back}>‹ Volver a Inicio</Link>
      : <Link href={matchesReturnHref ?? "/matches"} className={css.back}>‹ Volver a partidos</Link>}
    {notFound ? <div role="alert" className={css.message}>
        <h1>Partido no encontrado en la fuente real</h1>
      </div>
      : detail.error ? <Failure title="No se pudo cargar el partido" retry={detail.retry} />
      : !detail.data ? <section aria-label="Detalles del partido" aria-busy="true">
        <LoadingState variant="section" label="Cargando partido…" />
      </section>
      : <Detail data={detail.data} retry={detail.retry} section={section} setSection={setSection}
        linkProfiles matchesOrigin={originQuery.from === "matches" ? { kind: "matches", teamId: new URLSearchParams(matchesReturnHref?.split("?")[1]).get("team"), state: matchesInitialState(originQuery) } : undefined} relatedHref={(id) => relatedObservedMatchHref(id, originQuery)} />}
  </div>;
}

function Failure({ title, retry }: { title: string; retry: () => void }) {
  return <div role="alert" className={css.message}>
    <h1>{title}</h1>
    <Button size="sm" onClick={retry}>Reintentar</Button>
  </div>;
}

function Detail({ data, retry, linkProfiles, section, setSection, relatedHref, matchesOrigin }: {
  data: ObservedMatchDetail; retry: () => void; linkProfiles: boolean; matchesOrigin?: ObservedMatchListOrigin; section: Section; setSection: (section: Section) => void;
  relatedHref: (matchId: string) => string;
}) {
  const { match } = data;
  // Team-season observations supplement the event participants and lineups only while the Events tab is open.
  const squadKey = section === "comments" && match.competition && match.season
    ? (teamId: string) => `match-squad|${teamId}|${match.competition}|${match.season}` : () => null;
  const homeSquad = useApi(squadKey(match.home.id), (signal) => getObservedSquad(match.home.id, { signal }));
  const awaySquad = useApi(squadKey(match.away.id), (signal) => getObservedSquad(match.away.id, { signal }));
  const rosterById = new Map<string, PlayerRef>();
  const rosterSources = [
    ...(data.lineups.state === "stored" ? [...data.lineups.home, ...data.lineups.away].map(({ player }) => player) : []),
    ...playersForMatch(homeSquad.data, match.home.id, match),
    ...playersForMatch(awaySquad.data, match.away.id, match),
  ];
  for (const player of rosterSources) {
    if (publicPlayerIdPattern.test(player.id) && !rosterById.has(player.id)) rosterById.set(player.id, player);
  }
  const rosterPlayers = [...rosterById.values()];
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
  const home = match.home.name ?? "Equipo sin identificar";
  const away = match.away.name ?? "Equipo sin identificar";
  const central = match.status === "finished"
    ? match.score ? `${match.score.home} – ${match.score.away}` : "—"
    : match.status === "scheduled" && match.kickoff ? timeFormat.format(new Date(match.kickoff))
      : match.status === "scheduled" ? "Por definir" : "—";
  const state = match.status === null ? "Estado desconocido"
    : match.status === "finished" && !match.score ? "Finalizado · sin resultado" : STATUS[match.status];
  return <>
    <header className={css.header}>
      <h1 className={css.matchHeading}>{home} contra {away}</h1>
      <p className={css.context}>
        {match.round !== null ? <span>Jornada {match.round}</span> : null}
        {match.kickoff ? <time dateTime={match.kickoff}>
          {dateLabel(match.kickoff)} · {timeFormat.format(new Date(match.kickoff))}
        </time> : <span>Fecha por definir</span>}
      </p>
      <div className={css.fixture}>
      <Team team={match.home} side="home" />
        <div className={css.center}>
          <strong className={css.result} data-state={match.status === "finished" && match.score ? "finished" : "scheduled"}>{central}</strong>
          <span className={css.state}>{state}</span>
        </div>
      <Team team={match.away} side="away" />
      </div>
    </header>
    {data.localReplacement ? <div className={css.provenance}>
      <LocalReplacementNotice replacement={data.localReplacement} relatedHref={relatedHref} />
    </div> : null}
    {data.review ? <ReviewNotice review={data.review} /> : null}
    <div className={css.tabs} role="tablist" aria-label="Secciones del partido" ref={tabs}>
      {SECTIONS.map((id) => <button key={id} type="button" role="tab" id={`match-${id}-tab`} aria-selected={section === id}
        tabIndex={section === id ? 0 : -1} onClick={() => setSection(id)} onKeyDown={moveTab}
        aria-controls={`match-${id}-panel`} className={css.tab}>{LABEL[id]}</button>)}
    </div>
    <section role="tabpanel" id={`match-${section}-panel`} aria-labelledby={`match-${section}-tab`} className={css.summary}>
        {section === "summary" ? <Summary data={data} retry={retry} />
          : section === "lineups" ? <Lineups data={data} retry={retry} linkProfiles={linkProfiles} matchesOrigin={matchesOrigin} />
            : <MatchCommentsPanel matchId={match.id} homeName={home} awayName={away}
              rosterPlayers={rosterPlayers}
              profileOrigin={{ kind: "observedMatch", id: match.id, matches: matchesOrigin }} />}
    </section>
  </>;
}

const SECTION_NAMES: Record<ObservedSectionKey, string> = { statistics: "estadísticas", lineups: "alineaciones", incidents: "incidencias" };
/** «a, b y c»; «e» before a word starting with the sound /i/ («alineaciones e incidencias»). */
const list = (items: string[]) => {
  const last = items[items.length - 1];
  return items.length > 1 ? `${items.slice(0, -1).join(", ")} ${/^h?i(?![aeiouáéó])/i.test(last) ? "e" : "y"} ${last}` : last;
};

/**
 * Sections read before a correction of the event, still pending review. They stay as evidence; what the
 * correction makes incompatible is not used (never shown as 0).
 */
function ReviewNotice({ review }: { review: ObservedSectionReview }) {
  const sections = list(review.sections.map((s) => SECTION_NAMES[s]));
  return <div className={css.review} role="note" aria-label="Secciones pendientes de revisión">
    {review.corrections.includes("score") ? <p><strong>El marcador se ha actualizado, pero algunas secciones siguen pendientes de revisión.</strong></p> : null}
    {review.corrections.includes("teams") ? <p><strong>Los equipos del partido han cambiado y algunas secciones siguen pendientes de revisión.</strong></p> : null}
    <p>
      Pendientes: {sections}, leídas antes de la corrección.{" "}
      {review.corrections.includes("teams")
        ? "Las de los equipos anteriores no se muestran ni cuentan para nada; se conservan como evidencia."
        : "Sus goles y asistencias no se usan; lo que no depende del marcador sí."}
    </p>
  </div>;
}

const LOCAL_DECISION = { synthetic: false, label: "Decisión local" } as const;
const decision = (r: LocalReplacement) => `Decidido por ${r.decidedBy} el ${dateLabel(r.decidedOn)}. No es un dato de la fuente: SofaScore no enlaza los dos eventos.`;

/** A local decision on this event, said as such, with a link to the other event. */
function LocalReplacementNotice({ replacement: { replacedBy, replaces }, relatedHref }: {
  replacement: NonNullable<ObservedMatchDetail["localReplacement"]>; relatedHref: (matchId: string) => string;
}) {
  return <ProvenanceNotice source={LOCAL_DECISION}>
    {replacedBy ? <>
      <p>Este evento está sustituido por otro y no cuenta como encuentro vigente ni pendiente. {replacedBy.reason}</p>
      <p>{decision(replacedBy)}</p>
      <p><Link href={relatedHref(replacedBy.matchId)}>Ver el encuentro vigente</Link></p>
    </> : replaces.map((r) => <div key={r.matchId}>
      <p>Este encuentro sustituye a un evento anterior, que se conserva sin contar en el calendario. {r.reason}</p>
      <p>{decision(r)}</p>
      <p><Link href={relatedHref(r.matchId)}>Ver el evento sustituido</Link></p>
    </div>)}
  </ProvenanceNotice>;
}

function Summary({ data: { match, statistics }, retry }: { data: ObservedMatchDetail; retry: () => void }) {
  const home = match.home.name ?? "Equipo sin identificar";
  const away = match.away.name ?? "Equipo sin identificar";
  return <>
    {statistics.state === "stored" ? <>
      <TeamStatsComparison home={{ label: home, name: home, href: teamsHref({ teamId: match.home.id }), stats: statistics.home }}
        away={{ label: away, name: away, href: teamsHref({ teamId: match.away.id }), stats: statistics.away }} />
      {statistics.fetchedAt ? <p className={css.fetched}>Obtenidas de la fuente el {dateLabel(statistics.fetchedAt)}.</p> : null}
    </>
      : statistics.state === "error" ? <div role="alert" className={css.sectionError}>
        <p>No se pudieron cargar las estadísticas de este partido.</p>
        <Button size="sm" onClick={retry}>Reintentar</Button>
      </div>
      : <p className={css.empty} role="status">
        {statistics.state === "empty"
          ? "La fuente guardó las estadísticas de este partido sin contenido."
          : statistics.state === "outdated"
            ? "Las estadísticas guardadas son de los equipos anteriores del partido: pendientes de revisión."
            : "La fuente no tiene estadísticas guardadas de este partido."}
      </p>}
  </>;
}

function Lineups({ data: { match, lineups, incidents }, retry, linkProfiles, matchesOrigin }: {
  data: ObservedMatchDetail; retry: () => void; linkProfiles: boolean; matchesOrigin?: ObservedMatchListOrigin;
}) {
  if (lineups.state === "error") return <div role="alert" className={css.sectionError}>
    <p>No se pudieron cargar las alineaciones de este partido.</p>
    <Button size="sm" onClick={retry}>Reintentar</Button>
  </div>;
  if (lineups.state !== "stored") return <p className={css.empty} role="status">
    {lineups.state === "empty"
      ? "La fuente guardó las alineaciones de este partido sin jugadores."
      : lineups.state === "outdated"
        ? "Las alineaciones guardadas son de los equipos anteriores del partido: pendientes de revisión."
        : "La fuente no tiene alineaciones guardadas de este partido."}
  </p>;
  return <>
    <ObservedLineups match={match} confirmed={lineups.confirmed} home={lineups.home} away={lineups.away}
      linkProfiles={linkProfiles} matchesOrigin={matchesOrigin} incidents={incidents} retry={retry} />
    {lineups.fetchedAt ? <p className={css.fetched}>Alineaciones obtenidas de la fuente el {dateLabel(lineups.fetchedAt)}.</p> : null}
  </>;
}

function Team({ team, side }: { team: ObservedTeam; side: "home" | "away" }) {
  const name = observedCrest(team).name;
  return <div className={`${css.team} ${css[side]}`}>
    <TeamCrest team={observedCrest(team)} size={52} />
    <Link className={css.teamName} href={teamsHref({ teamId: team.id })} aria-label={`Ver equipo ${name}`}>{name}</Link>
  </div>;
}
