"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { Score, SeasonStat, TeamRef } from "@/contract";
import { Button } from "@/ui/Button";
import { TeamCrest } from "@/ui/TeamCrest";
import { activateRowLink } from "@/ui/activate-row-link";
import { dateLabel, formatNumber } from "@/lib/format";
import { CLUB_ID } from "@/lib/club";
import sections from "../teams/TeamPerformance.module.css";
import css from "./HomeView.module.css";

/**
 * The parts «Inicio» draws in both modes, from neutral display rows: the
 * synthetic and the real view each build them from their own data and never
 * from the other's.
 */

/** A team as drawn here: its crest (or neutral shield) and names. */
export type CrestTeam = Pick<TeamRef, "id" | "name" | "shortName" | "crestUrl" | "colors"> & { href: string };
/** A dated club match the target can be; `round` may be unknown in a real source. */
export type Fixture = { id: string; round: number | null; kickoff: string; home: CrestTeam; away: CrestTeam };
/** One of the rival's previous matches; `note` says why it has no result when it has none. */
export type HistoryRow = Fixture & { href: string; score: Score | null; note?: string };

const roundLabel = (round: number | null) => round === null ? null : `Jornada ${round}`;
/** «Jornada N · fecha», without the round when it is unknown. */
export const fixtureLabel = (fixture: Pick<Fixture, "round" | "kickoff">) =>
  [roundLabel(fixture.round), dateLabel(fixture.kickoff)].filter(Boolean).join(" · ");
const opponentOf = (fixture: Fixture) => fixture.home.id === CLUB_ID ? fixture.away : fixture.home;

/**
 * The encounter being prepared and the selector of the next ones. `choices`
 * are the selectable fixtures; `empty` is said when there is no target.
 * `notes` follow the encounter (e.g. what is not proposed and why).
 */
export function TargetSection({ context, target, choices, onChoose, days, analyze, empty, notes }: {
  context: string; target: Fixture | null; choices: readonly Fixture[]; onChoose: (fixture: Fixture) => void;
  days: number | null; analyze: { href: string; label: string } | null;
  empty: { text: string; alert: boolean }; notes?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const selectorId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const options = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (open) (options.current?.querySelector<HTMLButtonElement>('[aria-pressed="true"]')
      ?? options.current?.querySelector<HTMLButtonElement>("button"))?.focus();
  }, [open]);
  function close() { setOpen(false); trigger.current?.focus(); }

  return <section className={css.target} aria-label="Encuentro de preparación">
    <div className={css.context}>
      <div className={css.encounterContext}>
        <p>{target ? `${fixtureLabel(target)} · ` : ""}{context}</p>
      </div>
      <Button ref={trigger} size="sm" aria-expanded={open} aria-controls={selectorId}
        disabled={!choices.length} onClick={() => open ? close() : setOpen(true)}>Cambiar rival</Button>
    </div>
    {!target ? <p className={css.message} role={empty.alert ? "alert" : "status"}>{empty.text}</p> : <>
      <div className={css.fixture}>
        <FixtureTeam team={target.home} side="home" />
        <span className={css.versus} aria-hidden="true">—</span>
        <FixtureTeam team={target.away} side="away" />
      </div>
      <div className={css.preparationContext}>
        {days !== null ? <p>{days === 0 ? "Partido hoy" : days === 1 ? "Partido mañana" : `Partido dentro de ${days} días`}</p> : null}
      </div>
      {analyze ? <div className={css.actions}>
        <Link className={css.primaryLink} href={analyze.href}>{analyze.label}</Link>
      </div> : null}
    </>}
    {notes}
    {open ? <div ref={options} id={selectorId} role="region" aria-label="Próximos encuentros del Real Jaén"
      className={css.options} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); close(); } }}>
      {choices.map(fixture => {
        const opponent = opponentOf(fixture);
        return <button type="button" key={fixture.id} aria-pressed={fixture.id === target?.id} onClick={() => { onChoose(fixture); close(); }}>
          <TeamCrest team={opponent} size={28} />
          <span className={css.optionContent}><strong>{[roundLabel(fixture.round), opponent.name].filter(Boolean).join(" · ")}</strong><time dateTime={fixture.kickoff}>{dateLabel(fixture.kickoff)}</time></span>
          {fixture.id === target?.id ? <span className={css.current}>Actual</span> : null}
        </button>;
      })}
    </div> : null}
  </section>;
}

function FixtureTeam({ team, side }: { team: CrestTeam; side: "home" | "away" }) {
  return <div className={`${css.fixtureTeam} ${css[side]}`}>
    <TeamCrest team={team} size={44} />
    <div><Link className={css.fixtureName} href={team.href} aria-label={`Ver equipo ${team.name}`} data-rival={team.id !== CLUB_ID || undefined}>{team.name}</Link><span>{side === "home" ? "Local" : "Visitante"}</span></div>
  </div>;
}

/** Form and goals of the summary; `null` when no listed match has a result to summarise. */
export type FormSummary = { wins: number; draws: number; losses: number; goalsFor: SeasonStat; goalsAgainst: SeasonStat };
/** xG of the summary, with coverage for the matches that contain a valid value. */
export type Expectation = { xg: SeasonStat; opponentXg: SeasonStat };

/**
 * The rival's previous matches and their summary. `rows` are at most five,
 * named by their real count and by the analysed team (`team`); `coverage`
 * says what the window leaves out. The summary is from that team's side: its
 * xG is «a favor», its rivals' «en contra».
 */
export function HistorySection({ team, rows, summary, expectation, allHref, coverage }: {
  team: string; rows: readonly HistoryRow[]; summary: FormSummary | null; expectation: Expectation; allHref: string; coverage?: ReactNode;
}) {
  // Fewer than five previous matches are named by their real count.
  const title = `${rows.length === 1 ? "Último partido" : `Últimos ${rows.length || 5} partidos`} del ${team}`;
  return <div className={css.history}>
    <section aria-labelledby="home-recent-title">
      <div className={css.sectionHeader}>
        <h2 className={sections.zoneTitle} id="home-recent-title">{title}</h2>
        <Link className={css.secondaryLink} href={allHref}>Ver todos</Link>
      </div>
      {coverage}
      {rows.length ? <>
        {summary ? <dl className={css.summary} aria-label="Síntesis de partidos recientes">
          <div>
            <dt>Forma</dt>
            <dd className={css.formValues}>
              <span className={css.formChip} data-result="win"><strong>{summary.wins}</strong><abbr title="Victorias">V</abbr></span>
              <span className={css.formChip} data-result="draw"><strong>{summary.draws}</strong><abbr title="Empates">E</abbr></span>
              <span className={css.formChip} data-result="loss"><strong>{summary.losses}</strong><abbr title="Derrotas">D</abbr></span>
            </dd>
          </div>
          <div>
            <dt>Goles</dt>
            <dd className={css.metricPair}>
              <SummaryValue label="GF" stat={summary.goalsFor} />
              <SummaryValue label="GC" stat={summary.goalsAgainst} />
            </dd>
          </div>
          <div>
            <dt>Expectativa</dt>
            <dd className={css.metricPair}>
              <SummaryValue label="xG a favor" stat={expectation.xg} decimals={2} />
              <SummaryValue label="xG en contra" stat={expectation.opponentXg} decimals={2} />
            </dd>
          </div>
        </dl> : null}
        <PreparationMatches rows={rows} label="Últimos partidos del rival" />
      </> : <p className={css.message}>Todavía no hay partidos finalizados con fecha anterior al encuentro objetivo.</p>}
    </section>
  </div>;
}

function SummaryValue({ label, stat, decimals = 0 }: { label: string; stat: SeasonStat; decimals?: number }) {
  const note = stat.value === null ? stat.reason === "not_collected" ? "Dato no recogido" : "Dato no disponible"
    : stat.coverage === "partial" ? `Datos disponibles en ${stat.matches} de ${stat.of} partidos` : undefined;
  return <span className={css.measure}>
    <span className={css.reading}>
      <strong title={note} aria-label={note}>
        {stat.value === null ? "—" : formatNumber(stat.value, decimals)}
        {stat.coverage === "partial" ? <sup aria-hidden="true">*</sup> : null}
      </strong>
      <span className={css.valueLabel}>{" "}{label}</span>
    </span>
    {stat.coverage === "partial" ? <span className={css.coverage}>{stat.matches}/{stat.of} partidos con dato</span> : null}
  </span>;
}

function PreparationMatches({ rows, label }: { rows: readonly HistoryRow[]; label: string }) {
  return <ul className={css.matches} aria-label={label}>
    {rows.map(row => <li key={row.id}>
      <div className={css.match} onClick={event => activateRowLink(event.target, event.currentTarget, event)}>
        <div className={css.meta}>{row.round === null ? null : <strong>J{row.round}</strong>}<time dateTime={row.kickoff}>{dateLabel(row.kickoff)}</time>
          {row.note ? <span>{row.note}</span> : null}</div>
        <MatchTeam team={row.home} side="home" />
        <Link className={css.score} data-row-primary="" href={row.href} prefetch={false}
          aria-label={[`Ver partido ${row.home.name} contra ${row.away.name}`, row.round === null ? null : `jornada ${row.round}`, dateLabel(row.kickoff),
            row.score ? `${row.score.home} a ${row.score.away}` : (row.note ?? "resultado no disponible").toLowerCase()].filter(Boolean).join(", ")}>
          {row.score ? `${row.score.home} – ${row.score.away}` : "—"}
        </Link>
        <MatchTeam team={row.away} side="away" />
      </div>
    </li>)}
  </ul>;
}

function MatchTeam({ team, side }: { team: CrestTeam; side: "home" | "away" }) {
  return <div className={`${css.matchTeam} ${css[side]}`}>
    <TeamCrest team={team} size={28} />
    <Link href={team.href} aria-label={`Ver equipo ${team.name}`}>
      <span className={css.fullName}>{team.name}</span><span className={css.shortName}>{team.shortName}</span>
    </Link>
  </div>;
}

export function LoadError({ message, retry }: { message: string; retry: () => void }) {
  return <div role="alert" className={css.error}><p>{message}</p><Button size="sm" onClick={retry}>Reintentar</Button></div>;
}
