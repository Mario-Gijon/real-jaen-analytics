"use client";

import Link from "next/link";
import { House, LogOut } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { MatchResult, Side, TeamRef } from "@/contract";
import { LinkedTableRow } from "@/ui/LinkedTableRow";
import { TeamCrest } from "@/ui/TeamCrest";
import tableHeader from "@/ui/TableHeader.module.css";
import { dateLabel, formatNumber } from "@/lib/format";
import type { Period, VenueRecord } from "./performance";
import css from "./TeamPerformance.module.css";

/*
 * The zones of «Rendimiento del equipo» (TEMPORADA, RENDIMIENTO, PARTIDOS),
 * drawn the same from either data mode: each mode turns its own data into
 * `Played` rows and a balance, and nothing here loads data.
 */

const RESULT: Record<MatchResult, { letter: string; label: string }> = {
  win: { letter: "V", label: "Victoria" },
  draw: { letter: "E", label: "Empate" },
  loss: { letter: "D", label: "Derrota" },
};

const PERIODS: readonly (readonly [Period, string])[] = [
  ["season", "Temporada"],
  ["last3", "Últimos 3"],
];

/** A played match as the zones show it, whatever its source. */
export type Played = {
  id: string;
  /** `null` when the source does not give the round. */
  round: number | null;
  kickoff: string | null;
  side: Side;
  opponent: TeamRef;
  result: MatchResult;
  goalsFor: number;
  goalsAgainst: number;
  /** Detail of the match, or `null` when there is none to open. */
  href: string | null;
  /** Team identity destination for the opponent. */
  opponentHref: string;
};
/** Short label, long label and value of one balance item. */
export type BalanceItem = [string, string, string | number];

/** Goals of the team first, as the result reads from its side. */
const teamScore = (m: Played) => `${m.goalsFor}-${m.goalsAgainst}`;

function resultLabel(played: Played): string {
  const { label } = RESULT[played.result];
  const venue = played.side === "home" ? "en casa contra" : "fuera contra";
  const round = played.round !== null ? `Jornada ${played.round}: ` : "";
  return `${round}${label} ${teamScore(played)} ${venue} ${played.opponent.name}`;
}

/** Date with the year hidden on phones, where the season makes it obvious. */
function DateCell({ kickoff }: { kickoff: string | null }) {
  const text = dateLabel(kickoff);
  const cut = text.lastIndexOf(" ");
  if (cut < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, cut)}
      <span className={css.year}>{text.slice(cut)}</span>
    </>
  );
}

/** «+4», «−1» or «0»: a plain difference, never good or bad. */
export function signed(value: number): string {
  return value > 0 ? `+${value}` : value < 0 ? `−${Math.abs(value)}` : "0";
}

/** RENDIMIENTO: its title, the period control and what it shows for that period. */
export function OutputZone({ period, setPeriod, children }: { period: Period; setPeriod: (period: Period) => void; children: ReactNode }) {
  return (
    <section aria-labelledby="zone-output" className={css.zone}>
      <header className={css.zoneHeader}>
        <h3 className={css.zoneTitle} id="zone-output">
          Rendimiento
        </h3>
        <div aria-label="Periodo" className={css.periods} role="group">
          {PERIODS.map(([id, label]) => (
            <button
              aria-pressed={period === id}
              key={id}
              onClick={() => setPeriod(id)}
              type="button"
            >
              {label}
            </button>
          ))}
        </div>
      </header>
      {children}
    </section>
  );
}

/** PARTIDOS: the played matches, newest first, each opening its detail when there is one. */
export function PlayedZone({ played, highlighted }: { played: readonly Played[]; highlighted: ReadonlySet<string> }) {
  return (
    <section aria-labelledby="zone-matches" className={css.zone}>
      <header className={css.zoneHeader}>
        <h3 className={css.zoneTitle} id="zone-matches">
          Partidos
        </h3>
      </header>
      {played.length ? (
        <div className={css.scroll}>
          <table aria-label="Partidos disputados" className={css.table}>
            <thead className={tableHeader.header}>
              <tr>
                <th scope="col">
                  Jornada
                </th>
                <th scope="col">Fecha</th>
                <th className={css.venue} scope="col">
                  Condición
                </th>
                <th scope="col">Rival</th>
                <th className={css.end} scope="col">
                  Resultado
                </th>
              </tr>
            </thead>
            <tbody>
              {[...played].reverse().map((m) => {
                const marked = highlighted.has(m.id);
                const Row = m.href ? LinkedTableRow : "tr";
                const venueLabel = m.side === "home" ? "Local" : "Visitante";
                return (
                  <Row
                    data-in-period={marked || undefined}
                    data-linked={m.href ? undefined : "false"}
                    data-match-id={m.id}
                    key={m.id}
                  >
                    <td className={css.num}>
                      {m.round ?? "—"}
                      {marked ? <span className={css.sr}> (en «Últimos 3»)</span> : null}
                    </td>
                    <td className={css.date}>
                      <DateCell kickoff={m.kickoff} />
                    </td>
                    <td className={css.venue}>{venueLabel}</td>
                    <td className={css.rival}>
                      <abbr aria-label={venueLabel} className={css.venueShort}>
                        {m.side === "home" ? "L" : "V"}
                      </abbr>
                      <TeamCrest size={22} team={m.opponent} />
                      <Link href={m.opponentHref} className={css.opponent} prefetch={false} aria-label={`Ver equipo ${m.opponent.name}`}>
                        <span className={css.opponentName}>{m.opponent.name}</span>
                      </Link>
                    </td>
                    <td className={css.end}>
                      {m.href ? <Link href={m.href} data-row-primary="" className={css.matchResult} prefetch={false}
                        aria-label={`Ver partido: ${resultLabel(m)}`}>
                        <span
                        aria-label={`${RESULT[m.result].label} ${teamScore(m)}`}
                        className={css.score}
                        data-result={m.result}
                      >
                        {RESULT[m.result].letter} {teamScore(m)}
                        </span>
                      </Link> : <span
                        aria-label={`${RESULT[m.result].label} ${teamScore(m)}`}
                        className={css.score}
                        data-result={m.result}
                      >{RESULT[m.result].letter} {teamScore(m)}</span>}
                    </td>
                  </Row>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <p className={css.empty}>Sin partidos disputados.</p>
      )}
    </section>
  );
}

/** TEMPORADA: table position and points (when there are standings), record, home/away split and the result sequence. */
export function SeasonZone({ balance, lead, venues, played }: {
  balance: readonly BalanceItem[]; lead: { rank: number; points: number } | null; venues: VenueRecord; played: readonly Played[];
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  // When the sequence overflows, start at its end: the latest results.
  const sequence = useRef<HTMLOListElement>(null);
  const lastId = played.at(-1)?.id;
  useEffect(() => {
    const list = sequence.current;
    if (list) list.scrollLeft = list.scrollWidth;
  }, [lastId]);
  const active = played.find((m) => m.id === activeId) ?? played[played.length - 1];

  return (
    <section aria-labelledby="zone-season" className={css.zone}>
      <header className={css.zoneHeader}>
        <h3 className={css.zoneTitle} id="zone-season">
          Temporada
        </h3>
      </header>
      <div className={css.standing}>
        {lead ? (
          <dl className={css.lead}>
            <div>
              <dt>Clasificación</dt>
              <dd>{formatNumber(lead.rank)}.º</dd>
            </div>
            <div>
              <dt>Puntos</dt>
              <dd>{lead.points}</dd>
            </div>
          </dl>
        ) : null}
        <dl aria-label="Balance" className={css.balance}>
          {balance.map(([short, long, value]) => (
            <div data-kind={short} key={short}>
              <dt>
                <abbr title={long}>{short}</abbr>
              </dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <dl aria-label="Local y visitante" className={css.venues}>
          {(["home", "away"] as const).map((side) => (
            <div key={side}>
              <dt>
                {side === "home" ? <House size={14} aria-hidden="true" /> : <LogOut size={14} aria-hidden="true" />}
                {side === "home" ? "Local" : "Visitante"}
              </dt>
              <dd>
                {(["win", "draw", "loss"] as const).map((result) => (
                  <span data-result={result} key={result}>
                    <strong>{venues[side][result]}</strong>
                    <abbr title={`${RESULT[result].label}s`}>{RESULT[result].letter}</abbr>
                  </span>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      {played.length ? (
        <div className={css.form}>
          <ol aria-label="Resultados por jornada" className={css.chips} ref={sequence}>
            {played.map((m) => (
              <li key={m.id}>
                <button
                  aria-label={resultLabel(m)}
                  aria-pressed={m === active}
                  className={css.chip}
                  data-result={m.result}
                  onClick={() => setActiveId(m.id)}
                  onFocus={() => setActiveId(m.id)}
                  onMouseEnter={() => setActiveId(m.id)}
                  title={resultLabel(m)}
                  type="button"
                >
                  <span className={css.letter}>{RESULT[m.result].letter}</span>
                </button>
              </li>
            ))}
          </ol>
          {active ? (
            <p aria-live="polite" className={css.detail}>
              {active.round !== null ? <span className={css.detailRound}>J{active.round}</span> : null}
              <span>{active.side === "home" ? "Local" : "Visitante"}</span>
              <span className={css.detailOpponent}>
                <TeamCrest size={20} team={active.opponent} />
                <Link href={active.opponentHref} className={css.detailOpponentName} aria-label={`Ver equipo ${active.opponent.name}`}>
                  {active.opponent.name}
                </Link>
              </span>
              {active.href ? <Link href={active.href} className={css.detailMatch} prefetch={false}
                aria-label={`Ver partido: ${resultLabel(active)}`}>
                <span className={css.score} data-result={active.result}>
                  {RESULT[active.result].letter} {teamScore(active)}
                </span>
              </Link> : <span className={css.score} data-result={active.result}>
                {RESULT[active.result].letter} {teamScore(active)}
              </span>}
            </p>
          ) : null}
        </div>
      ) : (
        <p className={css.empty}>Todavía no ha disputado ningún partido.</p>
      )}
    </section>
  );
}
