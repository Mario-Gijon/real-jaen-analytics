/**
 * The analyst API contract (`/api/v1`). It is the only shape shared by the
 * screens and the server, and it describes what the analyst sees, never what a
 * provider returns. Every identifier is ours and stable; no provider id or
 * provider vocabulary appears here.
 */

export * from "./common";
export * from "./matches";
export * from "./teams";
export * from "./players";
export * from "./competitions";
export * from "./enrichment";
export * from "./videos";
export * from "./updates";
export { seasonStat, type Accumulation } from "./season-stat";
