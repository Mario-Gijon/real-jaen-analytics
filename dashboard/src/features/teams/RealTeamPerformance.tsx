"use client";

import { useState } from "react";
import type { ObservedCompetitionPerformance } from "@/contract";
import { observedCrest } from "@/lib/team-crests";
import { teamsHref } from "@/lib/navigation";
import type { Period } from "./performance";
import { TeamOutput } from "./TeamOutput";
import { OutputZone, PlayedZone, SeasonZone, signed, type BalanceItem, type Played } from "./TeamPerformanceParts";
import css from "./TeamPerformance.module.css";

/**
 * The same zones with a real source's results: balance (with goal
 * difference), home and away, the result sequence, production and
 * concession (as computed by the source, for the season or «Últimos 3») and
 * the played matches. No table position nor points (there are no
 * standings). If the production figures could not be read, the rest stays.
 * `matchHref` opens each match's real detail.
 */
export function RealTeamPerformance({ competition, matchHref }: {
  competition: ObservedCompetitionPerformance; matchHref: ((matchId: string) => string) | null;
}) {
  const { record, output } = competition;
  const [period, setPeriod] = useState<Period>("season");
  const highlighted = new Set(period === "last3" && output ? output.last3.matchIds : []);
  const played: Played[] = competition.results.map(({ match, side, result, goalsFor, goalsAgainst }) => ({
    id: match.id, round: match.round, kickoff: match.kickoff, side, result, goalsFor, goalsAgainst,
    opponent: { id: (side === "home" ? match.away : match.home).id, ...observedCrest(side === "home" ? match.away : match.home) },
    href: matchHref ? matchHref(match.id) : null,
    opponentHref: teamsHref({ teamId: (side === "home" ? match.away : match.home).id }),
  }));
  const balance: BalanceItem[] = [
    ["PJ", "Partidos jugados", record.played],
    ["V", "Victorias", record.wins],
    ["E", "Empates", record.draws],
    ["D", "Derrotas", record.losses],
    ["GF", "Goles a favor", record.goalsFor],
    ["GC", "Goles en contra", record.goalsAgainst],
    ["DG", "Diferencia de goles", signed(record.goalDifference)],
  ];
  if (!record.played) {
    return <div className={css.performance}>
      <p className={css.empty} role="status">Sin partidos finalizados observados en esta competición.</p>
    </div>;
  }
  return (
    <div className={css.performance}>
      <SeasonZone balance={balance} lead={null} played={played} venues={competition.venues} />
      <OutputZone period={period} setPeriod={setPeriod}>
        {output ? (
          <TeamOutput
            compare={period === "last3"}
            seasonValue={(indicator) => output.season[indicator]}
            values={(indicator) => (period === "last3" ? output.last3.values : output.season)[indicator]}
          />
        ) : (
          <p className={css.empty} role="status">No se pudieron leer la producción y la concesión de estos partidos.</p>
        )}
      </OutputZone>
      <PlayedZone highlighted={highlighted} played={played} />
    </div>
  );
}
