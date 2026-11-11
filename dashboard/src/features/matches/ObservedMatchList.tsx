import Link from "next/link";
import type { MatchStatus, ObservedMatch, ObservedTeam } from "@/contract";
import { TeamCrest } from "@/ui/TeamCrest";
import { activateRowLink } from "@/ui/activate-row-link";
import { CLUB_ID } from "@/lib/club";
import { observedMatchHref, teamsHref } from "@/lib/navigation";
import { dateLabel } from "@/lib/format";
import { observedCrest } from "@/lib/team-crests";
import css from "./MatchList.module.css";

const STATUS: Record<MatchStatus, string> = { finished: "Finalizado", scheduled: "Programado", postponed: "Aplazado" };
const timeFormat = new Intl.DateTimeFormat("es-ES", {
  hour: "2-digit", minute: "2-digit", timeZone: "Europe/Madrid",
});

/**
 * Real matches as the source lists them, in the order given (by kickoff).
 * Same layout as the synthetic list; each row opens the real match detail,
 * with its origin preserved in the query. Only a
 * finished match shows a score; a pending one shows its time or «Por
 * definir», never 0–0.
 */
/** `matchHref` builds each row's link (e.g. with the list's team and status to return to). */
export function ObservedMatchList({ matches, matchHref = (matchId) => observedMatchHref(matchId) }: {
  matches: readonly ObservedMatch[]; matchHref?: (matchId: string) => string;
}) {
  return (
    <ul className={css.list} aria-label="Partidos reales">
      {matches.map((match) => {
        const central = match.status === "finished"
          ? match.score ? `${match.score.home} – ${match.score.away}` : "—"
          : match.status === "postponed" ? "Aplazado"
            : match.status === "scheduled" && match.kickoff ? timeFormat.format(new Date(match.kickoff))
              : match.status === "scheduled" ? "Por definir" : "—";
        const state = match.status === null ? "Estado desconocido"
          : match.status === "finished" && !match.score ? "Finalizado · sin resultado"
            : match.status === "postponed" ? null : STATUS[match.status];
        const date = match.kickoff ? dateLabel(match.kickoff) : "Fecha por definir";
        const seasonRound = [match.season, match.round !== null ? `J${match.round}` : null].filter(Boolean).join(" · ");
        return (
          <li key={match.id}>
            <div className={css.match} data-observed="" onClick={event => activateRowLink(event.target, event.currentTarget, event)}>
              <div className={css.meta}>
                {/* The competition stays in the data; while every match is of the same one, it is not repeated per row. */}
                {seasonRound ? <span className={css.round}>{seasonRound}</span> : null}
                {match.kickoff ? <time dateTime={match.kickoff}>{date}</time> : <span>{date}</span>}
              </div>
              <Team team={match.home} side="home" />
              <div className={css.center}>
                <Link className={match.status === "finished" && match.score ? css.result : css.postponed} data-row-primary=""
                  href={matchHref(match.id)} prefetch={false}
                  aria-label={`Ver partido ${match.home.name ?? "Equipo sin identificar"} contra ${match.away.name ?? "Equipo sin identificar"}, ${central}, ${[match.season, match.round !== null ? `jornada ${match.round}` : null].filter(Boolean).join(" ")}, ${date}${state ? `, ${state}` : ""}`}>
                  {central}
                </Link>
                {state ? <span className={css.state}>{state}</span> : null}
              </div>
              <Team team={match.away} side="away" />
            </div>
          </li>
        );
      })}
    </ul>
  );
}


function Team({ team, side }: { team: ObservedTeam; side: "home" | "away" }) {
  const name = team.name ?? "Equipo sin identificar";
  const club = team.id === CLUB_ID;
  return <div className={`${css.team} ${css[side]}`}>
    <TeamCrest team={observedCrest(team)} size={40} />
    <Link className={css.teamName} href={teamsHref({ teamId: team.id })} aria-label={`Ver equipo ${name}`}>
      <span className={css.name} data-club={club || undefined}>
        <span className={css.fullName}>{name}</span>
        <span className={css.shortName}>{name}</span>
      </span>
    </Link>
  </div>;
}
