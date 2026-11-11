import { ArrowLeftRight, ArrowUp } from "lucide-react";
import Link from "next/link";
import type { LineupPlayer, MatchDetail, SubstitutionEvent } from "@/contract";
import { MISSING, POSITION_SHORT, positionLabel } from "@/lib/format";
import { teamsHref } from "@/lib/navigation";
import { GoalIcon } from "./GoalIcon";
import { AssistIcon } from "./AssistIcon";
import { PlayerLink } from "../players/PlayerLink";
import css from "./MatchLineups.module.css";

type Contribution = { goals: number; assists: number };

/** Lists the announced players as supplied, without using tactical slots. */
export function MatchLineups({ match }: { match: MatchDetail }) {
  if (!match.lineups) return <p role="status" className={css.empty}>
    {match.status === "scheduled" ? "Las alineaciones todavía no están disponibles."
      : match.status === "postponed" ? "Partido aplazado. Las alineaciones no están disponibles."
        : "No hay alineaciones disponibles para este partido."}
  </p>;

  const substitutions = match.events.filter((e): e is SubstitutionEvent => e.type === "substitution")
    .sort((a, b) => a.minute - b.minute || (a.addedTime ?? 0) - (b.addedTime ?? 0));
  const contributions = new Map<string, Contribution>();
  for (const event of match.events) {
    if (event.type !== "goal" || event.ownGoal) continue;
    const scorer = contributions.get(event.player.id) ?? { goals: 0, assists: 0 };
    scorer.goals++;
    contributions.set(event.player.id, scorer);
    if (event.assist) {
      const assistant = contributions.get(event.assist.id) ?? { goals: 0, assists: 0 };
      assistant.assists++;
      contributions.set(event.assist.id, assistant);
    }
  }
  return <div className={css.teams}>
    {(["home", "away"] as const).map((side) => {
      const team = match[side];
      const lineup = match.lineups![side];
      const changes = substitutions.filter(e => e.teamId === team.id);
      return <section key={side} aria-labelledby={`lineup-${side}-team`} className={css.team}>
        <header className={css.identity}>
          <div className={css.teamTitle}>
            <h2 id={`lineup-${side}-team`}><Link href={teamsHref({ seasonId: match.seasonId, teamId: team.id })}>{team.name}</Link></h2>
            <span className={css.side}>· {side === "home" ? "Local" : "Visitante"}</span>
          </div>
          {lineup.formation ? <p className={css.formation}>Formación: {lineup.formation}</p> : null}
        </header>
        <h3 className={css.sectionTitle}>Once inicial</h3>
        <PlayerList matchId={match.id} seasonId={match.seasonId} players={lineup.starters} changes={[]} contributions={contributions}
          substitutedPlayers={new Set(changes.map(event => event.playerOut.id))} label={`Once inicial de ${team.name}`} />
        <h3 className={css.sectionTitle}>Banquillo</h3>
        <PlayerList matchId={match.id} seasonId={match.seasonId} players={lineup.bench} changes={changes} contributions={contributions} label={`Banquillo de ${team.name}`} />
      </section>;
    })}
  </div>;
}

function PlayerList({ players, changes, contributions, substitutedPlayers, label, seasonId, matchId }: {
  players: readonly LineupPlayer[]; changes: readonly SubstitutionEvent[];
  contributions: ReadonlyMap<string, Contribution>; substitutedPlayers?: ReadonlySet<string>; label: string; seasonId: string; matchId: string;
}) {
  return <ul className={css.players} aria-label={label}>
    {players.map(({ player, shirtNumber }) => <li key={player.id} className={css.player}>
      <span className={css.number} aria-label={shirtNumber === null ? "Dorsal no disponible" : `Dorsal ${shirtNumber}`}>
        {shirtNumber ?? MISSING}
      </span>
      <span className={css.playerIdentity}>
        <PlayerLink player={player} seasonId={seasonId} className={css.name} origin={{ kind: "match", id: matchId, tab: "lineups" }} />
        <span className={css.position} title={player.position ? positionLabel(player.position) : "Posición no disponible"}
          aria-label={player.position ? positionLabel(player.position) : "Posición no disponible"}>
          {player.position ? POSITION_SHORT[player.position] : MISSING}
        </span>
      </span>
      <span className={css.changes}>
        <Contributions contribution={contributions.get(player.id)} substituted={substitutedPlayers?.has(player.id)} />
      </span>
      {changes.filter(e => e.playerIn.id === player.id).map((event) => {
        const minute = `${event.minute}${event.addedTime ? `+${event.addedTime}` : ""}′`;
        return <span key={event.id} className={css.entry}>
          <span className={css.change} aria-label={`Entra en el minuto ${minute}`}>
            <ArrowUp size={14} aria-hidden="true" /><span>{minute}</span>
          </span>
          <span className={css.replaced}>Sale · <PlayerLink player={event.playerOut} seasonId={seasonId} className={css.name}
            origin={{ kind: "match", id: matchId, tab: "lineups" }} /></span>
        </span>;
      })}
    </li>)}
  </ul>;
}

function Contributions({ contribution, substituted = false }: {
  contribution: Contribution | undefined; substituted?: boolean;
}) {
  if (!contribution && !substituted) return null;
  const { goals, assists } = contribution ?? { goals: 0, assists: 0 };
  return <span className={css.contributions}>
    {goals > 0 ? <span className={css.contribution} title={`${goals} ${goals === 1 ? "gol" : "goles"}`}>
      <GoalIcon label={`${goals} ${goals === 1 ? "gol" : "goles"}`} />
      {goals > 1 ? <span aria-hidden="true">{goals}</span> : null}
    </span> : null}
    {assists > 0 ? <span className={css.contribution} title={`${assists} ${assists === 1 ? "asistencia" : "asistencias"}`}>
      <AssistIcon label={`${assists} ${assists === 1 ? "asistencia" : "asistencias"}`} />
      <span aria-hidden="true">{assists}</span>
    </span> : null}
    {substituted ? <span className={css.contribution} title="Sustituido">
      <ArrowLeftRight size={16} role="img" aria-label="Sustituido" />
    </span> : null}
  </span>;
}
