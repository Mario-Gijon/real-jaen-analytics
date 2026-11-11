import {
  seasonStat,
  type MatchResult,
  type TeamOutputIndicator,
  type SeasonStat,
  type Stat,
  type TeamMatch,
  type TeamMatchStats,
  type TeamStatKey,
} from "@/contract";

/**
 * What the «Rendimiento del equipo» tab derives from a team's matches. Pure
 * functions over contract types: only `status: finished` matches count, and
 * conceded figures are the rival's stats in each match.
 */

export type Period = "season" | "last3";

/** Finished matches in chronological order (kickoff, then round); never by `currentRound`. */
export function finishedMatches(matches: readonly TeamMatch[]): TeamMatch[] {
  return matches
    .filter((m) => m.match.status === "finished" && m.result !== null && m.match.score !== null)
    .sort(
      (a, b) =>
        (a.match.kickoff ?? "").localeCompare(b.match.kickoff ?? "") ||
        a.match.round - b.match.round,
    );
}

/** The matches of a period: all finished ones, or the three most recent. */
export function periodMatches(matches: readonly TeamMatch[], period: Period): TeamMatch[] {
  const finished = finishedMatches(matches);
  return period === "season" ? finished : finished.slice(-3);
}

export type VenueRecord = Record<"home" | "away", Record<MatchResult, number>>;

/** Wins, draws and losses at home and away, from finished matches only. */
export function venueRecord(matches: readonly TeamMatch[]): VenueRecord {
  const record: VenueRecord = {
    home: { win: 0, draw: 0, loss: 0 },
    away: { win: 0, draw: 0, loss: 0 },
  };
  for (const m of finishedMatches(matches)) record[m.side][m.result!]++;
  return record;
}

/**
 * What the output figures need from a match, seen from one team: its side,
 * the final score and the team stats oriented to it (`own`, `opponent`).
 * A synthetic `TeamMatch` is one; a real match oriented by its caller too.
 */
export type OrientedMatch = Pick<TeamMatch, "side" | "teamStats"> & { match: { score: TeamMatch["match"]["score"] } };

/** Goals of the team first, as the result reads from its side. */
export function goalsFor({ match, side }: OrientedMatch): number {
  return side === "home" ? match.score!.home : match.score!.away;
}
export function goalsAgainst({ match, side }: OrientedMatch): number {
  return side === "home" ? match.score!.away : match.score!.home;
}

export type Indicator = TeamOutputIndicator;

const NO_STATS: Stat = { value: null, reason: "not_available" };

/** One indicator in one match: from the score, the team's stats or its rival's. */
function matchValue(m: OrientedMatch, indicator: Indicator): Stat {
  if (indicator === "goals") return { value: goalsFor(m) };
  if (indicator === "goalsAgainst") return { value: goalsAgainst(m) };
  const conceded: Partial<Record<Indicator, TeamStatKey>> = {
    xgAgainst: "xg",
    shotsAgainst: "shots",
    shotsOnTargetAgainst: "shotsOnTarget",
  };
  const side: keyof NonNullable<TeamMatch["teamStats"]> = conceded[indicator] ? "opponent" : "own";
  const key = (conceded[indicator] ?? indicator) as TeamStatKey;
  const stats: TeamMatchStats | undefined = m.teamStats?.[side];
  return stats ? stats[key] : NO_STATS;
}

/**
 * Per-match average of an indicator over some matches, with its coverage:
 * the average uses only the matches that have the value (`matches`), out of
 * `of`. `value` is unrounded; `null` when no match had it.
 */
export function average(matches: readonly OrientedMatch[], indicator: Indicator): SeasonStat {
  const values = matches.map((m) => matchValue(m, indicator));
  // seasonStat gives the coverage (and why a value is missing) with the shared rule.
  const coverage = seasonStat(values);
  if (coverage.value === null) return coverage;
  if (coverage.matches === 0) {
    return { value: null, coverage: "none", reason: "not_available", matches: 0, of: 0 };
  }
  const sum = values.reduce((total, stat) => total + (stat.value ?? 0), 0);
  return { ...coverage, value: sum / coverage.matches };
}

/** Difference of a period value against the season one; `null` if either is missing. */
export function difference(period: SeasonStat, season: SeasonStat): number | null {
  if (period.value === null || season.value === null) return null;
  return period.value - season.value;
}
