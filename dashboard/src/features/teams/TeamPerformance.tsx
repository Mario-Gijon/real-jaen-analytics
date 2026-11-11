"use client";

import { useState } from "react";
import type { TeamMatch, TeamSeason } from "@/contract";
import { average, finishedMatches, goalsAgainst, goalsFor, periodMatches, venueRecord, type Period } from "./performance";
import { matchHref, teamsHref } from "@/lib/navigation";
import { TeamOutput } from "./TeamOutput";
import { OutputZone, PlayedZone, SeasonZone, type BalanceItem, type Played } from "./TeamPerformanceParts";
import css from "./TeamPerformance.module.css";

function syntheticPlayed(m: TeamMatch): Played {
  return {
    id: m.match.id, round: m.match.round, kickoff: m.match.kickoff, side: m.side, opponent: m.opponent,
    result: m.result!, goalsFor: goalsFor(m), goalsAgainst: goalsAgainst(m),
    href: matchHref(m.match.id, { kind: "teams", seasonId: m.match.seasonId,
      teamId: m.side === "home" ? m.match.home.id : m.match.away.id, tab: "performance" }),
    opponentHref: teamsHref({ seasonId: m.match.seasonId, teamId: m.opponent.id }),
  };
}

/**
 * «Rendimiento del equipo» in three zones that answer three questions:
 * TEMPORADA (how is the team doing), RENDIMIENTO (what it produces and
 * concedes, for the season or the last three matches) and PARTIDOS (which
 * matches explain it). Only `status: finished` counts as played.
 */
export function TeamPerformance({ season }: { season: TeamSeason }) {
  const finished = finishedMatches(season.matches);
  const played = finished.map(syntheticPlayed);
  const [period, setPeriod] = useState<Period>("season");
  const inPeriod = periodMatches(season.matches, period);
  const highlighted = new Set(period === "last3" ? inPeriod.map((m) => m.match.id) : []);
  const { record, rank } = season.standing;
  const balance: BalanceItem[] = [
    ["PJ", "Partidos jugados", record.played],
    ["V", "Victorias", record.wins],
    ["E", "Empates", record.draws],
    ["D", "Derrotas", record.losses],
    ["GF", "Goles a favor", record.goalsFor],
    ["GC", "Goles en contra", record.goalsAgainst],
  ];

  return (
    <div className={css.performance}>
      <SeasonZone balance={balance} lead={{ rank, points: record.points }} played={played} venues={venueRecord(season.matches)} />

      <OutputZone period={period} setPeriod={setPeriod}>
        {finished.length ? (
          <TeamOutput
            compare={period === "last3"}
            seasonValue={(indicator) => average(finished, indicator)}
            values={(indicator) => average(inPeriod, indicator)}
          />
        ) : (
          <p className={css.empty}>Sin partidos disputados.</p>
        )}
      </OutputZone>

      <PlayedZone highlighted={highlighted} played={played} />
    </div>
  );
}
