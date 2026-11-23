import { describe, expect, it } from "vitest";
import type { EnrichedComment } from "@/contract";
import {
  locationParameters, locationUnavailableReason, minuteForVideo, PLAYBACK_LEAD_SECONDS, periodFor, sourceEventId,
} from "@/features/matches/event-video-location";

function comment(overrides: Partial<EnrichedComment> = {}): EnrichedComment {
  return {
    id: 1,
    captureId: 10,
    position: 0,
    text: "Gol.",
    originalType: "scoreChange",
    minute: 30,
    periodName: "1ST",
    isHome: true,
    score: null,
    category: "goal",
    attributes: {},
    participants: [],
    ...overrides,
  };
}

describe("sourceEventId", () => {
  it("returns the SofaScore event id of a public match id", () => {
    expect(sourceEventId("sofascore-match-16599363")).toBe("16599363");
    expect(sourceEventId("sofascore-match-1")).toBe("1");
  });

  it("rejects ids that are absent, malformed or from another source", () => {
    for (const value of [
      "", "sofascore-match-", "sofascore-match-0", "sofascore-match-0123", "sofascore-match--5", "sofascore-match-12a",
      "sofascore-match-12 ", " sofascore-match-12", "SOFASCORE-MATCH-12", "sofascore-player-12", "synthetic-match-12", "12",
    ]) expect(sourceEventId(value), value).toBeNull();
  });
});

describe("periodFor", () => {
  it("maps the regular SofaScore period names", () => {
    expect(periodFor("1ST")).toBe("first");
    expect(periodFor("2ND")).toBe("second");
  });

  it("returns null for any other or missing period, without case folding", () => {
    for (const value of [null, "", "1st", "2nd", "HT", "FT", "ET1", "ET2", "PEN", "3RD"]) expect(periodFor(value), String(value)).toBeNull();
  });
});

describe("minuteForVideo", () => {
  it("keeps regular first-half minutes, up to and including 45", () => {
    expect(minuteForVideo(1, "first")).toBe("1");
    expect(minuteForVideo(45, "first")).toBe("45");
  });

  it("writes first-half stoppage minutes as 45+N", () => {
    expect(minuteForVideo(46, "first")).toBe("45+1");
    expect(minuteForVideo(52, "first")).toBe("45+7");
  });

  it("keeps second-half minutes as they are, including those after 90", () => {
    expect(minuteForVideo(46, "second")).toBe("46");
    expect(minuteForVideo(90, "second")).toBe("90");
    expect(minuteForVideo(94, "second")).toBe("94");
  });
});

describe("locationParameters", () => {
  it("builds the locate parameters of an ordinary event", () => {
    expect(locationParameters(comment())).toEqual({ period: "first", eventKind: "other", minute: "30" });
    expect(locationParameters(comment({ minute: 78, periodName: "2ND" }))).toEqual({ period: "second", eventKind: "other", minute: "78" });
  });

  it("converts first-half stoppage time and keeps second-half stoppage time", () => {
    expect(locationParameters(comment({ minute: 47 }))).toEqual({ period: "first", eventKind: "other", minute: "45+2" });
    expect(locationParameters(comment({ minute: 93, periodName: "2ND" }))).toEqual({ period: "second", eventKind: "other", minute: "93" });
  });

  it("accepts minute 1 and rejects ordinary events without a minute of at least 1", () => {
    expect(locationParameters(comment({ minute: 1 }))).toEqual({ period: "first", eventKind: "other", minute: "1" });
    expect(locationParameters(comment({ minute: 0 }))).toBeNull();
    expect(locationParameters(comment({ minute: -1 }))).toBeNull();
    expect(locationParameters(comment({ minute: null }))).toBeNull();
  });

  it("rejects events outside the two regular periods", () => {
    for (const periodName of [null, "ET1", "PEN", "1st"]) expect(locationParameters(comment({ periodName })), String(periodName)).toBeNull();
  });

  it("identifies the match start and the second-half start, with or without a minute", () => {
    expect(locationParameters(comment({ originalType: "matchStarted", minute: null })))
      .toEqual({ period: "first", eventKind: "match_start", minute: null });
    expect(locationParameters(comment({ originalType: "matchStarted", minute: 0 })))
      .toEqual({ period: "first", eventKind: "match_start", minute: null });
    expect(locationParameters(comment({ originalType: "matchStarted", minute: 46, periodName: "2ND" })))
      .toEqual({ period: "second", eventKind: "period_start", minute: "46" });
    expect(locationParameters(comment({ originalType: "matchStarted", minute: null, periodName: "2ND" })))
      .toEqual({ period: "second", eventKind: "period_start", minute: null });
  });

  it("does not treat a period start outside the regular periods as localizable", () => {
    expect(locationParameters(comment({ originalType: "matchStarted", minute: null, periodName: null }))).toBeNull();
    expect(locationParameters(comment({ originalType: "matchStarted", minute: 91, periodName: "ET1" }))).toBeNull();
  });

  it("depends only on the original type, period and minute", () => {
    const base = locationParameters(comment());
    expect(locationParameters(comment({ category: null, text: "", isHome: null, captureId: null, id: "x", participants: [] }))).toEqual(base);
    expect(locationParameters(comment({ originalType: "matchEnded" }))).toEqual(base);
  });
});

describe("locationUnavailableReason", () => {
  // The exact messages of the match timeline (MatchCommentsPanel), now shared.
  it.each([
    [{ status: "ambiguous", reason: "multiple_first_reference_segments" },
      "Hay varias referencias iniciales incompatibles; no se elige una posición única."],
    [{ status: "ambiguous" }, "Hay más de una posición posible."],
    [{ status: "ambiguous", reason: "other_reason" }, "Hay más de una posición posible."],
    [{ status: "uncertain", reason: "following_reference_crosses_discontinuity" },
      "La referencia OCR posterior está separada por un posible corte o cambio de desfase."],
    [{ status: "uncertain", reason: "following_reference_evidence_insufficient" },
      "La referencia OCR posterior no reúne suficientes lecturas consistentes."],
    [{ status: "uncertain" }, "No se ha identificado con seguridad la parte o continuidad del partido."],
    [{ status: "out_of_coverage", reason: "event_outside_verified_ocr_segments_or_safe_extrapolation" },
      "No hay una referencia OCR fiable lo bastante cerca de este instante dentro de un tramo continuo."],
    [{ status: "out_of_coverage" }, "El minuto queda fuera de la cobertura OCR verificada."],
  ])("explains %o", (location, message) => {
    expect(locationUnavailableReason(location)).toBe(message);
  });

  it("has no reason for a resolved, pending, failed or missing location", () => {
    expect(locationUnavailableReason({ status: "resolved" })).toBeNull();
    expect(locationUnavailableReason({ status: "loading" })).toBeNull();
    expect(locationUnavailableReason({ status: "error" })).toBeNull();
    expect(locationUnavailableReason(undefined)).toBeNull();
  });
});

describe("PLAYBACK_LEAD_SECONDS", () => {
  it("starts replays 45 seconds before the located second", () => {
    expect(PLAYBACK_LEAD_SECONDS).toBe(45);
  });
});
