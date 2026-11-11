export const SAFE_ID = /^[a-zA-Z0-9][a-zA-Z0-9_-]*$/;

/** The status filter of «Partidos». */
export type MatchesState = "all" | "finished" | "pending";

export const matchesState = (value: unknown): MatchesState => (value === "finished" || value === "pending" ? value : "all");

/** «Partidos» on the team and status a real match was opened from, as its URL had them (`matchesHref`). */
export type ObservedMatchListOrigin = { kind: "matches"; teamId: string | null; state: MatchesState };
