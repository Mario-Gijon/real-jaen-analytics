/** Shared validation and parsing helpers for local API responses. */
import { type PlayerPosition, type PreferredFoot, type Stat } from "@/contract";
import { UpstreamUnavailableError } from "./http";

/** Source position letters; anything else is unknown, never guessed. */
const POSITIONS: Readonly<Record<string, PlayerPosition>> = {
  G: "goalkeeper",
  D: "defender",
  M: "midfielder",
  F: "forward",
};

/** Source foot values, case-insensitive; anything else is unknown. */
export const FEET: Readonly<Record<string, PreferredFoot>> = {
  left: "left",
  right: "right",
  both: "both",
};

export const NOT_AVAILABLE: Stat = { value: null, reason: "not_available" };

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export class InvalidLocalApiResponseError extends UpstreamUnavailableError {
  constructor(detail: string) {
    super("Respuesta no válida: " + detail);
    this.name = "InvalidLocalApiResponseError";
  }
}

export const invalid = (detail: string) => new InvalidLocalApiResponseError(detail);

/** `null` or absent stays unknown; any other non-text value makes the response invalid. */
export function optionalText(value: unknown, field: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") throw invalid(field);
  return value.trim() || null;
}

/** A positive integer id, or an invalid response. */
export function positiveId(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value <= 0) throw invalid(field);
  return value;
}

/** A finite number, `null` when absent; anything else makes the response invalid. */
export function optionalNumber(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isFinite(value)) throw invalid(field);
  return value;
}

/** Position as the source letter, or unknown. */
export function position(value: unknown, field: string): PlayerPosition | null {
  const letter = optionalText(value, field);
  return (letter && POSITIONS[letter]) || null;
}

export function decimal(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw invalid(field);
  return value;
}

/** A count or score of the source's aggregate: a non-negative integer (never a boolean nor a decimal). */
export function count(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) throw invalid(field);
  return value;
}
