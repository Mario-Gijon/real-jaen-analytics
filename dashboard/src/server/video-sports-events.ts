import type { MatchComments } from "@/contract";

export type VideoSportsEventsV1 = {
  contract: "video-sync.sports-events";
  version: 1;
  source_metadata: {
    source: "dashboard-bff";
    match_ref: string;
    captured_at: string | null;
    response_sha256: string | null;
    comment_count: number;
    source_order_policy: "source_position_descending_inverse_chronology";
    source_position_basis: "source_position";
    video_timestamp_policy: "never_derived_from_source_position_or_source_minute";
  };
  events: {
    source_order: number;
    source_id: string;
    original_type: string;
    normalized_type: "match_start" | "period_start" | "period_end" | "match_end" | "added_time" | "other";
    period: "first" | "second" | "unknown" | null;
    source_minute: number | null;
    game_clock_seconds: null;
    source_period_name: string | null;
    source_text: string;
    source_position: number;
    normalization_note?: string;
  }[];
};

export class SportsCommentsUnavailableError extends Error {}
export class SportsCommentsInvalidError extends Error {}

function sourcePeriod(periodName: string | null): "first" | "second" | "unknown" | null {
  if (periodName === null) return null;
  const normalized = periodName.trim().toUpperCase();
  if (normalized === "1ST") return "first";
  if (normalized === "2ND") return "second";
  return "unknown";
}

function normalizeEvent(type: string, originalPeriod: "first" | "second" | "unknown" | null) {
  let period = originalPeriod;
  let normalizedType: VideoSportsEventsV1["events"][number]["normalized_type"] = "other";
  let normalizationNote: string | undefined;

  if (type === "matchStarted") {
    if (originalPeriod === "first") normalizedType = "match_start";
    else if (originalPeriod === "second") normalizedType = "period_start";
    else normalizationNote = "matchStarted_without_recognized_periodName";
  } else if (type === "endFirstHalf") {
    if (originalPeriod === "second") {
      period = "unknown";
      normalizationNote = "type_period_conflict";
    } else {
      normalizedType = "period_end";
      period = "first";
    }
  } else if (type === "endSecondHalf") {
    if (originalPeriod === "first") {
      period = "unknown";
      normalizationNote = "type_period_conflict";
    } else {
      normalizedType = "period_end";
      period = "second";
    }
  } else if (type === "matchEnded") {
    if (originalPeriod === "first") {
      period = "unknown";
      normalizationNote = "type_period_conflict";
    } else {
      normalizedType = "match_end";
    }
  } else if (type === "addedTime") {
    if (originalPeriod === "first" || originalPeriod === "second") normalizedType = "added_time";
    else {
      period = "unknown";
      normalizationNote = "addedTime_without_recognized_periodName";
    }
  }

  return { period, normalizedType, normalizationNote };
}

/** Adapt the read-only Sports Data comments contract at the BFF boundary. */
export function commentsToVideoSportsEvents(matchRef: string, comments: MatchComments): VideoSportsEventsV1 | null {
  if (comments.availability === "error") {
    throw new SportsCommentsUnavailableError("La fuente de comentarios informó de un error.");
  }
  if ((comments.availability === "empty" || comments.availability === "not_collected")
      && comments.comments.length === 0) return null;
  if (comments.availability !== "available" || comments.comments.length === 0) {
    throw new SportsCommentsInvalidError("La disponibilidad no coincide con los comentarios recibidos.");
  }

  const positions = comments.comments.map((comment) => comment.position);
  if (new Set(positions).size !== positions.length) {
    throw new SportsCommentsInvalidError("La posición de origen no establece un orden inequívoco.");
  }

  const chronological = [...comments.comments].sort((left, right) => right.position - left.position);
  return {
    contract: "video-sync.sports-events",
    version: 1,
    source_metadata: {
      source: "dashboard-bff",
      match_ref: matchRef,
      captured_at: comments.capturedAt,
      response_sha256: comments.responseSha256,
      comment_count: chronological.length,
      source_order_policy: "source_position_descending_inverse_chronology",
      source_position_basis: "source_position",
      video_timestamp_policy: "never_derived_from_source_position_or_source_minute",
    },
    events: chronological.map((comment, sourceOrder) => {
      const result = normalizeEvent(comment.type, sourcePeriod(comment.periodName));
      return {
        source_order: sourceOrder,
        source_id: comment.id,
        original_type: comment.type,
        normalized_type: result.normalizedType,
        period: result.period,
        source_minute: comment.minute,
        game_clock_seconds: null,
        source_period_name: comment.periodName,
        source_text: comment.text,
        source_position: comment.position,
        ...(result.normalizationNote ? { normalization_note: result.normalizationNote } : {}),
      };
    }),
  };
}
