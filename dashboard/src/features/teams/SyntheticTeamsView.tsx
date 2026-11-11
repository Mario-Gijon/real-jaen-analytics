"use client";

import { useMemo, useState } from "react";
import { getCompetitions, getPlayer, getSeasonTeams, getTeamSeason } from "@/client/api";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/PageHeader";
import { Select } from "@/ui/Select";
import { LoadingState } from "@/ui/LoadingState";
import { CLUB_ID } from "@/lib/club";
import { footLabel } from "@/lib/format";
import { useApi } from "@/hooks/use-api";
import { TeamPerformance } from "./TeamPerformance";
import { TeamPlayersTable } from "./TeamPlayersTable";
import { gridOrder, TeamShell, useTeamsUrl, type Props, type Tab } from "./TeamShell";
import css from "./TeamsView.module.css";

/**
 * One club's whole season from the synthetic source. Competition and season
 * pick the teams, a crest grid picks the club and two tabs show its
 * collective performance and its squad.
 */
export function SyntheticTeamsView({ initialTeamId, initialSeasonId, initialTab = "performance" }: Props) {
  const competitions = useApi("competitions", (signal) => getCompetitions({ signal }));
  const [competitionId, setCompetitionId] = useState<string | null>(null);
  const [seasonId, setSeasonId] = useState<string | null>(initialSeasonId ?? null);
  const competition =
    competitions.data?.find((c) => c.id === competitionId) ?? competitions.data?.find(c => c.seasons.some(s => s.id === seasonId)) ?? competitions.data?.[0] ?? null;
  const season =
    competition?.seasons.find((s) => s.id === seasonId) ?? competition?.seasons[0] ?? null;

  const standings = useApi(season && `teams|${season.id}`, (signal) =>
    getSeasonTeams(season!.id, { signal }),
  );
  const teams = useMemo(
    () => gridOrder(standings.data?.map((row) => row.team) ?? []),
    [standings.data],
  );
  const [teamId, setTeamId] = useState<string | null>(initialTeamId ?? null);
  const team =
    teams.find((t) => t.id === teamId) ??
    teams.find((t) => t.id === CLUB_ID) ??
    teams[0] ??
    null;

  const detail = useApi(season && team && `team|${season.id}|${team.id}`, (signal) =>
    getTeamSeason(season!.id, team!.id, { signal }),
  );
  const [tab, setTab] = useState<Tab>(initialTab);

  const seasonLabel = [competition?.shortName, season?.name].filter(Boolean).join(" · ");
  useTeamsUrl(season && team ? { seasonId: seasonId ?? (competitionId ? season.id : null), teamId, tab } : null);

  return (
    <>
      <PageHeader
        actions={
          <div className={css.scope}>
            <label className={css.field}>
              <span>Competición</span>
              <Select
                disabled={!competitions.data}
                onChange={(event) => {
                  setCompetitionId(event.target.value);
                  setSeasonId(null);
                }}
                value={competition?.id ?? ""}
              >
                {competitions.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </label>
            <label className={css.field}>
              <span>Temporada</span>
              <Select
                disabled={!competition}
                onChange={(event) => setSeasonId(event.target.value)}
                value={season?.id ?? ""}
              >
                {competition?.seasons.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </label>
          </div>
        }
      />

      {competitions.error || standings.error ? (
        <div className={css.error} role="alert">
          <p>No se pudieron cargar los equipos.</p>
          <Button
            onClick={() => (competitions.error ? competitions.retry() : standings.retry())}
            size="sm"
          >
            Reintentar
          </Button>
        </div>
      ) : !team ? (
        competitions.loading || standings.loading
          ? <section aria-label="Equipos de la temporada" aria-busy="true"><LoadingState variant="section" label="Cargando equipos…" /></section>
          : <p className={css.status} role="status">No hay equipos en esta temporada.</p>
      ) : (
        <TeamShell
          busy={detail.loading}
          onSelectTeam={setTeamId}
          onTab={setTab}
          subtitle={seasonLabel}
          tab={tab}
          team={team}
          teams={teams}
        >
          {detail.error ? (
            <div className={css.error} role="alert">
              <p>No se pudo cargar la temporada del equipo.</p>
              <Button onClick={detail.retry} size="sm">
                Reintentar
              </Button>
            </div>
          ) : !detail.data ? (
            <LoadingState variant="section" label="Cargando temporada…" />
          ) : tab === "performance" ? (
            <TeamPerformance season={detail.data} />
          ) : (
            <TeamPlayersTable key={`${detail.data.team.id}|${detail.data.season.id}`} squad={detail.data.squad} seasonId={detail.data.season.id} teamId={detail.data.team.id}
              personalCells={(playerId) => <PersonalCells playerId={playerId} seasonId={detail.data!.season.id} />} />
          )}
        </TeamShell>
      )}

    </>
  );
}

/** Height and foot of a squad member, from the demonstration's player season. */
function PersonalCells({ playerId, seasonId }: { playerId: string; seasonId: string }) {
  const profile = useApi(`personal|${playerId}|${seasonId}`, signal => getPlayer(playerId, seasonId, { signal }));
  const player = profile.data?.player;
  const note = profile.error ? "No se pudieron cargar los datos personales" : profile.loading ? "Cargando datos personales" : undefined;
  return <>
    <td aria-busy={profile.loading || undefined} title={note}>{profile.loading ? "…" : player?.heightCm != null ? `${player.heightCm} cm` : "—"}</td>
    <td aria-busy={profile.loading || undefined} title={note}>{profile.loading ? "…" : footLabel(player?.preferredFoot ?? null)}</td>
  </>;
}
