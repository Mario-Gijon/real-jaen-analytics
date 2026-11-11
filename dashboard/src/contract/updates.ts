/**
 * What an update consults, as the backend's recipe declares it: "pilot" (the round-1 calendar and one match) or
 * "group" (the whole Group 2: missing results, postponed matches, calendar changes and corrections of the last
 * 72 hours, with partial coverage and sequential bounded batches). The texts come from the backend and are shown as they are.
 */
export type UpdateScope = {
  kind: "pilot" | "group";
  /** Spanish title of the scope. */
  label: string;
  /** Spanish sentence of what it does and does not update. */
  description: string;
  /** Text of the update button. */
  actionLabel: string;
  /** Pilot only: its round and match. */
  round: number | null;
  matchIds: string[];
};

/** Why a resource stayed pending: never absence, never zero. */
export type UpdatePendingReason = "empty" | "not_found" | "parse" | "error" | "budget" | "not_requested" | "restricted";

export type UpdateResource = "event" | "lineups" | "statistics" | "incidents";

/**
 * Coverage of a group update. `newResources`: answers with data just obtained; `keptResources`: previous data kept
 * as published (not consulted this time, or consulted without new data); `pending`: resources without usable data,
 * by match, with their reason and whether a previous version is still served (`kept`).
 */
export type UpdateCoverage = {
  newResources: number;
  keptResources: number;
  pending: { matchId: string; resources: { resource: UpdateResource; reason: UpdatePendingReason; kept: boolean }[] }[];
  /** Matches whose due review remained after safe batches ended (their data are kept). */
  deferredMatchIds: string[];
  /**
   * Matches with a detected change or due review outside the rounds this installation restricts its details to
   * (`detailRounds`): not consulted, their data kept. Empty without a restriction.
   */
  restrictedMatchIds: string[];
  /** The only rounds whose matches this update could consult; `null` without a restriction. */
  detailRounds: number[] | null;
  /** Matches new to the competition; `replaces`: unfinished matches with the same pairing, for a manual decision. */
  newMatches: { matchId: string; replaces: string[] }[];
  /** "Home – Away" of the matches above, from the data just published. */
  matchNames: Record<string, string>;
  /** Narrative-comment backfill within this update's sequential batches. */
  comments: {
    newMatchIds: string[];
    /** Successfully stored empty HTTP 200 captures: no narrations to display yet, checked again on later updates. */
    emptyMatchIds: string[];
    keptMatchIds: string[];
    pendingMatchIds: string[];
    failed: { matchId: string; reason: string }[];
  };
};

export type UpdateJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";

/** Phase of the current step; there is no percentage, only the step being done. */
export type UpdatePhase = "queued" | "acquiring" | "verifying" | "publishing" | "finished";

export type UpdateErrorCode =
  | "ACQUISITION_FAILED"
  | "ACQUISITION_INTERRUPTED"
  | "ACQUISITION_UNAVAILABLE"
  | "CANCELLED"
  | "BUNDLE_REJECTED"
  | "GENERATION_CHANGED"
  | "PUBLICATION_FAILED"
  | "INTERRUPTED"
  | "INTERNAL_ERROR";

/** One update. Whatever its outcome, the last valid published data keep being served. */
export type UpdateJob = {
  id: string;
  status: UpdateJobStatus;
  phase: UpdatePhase;
  /** Only while acquiring and not yet requested. */
  cancellable: boolean;
  cancelRequested: boolean;
  /** True when at least one verified batch was published, including a partial multi-batch update. */
  published: boolean;
  /** UTC ISO-8601. */
  requestedAt: string;
  finishedAt: string | null;
  pollAfterSeconds: number;
  error: { code: UpdateErrorCode; message: string; retryable: boolean } | null;
  /** `reusedBundle`: the completed update only republished verified data without consulting again. */
  result: { publishedAt: string; reusedBundle: boolean; coverage: UpdateCoverage | null } | null;
};

/** `GET /api/v1/updates/current` (any session: the same session that consults the data can update them). */
export type UpdateStatus = {
  available: boolean;
  scope: UpdateScope | null;
  /** Last data published by an update; `null` if none yet (not the server start time). */
  lastPublished: { at: string } | null;
  activeJob: UpdateJob | null;
  lastJob: UpdateJob | null;
  /**
   * The coverage of that last publication (group recipe), so its pending resources and deferred refreshes stay
   * visible while their previous data are served, even after a later update failed or was cancelled.
   */
  lastCoverage: UpdateCoverage | null;
};

/** `POST /api/v1/updates`: `reused` when the key or the active job already existed. */
export type UpdateStart = { job: UpdateJob; reused: boolean };
