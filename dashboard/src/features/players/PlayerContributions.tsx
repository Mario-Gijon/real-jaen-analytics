import type { GoalEvent, PlayerMatch, PlayerSeason } from "@/contract";
import Link from "next/link";
import { teamsHref } from "@/lib/navigation";
import { AssistIcon } from "../matches/AssistIcon";
import { GoalIcon } from "../matches/GoalIcon";
import { dateLabel, MISSING } from "@/lib/format";
import { PlayerSeasonValue } from "./PlayerSeasonValue";
import css from "./PlayerDetailView.module.css";

type Action = { event: GoalEvent; appearance: PlayerMatch };

/** Only the recorded goals involving this player; no extra match-detail requests. */
export function PlayerContributions({ data, matchHref }: { data: PlayerSeason; matchHref: (matchId: string) => string }) {
  const actions: Action[] = data.matches.flatMap(appearance => appearance.events
    .filter((event): event is GoalEvent => event.type === "goal")
    .map(event => ({ event, appearance })))
    .sort((a, b) => a.appearance.match.round - b.appearance.match.round
      || a.event.minute - b.event.minute || (a.event.addedTime ?? 0) - (b.event.addedTime ?? 0));
  const groups = [
    { kind: "goals", title: "Goles", stat: data.stats.goals,
      actions: actions.filter(({ event }) => event.player.id === data.player.id && !event.ownGoal) },
    { kind: "assists", title: "Asistencias", stat: data.stats.assists,
      actions: actions.filter(({ event }) => event.assist?.id === data.player.id) },
  ] as const;
  return <div className={css.contributions}>
    {groups.map(group => <section key={group.kind} aria-labelledby={`player-${group.kind}`}>
      <div className={css.contributionHeading}>
        <h3 id={`player-${group.kind}`}>{group.title}</h3>
        <span className={css.total}><PlayerSeasonValue stat={group.stat} /></span>
      </div>
      {group.kind === "assists" && group.actions.length ? <p className={css.actionNote}>El minuto corresponde al gol asistido.</p> : null}
      {group.actions.length ? <ul className={css.actions} aria-label={`Histórico de ${group.title.toLocaleLowerCase("es")}`}>
        {group.actions.map(({ event, appearance }) => <ActionRow key={event.id} event={event}
          appearance={appearance} assist={group.kind === "assists"} matchHref={matchHref} />)}
      </ul> : null}
    </section>)}
  </div>;
}

function ActionRow({ event, appearance, assist, matchHref }: Action & { assist: boolean; matchHref: (matchId: string) => string }) {
  const { match, side } = appearance;
  const opponent = side === "home" ? match.away : match.home;
  return <li className={css.action}>
    <span className={css.actionIcon}>{assist ? <AssistIcon /> : <GoalIcon />}</span>
    <div className={css.actionContext}>
      <span className={css.opponent}>J{match.round} · vs <Link href={teamsHref({ seasonId: match.seasonId, teamId: opponent.id })}>{opponent.name}</Link></span>
      <span className={css.actionMeta}>
        {side === "home" ? "Local" : "Visitante"} · {dateLabel(match.kickoff)}
        {!assist && event.penalty ? " · Penalti" : null}
      </span>
    </div>
    <span className={css.minute} aria-label={`${assist ? "Gol asistido" : "Gol"} en el minuto ${event.minute}${event.addedTime ? `+${event.addedTime}` : ""}`}>
      {event.minute}{event.addedTime ? `+${event.addedTime}` : ""}′
    </span>
    <Link className={css.score} href={matchHref(match.id)} aria-label={"Ver partido: " + match.home.name + " contra " + match.away.name}>
      {match.score ? `${match.score.home}–${match.score.away}` : MISSING}
    </Link>
  </li>;
}
