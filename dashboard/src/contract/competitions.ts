export type Season = {
  id: string;
  competitionId: string;
  /** Display name, e.g. "2026/27". */
  name: string;
  /** Number of rounds in the calendar. */
  rounds: number;
  /** Last round with at least one finished match; `null` before the first one. */
  currentRound: number | null;
};

/** `GET /api/v1/competitions` returns `Competition[]`. */
export type Competition = {
  id: string;
  name: string;
  shortName: string;
  /** Newest first. */
  seasons: Season[];
};

/**
 * A competition a real source serves in this version (its declared scope),
 * as the source names it: e.g. one group of a league in one season. Nothing
 * outside the scope is ever returned.
 */
export type ObservedCompetition = { name: string; season: string | null };
