import { describe, expect, it } from "vitest";
import { countryLabel } from "@/features/players/country-label";

describe("profile country labels", () => {
  it.each([["Spain", "España"], ["ES", "España"], ["Senegal", "Senegal"], ["Morocco", "Marruecos"],
    ["España", "España"], ["Unknown source label", "Unknown source label"], [null, "—"]])("displays %s as %s", (value, expected) => {
    expect(countryLabel(value)).toBe(expected);
  });
});
