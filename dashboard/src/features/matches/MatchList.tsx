import Link from "next/link";
import type { MatchSummary, TeamRef } from "@/contract";
import { TeamCrest } from "@/ui/TeamCrest";
import { activateRowLink } from "@/ui/activate-row-link";
import { dateLabel } from "@/lib/format";
import { matchHref as buildMatchHref, teamsHref } from "@/lib/navigation";
import css from "./MatchList.module.css";

const STATUS = { finished: "Finalizado", scheduled: "Programado", postponed: "Aplazado" };
const timeFormat = new Intl.DateTimeFormat("es-ES", {
  hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid",
});

/** Dated matches are chronological; undated matches remain visible at the end, by round. */
export function MatchList({ matches, showRound, matchHref = buildMatchHref }: {
  matches: readonly MatchSummary[]; showRound: boolean; matchHref?: (matchId: string) => string;
}) {
  const ordered = [...matches].sort((a, b) => {
    if (a.kickoff === null && b.kickoff !== null) return 1;
    if (b.kickoff === null && a.kickoff !== null) return -1;
    return (a.kickoff ?? "").localeCompare(b.kickoff ?? "")
      || a.round - b.round || a.id.localeCompare(b.id);
  });
  return (
    <ul className={css.list} aria-label="Partidos de la temporada">
      {ordered.map((match) => {
        const central = match.status === "finished" && match.score
          ? `${match.score.home} – ${match.score.away}`
          : match.status === "postponed" ? "Aplazado"
            : match.kickoff ? timeFormat.format(new Date(match.kickoff)) : "Por definir";
        const date = match.kickoff ? dateLabel(match.kickoff) : "Fecha por definir";
        return (
          <li key={match.id}>
            <div className={css.match} onClick={event => activateRowLink(event.target, event.currentTarget, event)}>
              <div className={css.meta}>
                {showRound ? <span className={css.round}>Jornada {match.round}</span> : null}
                {match.kickoff ? <time dateTime={match.kickoff}>{date}</time> : <span>{date}</span>}
              </div>
              <Team team={match.home} side="home" seasonId={match.seasonId} />
              <div className={css.center}>
                <Link className={match.status === "postponed" ? css.postponed : css.result} data-row-primary=""
                  href={matchHref(match.id)} prefetch={false}
                  aria-label={`Ver partido ${match.home.name} contra ${match.away.name}, ${central}, jornada ${match.round}, ${date}${match.status === "postponed" ? "" : `, ${STATUS[match.status]}`}`}>
                  {central}
                </Link>
                {match.status !== "postponed" ? <span className={css.state}>{STATUS[match.status]}</span> : null}
              </div>
              <Team team={match.away} side="away" seasonId={match.seasonId} />
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Team({ team, side, seasonId }: { team: TeamRef; side: "home" | "away"; seasonId: string }) {
  return <div className={`${css.team} ${css[side]}`}>
    <TeamCrest team={team} size={40} />
    <Link className={css.teamName} href={teamsHref({ seasonId, teamId: team.id })} aria-label={`Ver equipo ${team.name}`}>
      <span className={css.name} data-club={team.id === "team-real-jaen" || undefined}>
        <span className={css.fullName}>{team.name}</span>
        <span className={css.shortName}>{team.shortName}</span>
      </span>
    </Link>
  </div>;
}
