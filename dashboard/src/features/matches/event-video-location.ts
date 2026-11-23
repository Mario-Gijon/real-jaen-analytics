/**
 * Pure preparation of narrative events for Video Service location requests
 * (`locateMatchVideoEvent`). Shared by every screen that replays match events.
 */
import type { EnrichedComment } from "@/contract";

const matchIdPattern = /^sofascore-match-([1-9]\d*)$/;

export function sourceEventId(matchId: string): string | null {
  return matchIdPattern.exec(matchId)?.[1] ?? null;
}

export function locationParameters(item: EnrichedComment) {
  const period = periodFor(item.periodName);
  const eventKind: "other" | "match_start" | "period_start" = item.originalType === "matchStarted" && period === "first" ? "match_start"
    : item.originalType === "matchStarted" && period === "second" ? "period_start" : "other";
  const periodStart = eventKind !== "other";
  if ((!periodStart && (item.minute === null || item.minute < 1)) || !period) return null;
  const minute = item.minute !== null && item.minute >= 1 ? minuteForVideo(item.minute, period) : null;
  return { period, eventKind, minute };
}

export function periodFor(periodName: string | null): "first" | "second" | null {
  if (periodName === "1ST") return "first";
  if (periodName === "2ND") return "second";
  return null;
}

export function minuteForVideo(minute: number, period: "first" | "second"): string {
  // SofaScore stores first-half stoppage time as 46′, 47′, etc.; V5.1 expects 45+N.
  return period === "first" && minute > 45 ? `45+${minute - 45}` : String(minute);
}

/** Seconds of play shown before a located event when replaying it. */
export const PLAYBACK_LEAD_SECONDS = 45;

/**
 * Why a located event cannot be replayed, in Spanish, for the location statuses
 * without a playback position; `null` otherwise (resolved, pending, failed, absent).
 */
export function locationUnavailableReason(location: { status: string; reason?: string } | undefined): string | null {
  if (location?.status === "ambiguous") {
    return location.reason === "multiple_first_reference_segments"
      ? "Hay varias referencias iniciales incompatibles; no se elige una posición única."
      : "Hay más de una posición posible.";
  }
  if (location?.status === "uncertain") {
    if (location.reason === "following_reference_crosses_discontinuity") {
      return "La referencia OCR posterior está separada por un posible corte o cambio de desfase.";
    }
    if (location.reason === "following_reference_evidence_insufficient") {
      return "La referencia OCR posterior no reúne suficientes lecturas consistentes.";
    }
    return "No se ha identificado con seguridad la parte o continuidad del partido.";
  }
  if (location?.status === "out_of_coverage") {
    return location.reason === "event_outside_verified_ocr_segments_or_safe_extrapolation"
      ? "No hay una referencia OCR fiable lo bastante cerca de este instante dentro de un tramo continuo."
      : "El minuto queda fuera de la cobertura OCR verificada.";
  }
  return null;
}
