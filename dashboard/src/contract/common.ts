/** Why a metric has no value. */
export type MissingReason =
  /** The source does not offer it, or it does not apply (e.g. saves of a defender). */
  | "not_available"
  /** It should exist but has not been collected yet. */
  | "not_collected"
  /** The source's own figures for it contradict each other; none is shown. */
  | "inconsistent"
  /** Versions or duplicate records of the source disagree, and none is chosen. */
  | "conflicting"
  /** The source gives a value outside the metric's domain. */
  | "invalid";

/**
 * How a value the source does not give as such was obtained. The data
 * service applies the rule (documented there); the interface only says so.
 * - `derived` · `shots_partition`: shots on target is 0 because the shots
 *   the source reports as off target or blocked add up to all the shots.
 * - `calculated` · `ratio`: a quotient of two known values (pass accuracy).
 */
export type StatDerivation = { source: "derived"; rule: "shots_partition" } | { source: "calculated"; rule: "ratio" };

/**
 * One metric in one match. A real zero is `{ value: 0 }`; a missing value
 * always says why, so the interface never turns an absence into a fictitious
 * zero. A value without `derivation` is the source's own.
 */
export type Stat = { value: number; derivation?: StatDerivation } | { value: null; reason: MissingReason };

/**
 * A metric accumulated over several matches. `of` is the number of matches in
 * scope (the player's appearances, the team's played matches) and `matches`
 * how many of them actually had the metric:
 *
 * - `complete`: every match in scope contributed (`matches === of`);
 * - `partial`: only some did, so `value` is NOT a whole-season figure;
 * - `none`: none did; `reason` is `not_collected` when at least one match was
 *   still pending collection, `not_available` otherwise.
 *
 * Totals are sums; ratios (possession, pass accuracy) are computed over the
 * covered matches only. A per-match average is `value / matches`, never `value / of`.
 */
export type SeasonStat =
  | { value: number; coverage: "complete" | "partial"; matches: number; of: number }
  | { value: null; coverage: "none"; reason: MissingReason; matches: 0; of: number };

/** Enough of a team to show it anywhere (lists, match header, crest grid). */
export type TeamRef = {
  id: string;
  name: string;
  shortName: string;
  /** Crest image served by the app, or `null` (draw a placeholder with `colors`). */
  crestUrl: string | null;
  /** Primary and secondary kit colours as `#rrggbb`. */
  colors: { primary: string; secondary: string };
};

/** General position of a player, independent of any line-up. */
export type PlayerPosition = "goalkeeper" | "defender" | "midfielder" | "forward";

export type PreferredFoot = "left" | "right" | "both";

/** Enough of a player to name it in a line-up, an event or a table row. */
export type PlayerRef = {
  id: string;
  name: string;
  /** General position, `null` when unknown. */
  position: PlayerPosition | null;
};

/** Who supplied one response, when a section may not use the app's main source. */
export type DataProvenance = {
  /** Invented data. */
  synthetic: boolean;
  /** Short description for the analyst, in Spanish. */
  label: string;
};

/** A team as a real source names it; `name` is `null` when the source cannot name it. */
export type ObservedTeam = { id: string; name: string | null };

/** `GET /api/v1/source`: what the interface may know about the data source. */
export type SourceInfo = {
  /** Data are invented and must be labelled as such on every screen. */
  synthetic: boolean;
  /** Short description for the analyst, in Spanish. */
  label: string;
  /**
   * Sections served by a real source instead of the synthetic data, so a
   * screen picks its real view without guessing: `observedPlayers` (Equipos ›
   * Jugadores and real player profiles) and `matches` (Partidos).
   */
  realSections: RealSection[];
};

export type RealSection = "observedPlayers" | "matches" | "teams";

/** Error body of every `/api/v1` endpoint. */
export type ApiError = { error: { code: ApiErrorCode; message: string } };

/**
 * `UPSTREAM_UNAVAILABLE` (503): a real source did not answer, or answered something unusable. The update
 * routes (docs/contrato-actualizaciones.md) add `UNAUTHORIZED` (401, no session), `FORBIDDEN` (403,
 * another origin), `UPDATE_UNAVAILABLE` (409, demonstration or not configured), `CONFLICT` (409, e.g. a job that
 * can no longer be cancelled) and `TOO_MANY_REQUESTS` (429).
 */
export type ApiErrorCode =
  | "NOT_FOUND"
  | "BAD_REQUEST"
  | "UPSTREAM_UNAVAILABLE"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "UPDATE_UNAVAILABLE"
  | "CONFLICT"
  | "TOO_MANY_REQUESTS";

/**
 * `GET /api/v1/session`: whether this installation asks for the shared password (always with real data) and
 * whether the request has a valid session. `POST` with `{ password }` opens a 90-day session (HttpOnly cookie);
 * `DELETE` closes this browser's session.
 */
export type AccessSession = { required: boolean; signedIn: boolean };
