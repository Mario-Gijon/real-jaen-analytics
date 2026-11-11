import type { MissingReason, SeasonStat, Stat } from "./common";

/**
 * How per-match values become a figure over several matches, with its
 * coverage. Pure and on contract types only, so data sources and screens
 * share one rule (season totals, a "last 3 matches" period…).
 *
 * - `sum`: total of the covered matches;
 * - `mean`: average of the covered matches (possession);
 * - `weighted`: average weighted by another stat (pass accuracy by passes);
 *   a match with a real weight of 0 is covered but contributes nothing.
 */
export type Accumulation =
  { kind: "sum" } | { kind: "mean" } | { kind: "weighted"; weights: readonly Stat[] };

const SUM: Accumulation = { kind: "sum" };

/** Season value of `perMatch` (one entry per match in scope), with its coverage. */
export function seasonStat(
  perMatch: readonly Stat[],
  accumulation: Accumulation = SUM,
): SeasonStat {
  const of = perMatch.length;
  const reasons: MissingReason[] = [];
  let matches = 0;
  let total = 0;
  let weightTotal = 0;
  perMatch.forEach((stat, index) => {
    const weight = accumulation.kind === "weighted" ? accumulation.weights[index] : { value: 1 };
    if (stat.value !== null && weight.value !== null) {
      matches++;
      total += stat.value * weight.value;
      weightTotal += weight.value;
    } else if (accumulation.kind === "weighted" && weight.value === 0) {
      matches++;
    } else {
      reasons.push(
        stat.value === null ? stat.reason : (weight as { reason: MissingReason }).reason,
      );
    }
  });

  if (matches === 0 && of > 0) {
    const reason = reasons.includes("not_collected") ? "not_collected" : "not_available";
    return { value: null, coverage: "none", reason, matches: 0, of };
  }
  if (accumulation.kind === "sum") {
    return {
      value: round(total, 2),
      coverage: matches === of ? "complete" : "partial",
      matches,
      of,
    };
  }
  // An average over nothing (no matches, or only weights of 0) does not exist.
  if (weightTotal === 0)
    return { value: null, coverage: "none", reason: "not_available", matches: 0, of };
  return {
    value: round(total / weightTotal, 1),
    coverage: matches === of ? "complete" : "partial",
    matches,
    of,
  };
}

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}
