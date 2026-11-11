"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ApiRequestError, getTeamObservedMatches, getTeamObservedPerformance } from "@/client/api";
import type { ObservedMatch, ObservedTeam } from "@/contract";
import { CLUB_ID } from "@/lib/club";
import { homeHref, matchesHref, observedMatchHref, teamsHref } from "@/lib/navigation";
import { observedCrest } from "@/lib/team-crests";
import { useApi } from "@/hooks/use-api";
import { LoadingState } from "@/ui/LoadingState";
import { daysUntil, preparationChoices, preparationSummary, upcomingMatches } from "./preparation";
import { fixtureLabel, HistorySection, LoadError, TargetSection, type Fixture, type HistoryRow } from "./PreparationParts";
import { rivalHistory, unconfirmedClubMatches } from "./real-preparation";
import css from "./HomeView.module.css";

const crestTeam = (team: ObservedTeam) => ({ id: team.id, ...observedCrest(team), href: teamsHref({ teamId: team.id }) });
/** Only dated matches become fixtures (`upcomingMatches` and `preparationHistory` keep no other). */
const fixture = (match: ObservedMatch): Fixture =>
  ({ id: match.id, round: match.round, kickoff: match.kickoff!, home: crestTeam(match.home), away: crestTeam(match.away) });
const opponentName = (match: ObservedMatch) => (match.home.id === CLUB_ID ? match.away : match.home).name ?? "Equipo sin identificar";
const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * «Inicio» from the real source: the club's next current match with a
 * confirmed date and its rival's last finished matches before it, with the
 * coverage of each figure. Loads nothing synthetic; a season in the URL is
 * one of the app's synthetic ones, said so, never swapped.
 */
export function RealHomeView({ seasonId, matchId, now }: { seasonId?: string; matchId?: string; now: number }) {
  if (seasonId) return <div role="status" className={css.message}>
    <p>La temporada indicada no corresponde a la fuente real: la preparación con datos reales usa el ámbito de la fuente.</p>
    <Link href="/">Ver la preparación con datos reales</Link>
  </div>;
  return <RealPreparation matchId={matchId} now={now} />;
}

function RealPreparation({ matchId, now }: { matchId?: string; now: number }) {
  const router = useRouter();
  const club = useApi("home-real-club", signal => getTeamObservedMatches(CLUB_ID, { signal }));
  const rows = club.data?.matches ?? [];
  const future = upcomingMatches(rows.map(row => row.match), now);
  const target = matchId ? future.find(match => match.id === matchId) : future[0];
  const rival = target ? target.home.id === CLUB_ID ? target.away : target.home : null;
  // The rival is analysed only with an export of its own (its performance answers 404 otherwise).
  const performance = useApi(rival ? `home-rival-performance|${rival.id}` : null,
    signal => getTeamObservedPerformance(rival!.id, { signal }));
  const rivalMatches = useApi(rival ? `home-rival-matches|${rival.id}` : null,
    signal => getTeamObservedMatches(rival!.id, { signal }));
  const history = target && performance.data && rivalMatches.data
    ? rivalHistory(rivalMatches.data.matches, target, performance.data.competitions, now) : null;

  if (club.error) return <LoadError message="No se pudo cargar el calendario real del Real Jaén." retry={club.retry} />;
  if (!club.data) return <LoadingState variant="page" label="Cargando calendario…" />;

  // The competition as the source's scope declares it, as Equipos names it (a match carries the provider's own name).
  const context = club.data.competitions.map(c => [c.name, c.season].filter(Boolean).join(" · ")).join("; ");
  const noExport = performance.error instanceof ApiRequestError && performance.error.code === "NOT_FOUND";
  const analysable = rival && performance.data ? crestTeam(rival) : null;
  const unconfirmed = unconfirmedClubMatches(rows, now);
  const notes = [
    ...unconfirmed.postponed.map(match => `${[match.round === null ? null : `J${match.round}`, opponentName(match)].filter(Boolean).join(" · ")}: `
      + "aplazado, pendiente de disputar sin nueva fecha confirmada. No se propone para preparar."),
    ...unconfirmed.stale.map(match => `${fixtureLabel(fixture(match))} · ${opponentName(match)}: figura como programado aunque su fecha ya ha pasado; `
      + "los datos importados pueden no estar actualizados. No se propone para preparar."),
    ...unconfirmed.unknownStatus.map(match => `${match.kickoff ? fixtureLabel(fixture(match)) : "Sin fecha"} · ${opponentName(match)}: `
      + "estado desconocido en la fuente. No se considera para elegir el próximo encuentro."),
  ];

  return <>
    <TargetSection context={context} target={target ? fixture(target) : null}
      choices={preparationChoices(future, target).map(fixture)}
      onChoose={choice => router.push(homeHref({ matchId: choice.id }), { scroll: false })}
      days={target ? daysUntil(target.kickoff, now) : null}
      analyze={analysable ? { href: teamsHref({ teamId: analysable.id }), label: `Analizar ${analysable.shortName}` } : null}
      empty={matchId ? { text: "El encuentro seleccionado no es un próximo encuentro vigente del Real Jaén con fecha confirmada. Elige otro encuentro.", alert: true }
        : { text: "No hay próximos encuentros vigentes del Real Jaén con fecha confirmada en los datos importados.", alert: false }}
      notes={notes.length ? <div className={css.notes}>{notes.map(note => <p key={note}>{note}</p>)}</div> : null} />
    {!target || !rival ? null
      : noExport ? <p role="status" className={css.message}>Este rival no tiene exportación propia en la fuente real: no se muestran sus últimos partidos.</p>
        : performance.error || rivalMatches.error ? <LoadError message="No se pudieron cargar los partidos del rival."
          retry={() => { if (performance.error) performance.retry(); if (rivalMatches.error) rivalMatches.retry(); }} />
          : !history ? <section aria-label="Partidos recientes del rival" aria-busy="true">
            <LoadingState variant="section" label="Cargando partidos del rival…" />
          </section>
            : <RealHistory history={history} targetId={target.id} rival={crestTeam(rival)} />}
  </>;
}

function RealHistory({ history, targetId, rival }: {
  history: ReturnType<typeof rivalHistory>; targetId: string; rival: { id: string; name: string };
}) {
  const { recent, counted, upcomingBefore, overdueBefore, unknownStatus, undatedFinished } = history;
  // The performance response already orients each counted match's xG to this rival.
  const { xg, opponentXg, ...form } = preparationSummary(counted.map(entry => ({ ...entry, teamStats: null })));
  // A match opened from here returns to this target.
  const rows: HistoryRow[] = recent.map(({ match, result }) => ({
    ...fixture(match), score: result === null ? null : match.score,
    note: result === null ? "Resultado no contabilizado" : undefined,
    href: observedMatchHref(match.id, { kind: "home", seasonId: null, matchId: targetId }),
  }));
  const coverage = [
    recent.length && recent.length < 5
      ? `${recent.length} de 5: son todos los partidos finalizados del rival antes del encuentro en los datos importados.` : null,
    recent.length > counted.length
      ? `${plural(recent.length - counted.length, "partido sin resultado contabilizado (marcador no válido o versiones contradictorias) queda",
        "partidos sin resultado contabilizado (marcador no válido o versiones contradictorias) quedan")} fuera de la síntesis.` : null,
    upcomingBefore ? `${plural(upcomingBefore,
      "partido del rival previsto antes del encuentro todavía no se ha disputado",
      "partidos del rival previstos antes del encuentro todavía no se han disputado")}.` : null,
    overdueBefore ? `${plural(overdueBefore,
      "partido del rival con fecha ya pasada sigue sin resultado en los datos importados (aplazado o sin actualizar) y no se incluye",
      "partidos del rival con fecha ya pasada siguen sin resultado en los datos importados (aplazados o sin actualizar) y no se incluyen")}.` : null,
    unknownStatus ? `${plural(unknownStatus,
      "partido del rival con estado desconocido en la fuente no se considera",
      "partidos del rival con estado desconocido en la fuente no se consideran")}: no se sabe si se disputó.` : null,
    undatedFinished ? `${plural(undatedFinished, "partido finalizado sin fecha no se incluye", "partidos finalizados sin fecha no se incluyen")}: no se puede situar antes del encuentro.` : null,
  ].filter((line): line is string => line !== null);
  return <HistorySection team={rival.name} rows={rows} summary={counted.length ? form : null} expectation={{ xg, opponentXg }}
  allHref={matchesHref({ teamId: rival.id })}
    coverage={coverage.length ? <div className={css.notes}>{coverage.map(line => <p key={line}>{line}</p>)}</div> : null} />;
}
