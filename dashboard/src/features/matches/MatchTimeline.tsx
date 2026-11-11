import { ArrowDown, ArrowUp } from "lucide-react";
import Link from "next/link";
import { scoringSide, type MatchDetail, type MatchEvent } from "@/contract";
import { teamsHref } from "@/lib/navigation";
import { GoalIcon } from "./GoalIcon";
import { PlayerLink } from "../players/PlayerLink";
import css from "./MatchTimeline.module.css";

const CARD_LABEL = { yellow: "Amarilla", second_yellow: "Segunda amarilla", red: "Roja" };

/** A sequence of recorded actions, not a chart with elapsed time to scale. */
export function MatchTimeline({ match }: { match: MatchDetail }) {
  if (match.status === "scheduled") return <p role="status" className={css.empty}>El partido todavía no se ha disputado.</p>;
  if (match.status === "postponed") return <p role="status" className={css.empty}>Partido aplazado. No hay cronología disponible.</p>;
  if (!match.events.length) return <p role="status" className={css.empty}>No se registraron eventos del partido.</p>;

  // Stable sort retains source order when minute and added time are identical.
  const events = [...match.events].sort((a, b) => a.minute - b.minute || (a.addedTime ?? 0) - (b.addedTime ?? 0));
  return <>
    <div className={css.legend} aria-label="Equipos del partido">
      <span><Link href={teamsHref({ seasonId: match.seasonId, teamId: match.home.id })}>{match.home.shortName}</Link> · Local</span>
      <span><Link href={teamsHref({ seasonId: match.seasonId, teamId: match.away.id })}>{match.away.shortName}</Link> · Visitante</span>
    </div>
    <ol className={css.timeline} aria-label="Eventos del partido">
      {events.map((event) => {
        const actorSide = event.teamId === match.home.id ? "home" : "away";
        const side = event.type === "goal" ? scoringSide(event, actorSide) : actorSide;
        const team = match[side];
        return <li className={css.row} key={event.id} data-side={side}
          aria-label={`${team.name} · ${side === "home" ? "Local" : "Visitante"}`}>
          <span className={css.minute}>{event.minute}{event.addedTime ? `+${event.addedTime}` : ""}′</span>
          <div className={css.action}>
            <div className={css.teamLabel}>
              <Link href={teamsHref({ seasonId: match.seasonId, teamId: team.id })}>{team.shortName}</Link>
              <span> · {side === "home" ? "Local" : "Visitante"}</span>
            </div>
            <Action event={event} ownGoalTeam={match[actorSide].shortName} seasonId={match.seasonId} matchId={match.id} />
          </div>
        </li>;
      })}
    </ol>
  </>;
}

function Action({ event, ownGoalTeam, seasonId, matchId }: { event: MatchEvent; ownGoalTeam: string; seasonId: string; matchId: string }) {
  const origin = { kind: "match", id: matchId, tab: "timeline" } as const;
  if (event.type === "goal") return <>
    <div className={css.primary}>
      <GoalIcon />
      <PlayerLink player={event.player} seasonId={seasonId} origin={origin} />
    </div>
    {event.ownGoal ? <p className={css.secondary}>Propia puerta · {ownGoalTeam}</p> : null}
    {event.penalty ? <p className={css.secondary}>Penalti</p> : null}
    {event.assist ? <p className={css.secondary}>Asist. <PlayerLink player={event.assist} seasonId={seasonId} origin={origin} /></p> : null}
  </>;
  if (event.type === "card") return <>
    <div className={css.primary}>
      <span className={css.cardIcon} data-card={event.card} aria-hidden="true">
        <span />{event.card === "second_yellow" ? <span /> : null}
      </span>
      <PlayerLink player={event.player} seasonId={seasonId} origin={origin} />
    </div>
    <p className={css.secondary}>{CARD_LABEL[event.card]}</p>
  </>;
  return <>
    <div className={css.primary}><ArrowUp size={16} aria-hidden="true" /><span>Entra · <PlayerLink player={event.playerIn} seasonId={seasonId} origin={origin} /></span></div>
    <div className={css.departure}><ArrowDown size={16} aria-hidden="true" /><span>Sale · <PlayerLink player={event.playerOut} seasonId={seasonId} origin={origin} /></span></div>
  </>;
}
