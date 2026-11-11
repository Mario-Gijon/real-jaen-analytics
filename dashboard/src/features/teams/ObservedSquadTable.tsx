import type { ObservedSquad, ObservedSquadCompetition } from "@/contract";
import { playerProfileHref } from "../players/navigation";
import { TeamPlayersTable, type SquadRow } from "./TeamPlayersTable";
import css from "./ObservedPlayersList.module.css";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What the figures rest on and how to read them, briefly; only real uncertainties are named. */
function coverage({ matches, callUps }: ObservedSquadCompetition): string[] {
  const notes = matches.withCallUps < matches.counted
    ? [`Convocatorias en ${matches.withCallUps} de ${plural(matches.counted, "partido finalizado", "partidos finalizados")}.`]
    : [];
  const doubts = [
    callUps.mismatches ? `${plural(callUps.mismatches, "dato de goles o asistencias contradice", "datos de goles o asistencias contradicen")} las incidencias` : "",
    callUps.duplicateRecords ? `${plural(callUps.duplicateRecords, "registro duplicado se cuenta", "registros duplicados se cuentan")} una vez` : "",
    callUps.discardedRecords ? `${plural(callUps.discardedRecords, "registro incoherente se descarta", "registros incoherentes se descartan")}` : "",
  ].filter(Boolean);
  if (doubts.length) notes.push(`${doubts.join("; ")}.`.replace(/^./, (c) => c.toUpperCase()));
  return notes;
}

/**
 * Observed players of a real team with their figures in the competition the
 * real season selector chose (`competitionId`, one of the source's), in the
 * original squad table. Personal data come with the figures; each name opens
 * the real profile and returns here.
 */
export function ObservedSquadTable({ squad, competitionId }: { squad: ObservedSquad; competitionId: string }) {
  const competition = squad.competitions.find((c) => c.id === competitionId);
  if (!competition) {
    return <p className={css.empty} role="status">La fuente real no tiene jugadores observados de este equipo en esta competición.</p>;
  }
  const rows: SquadRow[] = competition.players.map((p) => ({
    player: p.player,
    shirtNumber: p.lastShirtNumber,
    stats: p.stats,
    personal: { heightCm: p.heightCm, preferredFoot: p.preferredFoot },
  }));
  return (
    <div className={css.observed}>
      {coverage(competition).map((note) => <p className={css.note} key={note}>{note}</p>)}
      {rows.length === 0 ? (
        <p className={css.empty}>La fuente no tiene convocatorias de este equipo en esta competición.</p>
      ) : (
        <TeamPlayersTable
          key={`${squad.teamId}|${competition.id}`}
          observed={{ href: (playerId) => playerProfileHref(playerId, competition.id, { kind: "team", id: squad.teamId }) }}
          seasonId={competition.id}
          squad={rows}
          teamId={squad.teamId}
        />
      )}
    </div>
  );
}
