import { ArrowLeftRight, ArrowUp } from "lucide-react";
import Link from "next/link";
import type { ObservedGoal, ObservedLineupPlayer, ObservedMatch, ObservedMatchDetail, ObservedSubstitution } from "@/contract";
import { Button } from "@/ui/Button";
import { MISSING, POSITION_SHORT, formatNumber, positionLabel } from "@/lib/format";
import { playerProfileHref } from "../players/navigation";
import { teamsHref } from "@/lib/navigation";
import type { ObservedMatchListOrigin } from "@/lib/navigation";
import linkCss from "../players/PlayerLink.module.css";
import { AssistIcon } from "./AssistIcon";
import { GoalIcon } from "./GoalIcon";
import css from "./MatchLineups.module.css";

/** A side reads as complete only with eleven starters and every role given. */
const STARTERS = 11;

type Incidents = ObservedMatchDetail["incidents"];
/** What the recorded incidents say about one player; nothing comes from minutes or stats. */
type Marks = { goals: ObservedGoal[]; ownGoals: number; assists: number; out?: ObservedSubstitution; in?: ObservedSubstitution };

const minuteLabel = ({ minute, addedTime }: { minute: number | null; addedTime: number | null }) =>
  minute === null ? null : `${minute}${addedTime ? `+${addedTime}` : ""}′`;

/** Marks per player id, from goals (scorer, named assistant) and substitutions only. */
function marksFrom(incidents: Incidents): Map<string, Marks> {
  const marks = new Map<string, Marks>();
  if (incidents.state !== "stored") return marks;
  const of = (id: string) => {
    const existing = marks.get(id);
    if (existing) return existing;
    const created: Marks = { goals: [], ownGoals: 0, assists: 0 };
    marks.set(id, created);
    return created;
  };
  for (const goal of incidents.goals ?? []) {
    if (goal.scorer) {
      if (goal.kind === "ownGoal") of(goal.scorer.id).ownGoals++;
      else of(goal.scorer.id).goals.push(goal);
    }
    if (goal.assist && goal.kind !== "ownGoal") of(goal.assist.id).assists++;
  }
  for (const change of incidents.substitutions) {
    if (change.playerOut) of(change.playerOut.id).out = change;
    if (change.playerIn) of(change.playerIn.id).in = change;
  }
  return marks;
}

/**
 * Real line-ups in the format of the synthetic detail: per team, «Once
 * inicial» and «Banquillo» (by the source's explicit flag), shirt number,
 * name and position, and the goals, assists and substitutions the source's
 * incidents record. A player whose role the source does not give is listed
 * apart. No formation or pitch. Without incidents nothing is marked, and an
 * unused substitute is never taken for one who did not play.
 */
export function ObservedLineups({ match, confirmed, home, away, linkProfiles, incidents, retry, matchesOrigin }: {
  match: ObservedMatch;
  matchesOrigin?: ObservedMatchListOrigin;
  confirmed: boolean | null;
  home: readonly ObservedLineupPlayer[];
  away: readonly ObservedLineupPlayer[];
  /** Real profiles can be opened (their source is enabled). */
  linkProfiles: boolean;
  incidents: Incidents;
  retry: () => void;
}) {
  const marks = marksFrom(incidents);
  return <>
    {/* Confirmed line-ups need no note; only doubtful ones are said so. */}
    {confirmed === true ? null : <p className={css.status}>
      {confirmed === false ? "Alineaciones no confirmadas por la fuente." : "La fuente no indica si las alineaciones están confirmadas."}
    </p>}
    {incidents.state === "error" ? <div role="alert" className={css.incidentsNote}>
      <p>No se pudieron cargar las incidencias: no se marcan goles, asistencias ni cambios.</p>
      <Button size="sm" onClick={retry}>Reintentar</Button>
    </div>
      : incidents.state !== "stored" ? <p className={css.incidentsNote} role="status">
        {incidents.state === "empty" ? "La fuente guardó las incidencias de este partido sin contenido"
          : incidents.state === "outdated" ? "Las incidencias guardadas son de los equipos anteriores del partido"
          : "La fuente no tiene incidencias guardadas de este partido"}: no se marcan goles, asistencias ni cambios.
      </p>
      : incidents.goals === null ? <p className={css.incidentsNote} role="status">
        Las incidencias son anteriores a la corrección del marcador: solo se marcan los cambios, no los goles ni las asistencias.
      </p> : null}
    <div className={css.teams}>
      {([["home", home], ["away", away]] as const).map(([side, players]) => {
        const name = match[side].name ?? "Equipo sin identificar";
        const starters = players.filter((p) => p.role === "starter");
        const substitutes = players.filter((p) => p.role === "substitute");
        const unassigned = players.filter((p) => p.role === null);
        const complete = starters.length === STARTERS && unassigned.length === 0;
        const list = (items: readonly ObservedLineupPlayer[], label: string) =>
          <PlayerList players={items} label={`${label} de ${name}`} matchId={match.id} linkProfiles={linkProfiles} matchesOrigin={matchesOrigin} marks={marks} />;
        return <section key={side} aria-labelledby={`real-lineup-${side}`} className={css.team}>
          <header className={css.identity}>
            <div className={css.teamTitle}>
              <h3 id={`real-lineup-${side}`}><Link href={teamsHref({ teamId: match[side].id })}>{name}</Link></h3>
              <span className={css.side}>· {side === "home" ? "Local" : "Visitante"}</span>
            </div>
          </header>
          {players.length === 0 ? <p className={css.warning} role="status">La fuente no registra jugadores de este equipo.</p>
            : !complete ? <p className={css.warning} role="status">
              Alineación incompleta en la fuente: {starters.length} titulares{unassigned.length ? ` y ${unassigned.length} sin rol informado` : ""}.
            </p> : null}
          {players.length ? <>
            <h4 className={css.sectionTitle}>Once inicial</h4>
            {starters.length ? list(starters, "Once inicial") : <p className={css.empty}>Sin titulares en la fuente.</p>}
            <h4 className={css.sectionTitle}>Banquillo</h4>
            {substitutes.length ? list(substitutes, "Banquillo") : <p className={css.empty}>Sin suplentes en la fuente.</p>}
            {unassigned.length ? <>
              <h4 className={css.sectionTitle}>Sin rol informado</h4>
              {list(unassigned, "Jugadores sin rol informado")}
            </> : null}
          </> : null}
        </section>;
      })}
    </div>
  </>;
}

function PlayerList({ players, label, matchId, linkProfiles, marks, matchesOrigin }: {
  matchesOrigin?: ObservedMatchListOrigin;
  players: readonly ObservedLineupPlayer[]; label: string; matchId: string; linkProfiles: boolean; marks: ReadonlyMap<string, Marks>;
}) {
  return <ul className={css.players} aria-label={label}>
    {players.map(({ player, shirtNumber, role, participation, minutes }) => {
      const mark = marks.get(player.id);
      const entered = role !== "starter" ? mark?.in : undefined;
      return <li key={player.id} className={css.player}>
        <span className={css.number} aria-label={shirtNumber === null ? "Dorsal no disponible" : `Dorsal ${shirtNumber}`}>
          {shirtNumber ?? MISSING}
        </span>
        <span className={css.playerIdentity}>
          {linkProfiles
            ? <Link className={`${linkCss.link} ${css.name}`} prefetch={false}
              href={playerProfileHref(player.id, null, { kind: "observedMatch", id: matchId, tab: "lineups", matches: matchesOrigin })}>{player.name}</Link>
            : <span className={css.name}>{player.name}</span>}
          <span className={css.position} title={player.position ? positionLabel(player.position) : "Posición no disponible"}
            aria-label={player.position ? positionLabel(player.position) : "Posición no disponible"}>
            {player.position ? POSITION_SHORT[player.position] : MISSING}
          </span>
        </span>
        <span className={css.changes}>
          <Contributions mark={mark} substituted={role === "starter" ? mark?.out : undefined} />
          {role === "substitute" && !entered
            ? participation === "confirmed" && minutes.value !== null
              ? <span className={css.minutes} aria-label={`${formatNumber(minutes.value)} minutos`}>{formatNumber(minutes.value)}′</span>
              : participation === "unused" ? null : <span className={css.unknown}>Participación no confirmada</span>
            : null}
        </span>
        {entered ? <span className={css.entry}>
          <span className={css.change} aria-label={minuteLabel(entered) ? `Entra en el minuto ${minuteLabel(entered)}` : "Entra, minuto no disponible"}>
            <ArrowUp size={14} aria-hidden="true" /><span>{minuteLabel(entered) ?? MISSING}</span>
          </span>
          <span className={css.replaced}>Sale · {entered.playerOut
            ? linkProfiles ? <Link className={`${linkCss.link} ${css.name}`} prefetch={false}
              href={playerProfileHref(entered.playerOut.id, null, { kind: "observedMatch", id: matchId, tab: "lineups", matches: matchesOrigin })}>{entered.playerOut.name}</Link>
              : entered.playerOut.name
            : "jugador no indicado"}</span>
        </span> : null}
      </li>;
    })}
  </ul>;
}

/** «2 goles (1 de penalti)»: regular and penalty goals only; a penalty is a goal, told apart. */
function goalLabel(goals: readonly ObservedGoal[]) {
  const penalties = goals.filter((g) => g.kind === "penalty").length;
  return `${goals.length} ${goals.length === 1 ? "gol" : "goles"}${penalties ? ` (${penalties} de penalti)` : ""}`;
}

const unknownLabel = (count: number) => `${count} ${count === 1 ? "gol" : "goles"} de tipo no indicado por la fuente`;

function Contributions({ mark, substituted }: { mark: Marks | undefined; substituted: ObservedSubstitution | undefined }) {
  if (!mark?.goals.length && !mark?.ownGoals && !mark?.assists && !substituted) return null;
  // A goal of a kind the app does not know is never counted with the ordinary ones: it gets its own «?» mark.
  const goals = (mark?.goals ?? []).filter((g) => g.kind !== "unknown");
  const unknown = (mark?.goals ?? []).length - goals.length;
  const ownGoals = mark?.ownGoals ?? 0;
  const assists = mark?.assists ?? 0;
  const out = substituted ? minuteLabel(substituted) : null;
  return <span className={css.contributions}>
    {goals.length ? <span className={css.contribution} title={goalLabel(goals)}>
      <GoalIcon label={goalLabel(goals)} />
      {goals.length > 1 ? <span aria-hidden="true">{goals.length}</span> : null}
    </span> : null}
    {unknown ? <span className={css.contribution} title={unknownLabel(unknown)}>
      <GoalIcon label={unknownLabel(unknown)} />
      <span aria-hidden="true">?{unknown > 1 ? ` ${unknown}` : ""}</span>
    </span> : null}
    {ownGoals ? <span className={css.contribution} title={`${ownGoals} ${ownGoals === 1 ? "gol" : "goles"} en propia puerta`}
      aria-label={`${ownGoals} ${ownGoals === 1 ? "gol" : "goles"} en propia puerta`}>
      <span aria-hidden="true">p.p.{ownGoals > 1 ? ` ${ownGoals}` : ""}</span>
    </span> : null}
    {assists ? <span className={css.contribution} title={`${assists} ${assists === 1 ? "asistencia" : "asistencias"}`}>
      <AssistIcon label={`${assists} ${assists === 1 ? "asistencia" : "asistencias"}`} />
      <span aria-hidden="true">{assists}</span>
    </span> : null}
    {substituted ? <span className={css.contribution} title={out ? `Sustituido en el minuto ${out}` : "Sustituido"}>
      <ArrowLeftRight size={16} role="img" aria-label={out ? `Sustituido en el minuto ${out}` : "Sustituido"} />
      {out ? <span aria-hidden="true">{out}</span> : null}
    </span> : null}
  </span>;
}
