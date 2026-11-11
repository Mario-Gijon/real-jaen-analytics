"use client";

import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { playerHref } from "../players/navigation";
import type { PlayerPosition, PlayerRef, PlayerSeasonStats, PreferredFoot, SeasonStat } from "@/contract";
import {
  POSITION_LABEL,
  POSITION_ORDER,
  POSITION_PLURAL,
  POSITION_SHORT,
  positionLabel,
  footLabel,
} from "@/lib/format";
import { PlayerAvatar } from "@/ui/PlayerAvatar";
import { SeasonValue } from "../SeasonValue";
import tableHeader from "@/ui/TableHeader.module.css";
import css from "./TeamPlayersTable.module.css";

type StatKey = keyof Pick<
  PlayerSeasonStats,
  "appearances" | "starts" | "minutes" | "goals" | "assists"
>;
export type SortKey = "name" | "position" | StatKey;
type Sort = { key: SortKey; direction: "ascending" | "descending" };

/**
 * One row: a synthetic squad member, or an observed player of a real source
 * whose figures count call-ups and whose personal data come with the row.
 */
export type SquadRow = {
  player: PlayerRef;
  shirtNumber: number | null;
  stats: Record<StatKey, SeasonStat>;
  /** Already in the response; without it, each row asks for the player's season. */
  personal?: { heightCm: number | null; preferredFoot: PreferredFoot | null };
};

/** Key, header, header on phones, full name. */
const STAT_COLUMNS: readonly (readonly [StatKey, string, string, string])[] = [
  ["appearances", "Partidos", "Partidos", "Partidos jugados"],
  ["starts", "Titularidades", "Titularidades", "Titularidades"],
  ["minutes", "Minutos", "Minutos", "Minutos"],
  ["goals", "Goles", "Goles", "Goles"],
  ["assists", "Asistencias", "Asistencias", "Asistencias"],
];

const lineIndex = (position: PlayerPosition | null) =>
  position ? POSITION_ORDER.indexOf(position) : POSITION_ORDER.length;

/** Line, then shirt number: how a squad list reads by default. */
function byPosition(a: SquadRow, b: SquadRow) {
  return (
    lineIndex(a.player.position) - lineIndex(b.player.position) ||
    (a.shirtNumber ?? 999) - (b.shirtNumber ?? 999) ||
    a.player.name.localeCompare(b.player.name, "es")
  );
}

/** Complete figures, then partial ones, then missing ones. */
const COVERAGE_RANK: Readonly<Record<SeasonStat["coverage"], number>> = { complete: 0, partial: 1, none: 2 };

/**
 * Whatever the direction, complete figures go first, then partial ones (each
 * group by its value) and missing ones last, so a partial figure is never
 * ranked as a whole-season total nor as 0. Ties read by position.
 */
function sortSquad(rows: readonly SquadRow[], { key, direction }: Sort): SquadRow[] {
  const sign = direction === "ascending" ? 1 : -1;
  return [...rows].sort((a, b) => {
    if (key === "position") return sign * byPosition(a, b);
    if (key === "name") return sign * a.player.name.localeCompare(b.player.name, "es");
    const [x, y] = [a.stats[key], b.stats[key]];
    const group = COVERAGE_RANK[x.coverage] - COVERAGE_RANK[y.coverage];
    if (group) return group;
    if (x.value === null || y.value === null) return byPosition(a, b);
    return sign * (x.value - y.value) || byPosition(a, b);
  });
}

/** Thin bar below the minutes: share of the squad's highest figure, a visual aid only. */
function MinutesBar({ minutes, max }: { minutes: number; max: number }) {
  const share = max > 0 ? Math.min(1, minutes / max) : 0;
  return (
    <span aria-hidden="true" className={css.bar}>
      {minutes > 0 ? <span style={{ width: `${share * 100}%` }} /> : null}
    </span>
  );
}

/** Partial notes of a real source's figures, which count call-ups rather than matches. */
function callUpsNote(key: StatKey) {
  return (stat: SeasonStat) => key === "appearances"
    ? `Participación conocida en ${stat.matches} de ${stat.of} convocatorias`
    : `Dato de ${stat.matches} de ${stat.of} convocatorias`;
}

/**
 * The season squad: filter by line, sort by column, navigate to the player.
 * Every squad member appears, with or without minutes; a real 0 reads «0».
 * With `observed`, the rows are a real source's observed players: figures are
 * over call-ups (a call-up without resolved participation makes them
 * partial), the shirt is the last one observed and each name opens the real
 * profile through `href`. The table loads nothing: a row without its
 * personal data gets them from `personalCells` (its height and foot cells).
 */
export function TeamPlayersTable({
  squad,
  seasonId,
  teamId,
  observed,
  personalCells,
}: {
  squad: readonly SquadRow[];
  seasonId: string;
  teamId: string;
  observed?: { href: (playerId: string) => string };
  personalCells?: (playerId: string) => ReactNode;
}) {
  const [line, setLine] = useState<PlayerPosition | "all">("all");
  const [sort, setSort] = useState<Sort>({ key: "position", direction: "ascending" });
  const counts = useMemo(() => {
    const result = new Map<PlayerPosition, number>();
    for (const { player } of squad) {
      if (player.position) result.set(player.position, (result.get(player.position) ?? 0) + 1);
    }
    return result;
  }, [squad]);
  // Scale of the minutes bars: the whole squad, so the line filter never rescales them.
  const maxMinutes = useMemo(
    () => Math.max(0, ...squad.map((row) => row.stats.minutes.value ?? 0)),
    [squad],
  );
  const rows = useMemo(
    () =>
      sortSquad(
        squad.filter((row) => line === "all" || row.player.position === line),
        sort,
      ),
    [squad, line, sort],
  );

  function header(key: SortKey, short: string, long: string, numeric = false, phone = short) {
    const active = sort.key === key;
    const Icon = !active ? ArrowUpDown : sort.direction === "ascending" ? ArrowUp : ArrowDown;
    return (
      <button
        className={css.sort}
        data-active={active || undefined}
        onClick={() =>
          setSort(
            active
              ? { key, direction: sort.direction === "ascending" ? "descending" : "ascending" }
              : { key, direction: numeric ? "descending" : "ascending" },
          )
        }
        title={`Ordenar por ${long.toLocaleLowerCase("es")}`}
        type="button"
      >
        <abbr title={long}>
          {phone === short ? (
            short
          ) : (
            <>
              <span className={css.longLabel}>{short}</span>
              <span className={css.shortLabel}>{phone}</span>
            </>
          )}
        </abbr>
        <Icon aria-hidden="true" size={12} />
      </button>
    );
  }
  const ariaSort = (key: SortKey) => (sort.key === key ? sort.direction : undefined);

  return (
    <div className={css.players}>
      <div aria-label="Filtrar por posición" className={css.filters} role="group">
        <button aria-pressed={line === "all"} onClick={() => setLine("all")} type="button">
          Todos <span>{squad.length}</span>
        </button>
        {POSITION_ORDER.filter((p) => counts.get(p)).map((position) => (
          <button
            aria-pressed={line === position}
            key={position}
            onClick={() => setLine(position)}
            type="button"
          >
            {POSITION_PLURAL[position]} <span>{counts.get(position)}</span>
          </button>
        ))}
      </div>
      <div className={css.scroll}>
        <table className={css.table}>
          <caption className={css.sr}>{observed ? "Jugadores observados" : "Jugadores de la plantilla"}</caption>
          <thead className={tableHeader.header}>
            <tr>
              <th aria-sort={ariaSort("name")} className={css.player} scope="col">
                {header("name", "Jugador", "Nombre")}
              </th>
              <th aria-sort={ariaSort("position")} className={css.position} scope="col">
                {header("position", "Posición", "Posición")}
              </th>
              {STAT_COLUMNS.map(([key, short, phone, long]) => (
                <th aria-sort={ariaSort(key)} className={css.stat} key={key} scope="col">
                  {header(key, short, observed && key === "appearances" ? "Partidos jugados (convocatorias con participación)" : long, true, phone)}
                </th>
              ))}
              <th scope="col">Estatura</th>
              <th scope="col">Pierna</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr
                data-unused={(row.stats.appearances.value === 0 && row.stats.appearances.coverage === "complete") || undefined}
                key={row.player.id}
              >
                <th className={css.player} scope="row">
                  <span className={css.playerIdentity}>
                    <span
                      aria-hidden="true"
                      className={css.shirt}
                      title={observed && row.shirtNumber !== null ? "Dorsal en su última convocatoria observada" : undefined}
                    >
                      {row.shirtNumber ?? "—"}
                    </span>
                    {/* The contract has no player picture yet: always the neutral fallback. */}
                    <PlayerAvatar name={row.player.name} />
                    <Link
                      aria-label={`Ver ficha de ${row.player.name}`}
                      className={css.open}
                      href={observed ? observed.href(row.player.id) : playerHref(row.player.id, seasonId, { kind: "team", id: teamId })}
                      prefetch={false}
                    >
                      <span className={css.name}>{row.player.name}</span>
                    </Link>
                  </span>
                </th>
                <td className={css.position}>
                  <span className={css.positionLong}>{positionLabel(row.player.position)}</span>
                  <abbr
                    className={css.positionShort}
                    title={row.player.position ? POSITION_LABEL[row.player.position] : undefined}
                  >
                    {row.player.position ? POSITION_SHORT[row.player.position] : "—"}
                  </abbr>
                </td>
                {STAT_COLUMNS.map(([key]) => (
                  <td
                    className={key === "minutes" ? `${css.stat} ${css.minutes}` : css.stat}
                    key={key}
                  >
                    {key === "minutes" ? (
                      <span className={css.minutesValue}>
                        <SeasonValue partialNote={observed && callUpsNote(key)} stat={row.stats[key]} />
                        <MinutesBar max={maxMinutes} minutes={row.stats.minutes.value ?? 0} />
                      </span>
                    ) : <SeasonValue partialNote={observed && callUpsNote(key)} stat={row.stats[key]} />}
                  </td>
                ))}
                {row.personal ? (
                  <>
                    <td>{row.personal.heightCm !== null ? `${row.personal.heightCm} cm` : "—"}</td>
                    <td>{footLabel(row.personal.preferredFoot)}</td>
                  </>
                ) : personalCells?.(row.player.id) ?? <><td>—</td><td>—</td></>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {observed && rows.some((row) => STAT_COLUMNS.some(([key]) => row.stats[key].value !== null && row.stats[key].coverage === "partial")) ? (
        <aside className={css.note} aria-label="Nota sobre los datos">
          <strong>Nota sobre los datos</strong>
          <p>El asterisco (*) indica que la cifra se basa en datos de solo algunas convocatorias y puede estar incompleta.</p>
        </aside>
      ) : null}
      {rows.length === 0 ? <p className={css.empty}>No hay jugadores en esta posición.</p> : null}
    </div>
  );
}
