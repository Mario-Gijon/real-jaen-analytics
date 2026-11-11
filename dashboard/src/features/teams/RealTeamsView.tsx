"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { ApiRequestError, getObservedSquad, getObservedTeam, getTeamObservedPerformance } from "@/client/api";
import type { ObservedCompetitionPerformance, ObservedTeam, TeamRef } from "@/contract";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { CLUB_ID } from "@/lib/club";
import { observedCrest } from "@/lib/team-crests";
import { observedMatchHref } from "@/lib/navigation";
import { useApi } from "@/hooks/use-api";
import { TeamCrest } from "@/ui/TeamCrest";
import { ObservedSquadTable } from "./ObservedSquadTable";
import { RealTeamPerformance } from "./RealTeamPerformance";
import { gridOrder, TeamShell, useTeamsUrl, type Props, type Tab } from "./TeamShell";
import css from "./TeamsView.module.css";

/** A real team as the crest grid and header draw it: the source's name and its crest file, or a neutral shield. */
const realTeamRef = (team: ObservedTeam): TeamRef => ({ id: team.id, ...observedCrest(team) });

// The current product scope. Keep competition selection internal until other scopes are enabled.
const CURRENT_SCOPE = { name: "Primera Federación, Grupo 2", season: "26/27" };

/**
 * Teams from the real source (`TEAMS_SOURCE=local`): only teams with an
 * explicit correspondence, and the competitions of the source's scope as
 * seasons, never related to the synthetic ones. Performance shows the
 * results of the observed finished matches; the players tab shows the real
 * observed players with their figures in the selected competition when that
 * source is enabled (`OBSERVED_PLAYERS_SOURCE=local`), and says so when it
 * is not. Nothing synthetic is loaded.
 */
export function RealTeamsView({ initialTeamId, initialSeasonId, initialTab = "performance" }: Props) {
  // The team asked for (URL or grid); without one, the club.
  const [chosenTeamId, setTeamId] = useState<string | null>(initialTeamId ?? null);
  const teamId = chosenTeamId ?? CLUB_ID;
  const load = useApi(`real-team|${teamId}`, (signal) => getTeamObservedPerformance(teamId, { signal }));
  // A season id from the URL must be one of the source's; a synthetic one is said so, never swapped.
  const [seasonId, setSeasonId] = useState<string | null>(initialSeasonId ?? null);
  const [tab, setTab] = useState<Tab>(initialTab);
  // Every competition at once: the season selector then picks the figures without asking again.
  const squad = useApi(tab === "players" ? `observed-squad|${teamId}` : null, (signal) =>
    getObservedSquad(teamId, { signal }),
  );
  const competitions = load.data?.competitions ?? [];
  const competition: ObservedCompetitionPerformance | null =
    competitions.find((c) => c.name === CURRENT_SCOPE.name && c.season === CURRENT_SCOPE.season
      && (seasonId === null || c.id === seasonId)) ?? null;
  const notFound = load.error instanceof ApiRequestError && load.error.code === "NOT_FOUND";
  // A 404 from performance can mean a known opponent without an export. Resolve its identity separately;
  // never substitute the selected team's own data or infer its name from another record.
  const identity = useApi(notFound ? `real-team-identity|${teamId}` : null,
    (signal) => getObservedTeam(teamId, { signal }));
  const teams = useMemo(() => gridOrder((load.data?.teams ?? []).map(realTeamRef)), [load.data]);
  const team = teams.find((t) => t.id === teamId) ?? realTeamRef({ id: teamId, name: null });
  const notes = competition ? coverage(competition) : "";
  // A match opened here returns to this team, season and tab, as the URL keeps them.
  const matchHref = (matchId: string) => observedMatchHref(matchId, { kind: "teams", seasonId, teamId: chosenTeamId, tab });
  // Only a team and season the source serves are written; an unknown one stays as asked, with its explicit state.
  useTeamsUrl(load.data && competition ? { seasonId, teamId: chosenTeamId, tab } : null);

  return (
    <>

      {notFound ? (
        identity.error ? <div className={css.status} role="status">
          <p>La fuente real no pudo resolver la identidad de este equipo.</p>
          <Button onClick={identity.retry} size="sm">Reintentar</Button>
          <Link href="/teams" onClick={() => setTeamId(null)}>Ver el Real Jaén</Link>
        </div> : !identity.data ? <section aria-label="Identidad del equipo" aria-busy="true">
          <LoadingState variant="section" label="Resolviendo equipo…" />
        </section>
          : <section className={css.identityOnly} aria-labelledby="team-identity-name">
            <header className={css.identity}>
              <TeamCrest size={44} team={realTeamRef(identity.data)} />
              <h2 id="team-identity-name">{identity.data.name}</h2>
            </header>
            <p role="status">Este equipo está identificado, pero no tiene estadísticas propias disponibles para el análisis. No se muestran datos de otro equipo.</p>
            <Link href="/teams" onClick={() => setTeamId(null)}>Ver el Real Jaén</Link>
          </section>
      ) : load.error ? (
        <div className={css.error} role="alert">
          <p>No se pudieron cargar los datos reales del equipo.</p>
          <Button onClick={load.retry} size="sm">Reintentar</Button>
        </div>
      ) : !load.data ? (
        <LoadingState variant="page" label="Cargando equipo…" />
      ) : !competition ? (
        <div className={css.status} role="status">
          <p>
            {competitions.length
              ? "La temporada indicada no corresponde a la fuente real de equipos: sus temporadas son las de la fuente."
              : "La fuente real no tiene competiciones para este equipo."}
          </p>
          {competitions.length ? <Link href="/teams" onClick={() => setSeasonId(null)}>Ver la temporada real</Link> : null}
        </div>
      ) : (
        <TeamShell
          busy={tab === "players" && squad.loading}
          onSelectTeam={setTeamId}
          onTab={setTab}
          subtitle=""
          tab={tab}
          team={team}
          teams={teams}
        >
          {tab === "performance" ? (
            <div className={css.real}>
              {notes ? <p className={css.status}>{notes}</p> : null}
              <RealTeamPerformance competition={competition} key={`${teamId}|${competition.id}`} matchHref={matchHref} />
            </div>
          ) : squad.error ? (
            <ObservedError error={squad.error} retry={squad.retry} />
          ) : !squad.data ? (
            <LoadingState variant="section" label="Cargando jugadores…" />
          ) : (
            <ObservedSquadTable competitionId={competition.id} squad={squad.data} />
          )}
        </TeamShell>
      )}
    </>
  );
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What the figures rest on: counted finished matches out of the observed ones, and what was left out. */
function coverage({ matches, output }: ObservedCompetitionPerformance): string {
  const parts: string[] = [];
  if (matches.finishedWithoutValidScore) {
    parts.push(`${plural(matches.finishedWithoutValidScore, "finalizado sin marcador válido no se incluye", "finalizados sin marcador válido no se incluyen")}.`);
  }
  if (matches.excludedByVersionConflict) {
    parts.push(`${plural(matches.excludedByVersionConflict, "finalizado con versiones contradictorias no se incluye", "finalizados con versiones contradictorias no se incluyen")}.`);
  }
  if (matches.unknownStatus) {
    parts.push(`${plural(matches.unknownStatus, "partido con estado desconocido no se incluye", "partidos con estado desconocido no se incluyen")}.`);
  }
  if (matches.replacedLocally) {
    parts.push(`${plural(matches.replacedLocally, "evento sustituido por otro por decisión local se conserva", "eventos sustituidos por otros por decisión local se conservan")} sin contar.`);
  }
  if (output?.undated) {
    parts.push(`${plural(output.undated, "partido sin fecha no entra", "partidos sin fecha no entran")} en «Últimos 3».`);
  }
  return parts.join(" ");
}

/** The observed players could not be read: a team the source does not know, or a failure with retry. */
function ObservedError({ error, retry }: { error: unknown; retry: () => void }) {
  return error instanceof ApiRequestError && error.code === "NOT_FOUND" ? (
    <p className={css.status} role="status">
      La fuente de datos real no tiene jugadores observados de este equipo.
    </p>
  ) : (
    <div className={css.error} role="alert">
      <p>{error instanceof ApiRequestError ? error.message : "No se pudieron cargar los jugadores."}</p>
      <Button onClick={retry} size="sm">
        Reintentar
      </Button>
    </div>
  );
}
