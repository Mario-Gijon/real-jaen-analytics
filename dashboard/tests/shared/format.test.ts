import { describe, expect, it } from "vitest";
import { crestCode } from "@/ui/TeamCrest";
import {
  ageOn,
  birthLabel,
  dateLabel,
  footLabel,
  heightLabel,
  positionLabel,
  seasonText,
  statText,
} from "@/lib/format";

describe("presentation of contract values", () => {
  it("keeps a real 0 and turns any absence into «—»", () => {
    expect(statText({ value: 0 })).toBe("0");
    expect(statText({ value: null, reason: "not_available" })).toBe("—");
    expect(statText({ value: null, reason: "not_collected" })).toBe("—");
    expect(seasonText({ value: 0, coverage: "complete", matches: 0, of: 0 })).toBe("0");
    expect(
      seasonText({ value: null, coverage: "none", reason: "not_collected", matches: 0, of: 4 }),
    ).toBe("—");
    expect(seasonText({ value: 20.5, coverage: "partial", matches: 3, of: 4 }, 2)).toBe("20,50");
  });

  it("labels personal data, or «—» when unknown", () => {
    expect(heightLabel(183)).toBe("1,83 m");
    expect(heightLabel(null)).toBe("—");
    expect(footLabel("both")).toBe("Ambas");
    expect(footLabel(null)).toBe("—");
    expect(positionLabel("midfielder")).toBe("Centrocampista");
    expect(positionLabel(null)).toBe("—");
  });

  it("computes the age only after the birthday", () => {
    expect(ageOn("2008-03-14", new Date(2026, 2, 13))).toBe(17);
    expect(ageOn("2008-03-14", new Date(2026, 2, 14))).toBe(18);
    expect(birthLabel("2008-03-14", new Date(2026, 9, 5))).toBe("14/03/2008 (18 años)");
    expect(birthLabel(null)).toBe("—");
  });

  it("shows kickoff dates in Spain", () => {
    expect(dateLabel("2026-08-22T22:30:00Z")).toBe("23 ago 2026");
    expect(dateLabel(null)).toBe("—");
  });

  it("derives a short code for crest fallbacks", () => {
    expect(crestCode("Montealto")).toBe("MON");
    expect(crestCode("Real Jaén")).toBe("RJ");
    expect(crestCode("Peñalbar")).toBe("PEN");
  });
});
