"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ApiRequestError, getTeamObservedMatches, getTeamObservedPerformance } from "@/client/api";
import type { ObservedMatch } from "@/contract";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/PageHeader";
import { Select } from "@/ui/Select";
import { LoadingState } from "@/ui/LoadingState";
import { CLUB_ID } from "@/lib/club";
import { matchesHref, observedMatchHref, type MatchesState } from "@/lib/navigation";
import { useApi } from "@/hooks/use-api";
import { ObservedMatchList } from "./ObservedMatchList";
import { ALL_TEAMS, mergeTeamMatches, teamOptions } from "./real-matches";
import css from "./MatchesView.module.css";

type State = MatchesState;
const isPending = (match: ObservedMatch) => match.status === "scheduled" || match.status === "postponed";

/** Distinct non-empty values, newest season first / alphabetical competitions. */
function options(values: (string | null)[], descending = false) {
  const unique = [...new Set(values.filter((v): v is string => v !== null))];
  return unique.sort((a, b) => (descending ? -1 : 1) * a.localeCompare(b, "es"));
}

/**
 * «Partidos» from the real source: the matches of one team of the real
 * catalog (the club by default), or of all of them («Todos los equipos»),
 * filtered by status. The competition and season are the source's scope:
 * while it serves a single one, they are applied without being offered (the
 * selectors come back when it serves more). The app's synthetic seasons and
 * rounds play no part; a season or team from the URL that does not belong
 * here is said so.
 */
export function RealMatchesView({ initialSeasonId, initialTeamId, initialState = "all" }: {
  initialSeasonId?: string; initialTeamId?: string; initialState?: MatchesState;
}) {
  // A season in the URL is one of the app's synthetic seasons: it cannot select real matches.
  const unmatchedSeason = initialSeasonId !== undefined;
  const [chosen, setChosen] = useState(initialTeamId ?? CLUB_ID);
  // The teams that can be chosen: the real catalog, as «Equipos» offers it.
  const catalog = useApi(unmatchedSeason ? null : "real-match-teams", (signal) => getTeamObservedPerformance(CLUB_ID, { signal }));
  const teams = useMemo(() => (catalog.data ? teamOptions(catalog.data.teams) : null), [catalog.data]);
  const free = chosen === CLUB_ID || chosen === ALL_TEAMS;
  // A team the catalog does not offer falls back to the club, said so; without the catalog, it is asked as is.
  const rejected = teams && !free && !teams.some((t) => t.id === chosen) ? chosen : null;
  const teamId = rejected ? CLUB_ID : free || teams || catalog.error ? chosen : null;
  const ids = teams?.map((t) => t.id) ?? null;
  const load = useApi(
    unmatchedSeason || teamId === null ? null : teamId === ALL_TEAMS ? (ids ? `real-matches|all|${ids.join(",")}` : null) : `real-matches|${teamId}`,
    async (signal) => {
      if (teamId !== ALL_TEAMS) return mergeTeamMatches([await getTeamObservedMatches(teamId!, { signal })]);
      // One team at a time: the local service is not asked for many lists at once.
      const lists = [];
      for (const id of ids!) lists.push(await getTeamObservedMatches(id, { signal }));
      return mergeTeamMatches(lists);
    },
  );
  const [season, setSeason] = useState("all");
  const [competition, setCompetition] = useState("all");
  const [state, setState] = useState<State>(initialState);
  const all = useMemo(() => load.data?.matches ?? [], [load.data]);
  const seasons = useMemo(() => options(all.map((m) => m.season), true), [all]);
  const competitions = useMemo(() => options(all.map((m) => m.competition)), [all]);
  const served = load.data?.competitions ?? [];
  // A single competition and season served: that one, never «Todas» nor another season (a match the source does
  // not name a season for is still in its scope, which the adapter checks by id).
  const current = served.length === 1 ? served[0] : null;
  const shown = all.filter((m) =>
    (current ? m.season === null || m.season === current.season : (season === "all" || m.season === season) && (competition === "all" || m.competition === competition))
    && (state === "all" || (state === "finished" ? m.status === "finished" : isPending(m))));
  const notFound = load.error instanceof ApiRequestError && load.error.code === "NOT_FOUND";

  // The chosen team and status stay in the URL, replaced without a history entry; an explicit state keeps what was asked for.
  const href = unmatchedSeason || rejected ? null : matchesHref({ teamId: chosen, state });
  useEffect(() => {
    if (href && `${window.location.pathname}${window.location.search}` !== href) {
      window.history.replaceState(window.history.state, "", href);
    }
  }, [href]);

  return (
    <>
      <PageHeader
        actions={served.length > 1 ? (
          <div className={css.scope}>
            <label className={`${css.field} ${css.competition}`}>
              <span>Competición</span>
              <Select disabled={!competitions.length} value={competition} onChange={(e) => setCompetition(e.target.value)}>
                <option value="all">Todas las competiciones</option>
                {competitions.map((c) => <option key={c} value={c}>{c}</option>)}
              </Select>
            </label>
            <label className={`${css.field} ${css.season}`}>
              <span>Temporada</span>
              <Select disabled={!seasons.length} value={season} onChange={(e) => setSeason(e.target.value)}>
                <option value="all">Todas</option>
                {seasons.map((s) => <option key={s} value={s}>{s}</option>)}
              </Select>
            </label>
          </div>
        ) : undefined}
      />
      <div className={css.explorer}>
        {unmatchedSeason ? (
          <div role="status" className={css.unmatched}>
            <p>La temporada seleccionada no corresponde a la fuente real de partidos: sus partidos se clasifican por las temporadas de la fuente.</p>
            {/* Same page: the link also resets the team the URL had asked for. */}
            <Link href="/matches" onClick={() => setChosen(CLUB_ID)}>Ver los partidos reales del Real Jaén</Link>
          </div>
        ) : (
          <>
            <div className={css.filters}>
              <label className={`${css.field} ${css.teamPicker}`} aria-busy={!teams && !catalog.error || undefined}>
                <span>Equipo</span>
                <Select value={teamId ?? chosen} disabled={!teams} onChange={(e) => setChosen(e.target.value)}>
                  {teams ? <>
                    {teams.map((t) => <option key={t.id} value={t.id}>{t.name ?? "Equipo sin identificar"}</option>)}
                    <option value={ALL_TEAMS}>Todos los equipos</option>
                  </> : <option value={teamId ?? chosen}>{chosen === CLUB_ID ? "Real Jaén" : chosen === ALL_TEAMS ? "Todos los equipos" : "Equipo seleccionado"}</option>}
                </Select>
                {!teams && !catalog.error ? <LoadingState variant="inline" label="Cargando equipos…" /> : null}
              </label>
              <label className={`${css.field} ${css.teamPicker}`}>
                <span>Estado</span>
                <Select value={state} onChange={(e) => setState(e.target.value as State)}>
                  <option value="all">Todos</option>
                  <option value="finished">Finalizados</option>
                  <option value="pending">Pendientes</option>
                </Select>
              </label>
            </div>
            {rejected ? <p role="status" className={css.unmatched}>El equipo indicado no está disponible en la fuente real: se muestran los partidos del Real Jaén.</p> : null}
            {catalog.error ? (
              <div role="alert" className={css.error}>
                <p>No se pudo cargar la lista de equipos.</p>
                <Button size="sm" onClick={catalog.retry}>Reintentar</Button>
              </div>
            ) : null}
            {notFound ? (
              <div role="status" className={css.unmatched}>
                <p>Este equipo no tiene correspondencia con la fuente real de partidos.</p>
                <Link href="/matches" onClick={() => setChosen(CLUB_ID)}>Ver los partidos reales del Real Jaén</Link>
              </div>
            ) : load.error ? (
              <div role="alert" className={css.error}>
                <p>No se pudieron cargar los partidos reales.</p>
                <Button size="sm" onClick={load.retry}>Reintentar</Button>
              </div>
            ) : teamId === ALL_TEAMS && catalog.error ? null : !load.data ? (
              <section aria-label="Resultados de partidos" aria-busy={load.loading || undefined} className={css.surface}>
                <LoadingState variant="section" label="Cargando partidos…" />
              </section>
            ) : (
              <section aria-label="Resultados de partidos" className={css.surface}>
                {all.length === 0 ? <p className={css.status} role="status">{teamId === ALL_TEAMS ? "La fuente no tiene partidos de estos equipos." : "La fuente no tiene partidos de este equipo."}</p>
                  : shown.length === 0 ? <p className={css.status} role="status">No hay partidos con estos filtros.</p>
                  // A match opened here returns to this team and status.
                  : <ObservedMatchList matches={shown} matchHref={(id) => observedMatchHref(id, { kind: "matches", teamId, state })} />}
              </section>
            )}
          </>
        )}
      </div>
    </>
  );
}
