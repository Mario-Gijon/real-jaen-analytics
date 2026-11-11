"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { getCompetitions, getSeasonMatches, getSeasonTeams } from "@/client/api";
import type { Season } from "@/contract";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/PageHeader";
import { Select } from "@/ui/Select";
import { LoadingState } from "@/ui/LoadingState";
import type { MatchesState } from "@/lib/navigation";
import { useApi } from "@/hooks/use-api";
import { MatchList } from "./MatchList";
import css from "./MatchesView.module.css";

/** What the «Partidos» URL asks for. */
export type Props = { initialSeasonId?: string; initialTeamId?: string; initialState?: MatchesState };

/** The demonstration's season, round by round, with its competition, season, round and team filters. */
export function SyntheticMatchesView({ initialSeasonId, initialTeamId }: Props) {
  const competitions = useApi("competitions", (signal) => getCompetitions({ signal }));
  const [competitionId, setCompetitionId] = useState<string | null>(null);
  const [seasonId, setSeasonId] = useState<string | null>(initialSeasonId ?? null);
  const competition = competitions.data?.find((c) => c.id === competitionId)
    ?? competitions.data?.find(c => c.seasons.some(s => s.id === seasonId)) ?? competitions.data?.[0] ?? null;
  const season = competition?.seasons.find((s) => s.id === seasonId)
    ?? competition?.seasons[0] ?? null;

  return (
    <>
      <PageHeader
        actions={
          <div className={css.scope}>
            <label className={`${css.field} ${css.competition}`}>
              <span>Competición</span>
              <Select
                disabled={!competitions.data?.length}
                value={competition?.id ?? ""}
                onChange={(event) => {
                  setCompetitionId(event.target.value);
                  setSeasonId(null);
                }}
              >
                {competitions.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </label>
            <label className={`${css.field} ${css.season}`}>
              <span>Temporada</span>
              <Select
                disabled={!competition?.seasons.length}
                value={season?.id ?? ""}
                onChange={(event) => setSeasonId(event.target.value)}
              >
                {competition?.seasons.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
              </Select>
            </label>
          </div>
        }
      />
      {competitions.error ? (
        <LoadError message="No se pudieron cargar las competiciones." retry={competitions.retry} />
      ) : season ? (
        <SeasonMatches key={season.id} season={season} initialTeamId={season.id === initialSeasonId ? initialTeamId : undefined} />
      ) : competitions.loading ? (
        <section aria-label="Competiciones" aria-busy="true"><LoadingState variant="section" label="Cargando competiciones…" /></section>
      ) : (
        <p className={css.status} role="status">No hay temporadas disponibles.</p>
      )}
    </>
  );
}

/** Each season starts with its latest played round; without one, use the first round. */
function SeasonMatches({ season, initialTeamId }: { season: Season; initialTeamId?: string }) {
  const [round, setRound] = useState<number | "all">(initialTeamId ? "all" : season.currentRound ?? (season.rounds ? 1 : "all"));
  const [teamId, setTeamId] = useState(initialTeamId ?? "all");
  const standings = useApi(`teams|${season.id}`, (signal) => getSeasonTeams(season.id, { signal }));
  const teams = useMemo(() =>
    [...(standings.data ?? [])].sort((a, b) => a.team.name.localeCompare(b.team.name, "es")),
  [standings.data]);
  const matches = useApi(`matches|${season.id}|${round}|${teamId}`, (signal) =>
    getSeasonMatches(season.id, {
      round: round === "all" ? undefined : round,
      teamId: teamId === "all" ? undefined : teamId,
    }, { signal }),
  );

  return (
    <div className={css.explorer}>
      <div className={css.filters}>
        <div className={css.roundPicker}>
          <label className={css.field}>
            <span>Jornada</span>
            <Select value={round} onChange={(event) =>
              setRound(event.target.value === "all" ? "all" : Number(event.target.value))
            }>
              <option value="all">Todas las jornadas</option>
              {Array.from({ length: season.rounds }, (_, i) =>
                <option key={i + 1} value={i + 1}>Jornada {i + 1}</option>
              )}
            </Select>
          </label>
          {round !== "all" ? (
            <div className={css.roundActions}>
              <Button aria-label="Jornada anterior" disabled={round <= 1}
                onClick={() => setRound(round - 1)} icon={<ChevronLeft aria-hidden="true" size={18} />} />
              <Button aria-label="Jornada siguiente" disabled={round >= season.rounds}
                onClick={() => setRound(round + 1)} icon={<ChevronRight aria-hidden="true" size={18} />} />
            </div>
          ) : null}
        </div>
        <label className={`${css.field} ${css.teamPicker}`}>
          <span>Equipo</span>
          <Select disabled={!standings.data} value={teamId}
            onChange={(event) => setTeamId(event.target.value)}>
            <option value="all">Todos los equipos</option>
            {teams.map(({ team }) => <option key={team.id} value={team.id}>{team.name}</option>)}
          </Select>
        </label>
      </div>
      {standings.error ? (
        <LoadError message="No se pudieron cargar los equipos del filtro." retry={standings.retry} />
      ) : null}
      <section aria-label="Resultados de partidos" aria-busy={matches.loading || undefined} className={css.surface}>
        {matches.error ? (
          <LoadError message="No se pudieron cargar los partidos." retry={matches.retry} />
        ) : matches.data ? (
          matches.data.length ? <MatchList matches={matches.data} showRound={round === "all"} />
            : <p className={css.status} role="status">No hay partidos con estos filtros.</p>
        ) : <LoadingState variant="section" label="Cargando partidos…" />}
      </section>
    </div>
  );
}

export function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return <div role="alert" className={css.error}>
    <p>{message}</p><Button size="sm" onClick={retry}>Reintentar</Button>
  </div>;
}
