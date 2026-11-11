import { existsSync } from "node:fs";
import { join } from "node:path";
import { fireEvent, render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { crestUrlOf, observedCrest } from "@/lib/team-crests";
import { TeamCrest } from "@/ui/TeamCrest";

/** The 20 teams of Group 2, 26/27, by our public team id (as the real adapter maps them). */
const GROUP_2 = ["team-real-jaen", ...[24368, 24322, 4489, 24323, 24326, 43751, 217210, 135672, 24329, 2840, 2877, 24265, 263407,
  202074, 5069, 4487, 2815, 24363, 24338].map((id) => `sofascore-team-${id}`)];

describe("crests of real teams", () => {
  it("come from the local files of the registry, by our team id; any other team has none", () => {
    expect(crestUrlOf("team-real-jaen")).toBe("/crests/real-jaen.webp");
    expect(crestUrlOf("sofascore-team-24368")).toBe("/crests/ud-ibiza.webp");
    expect(crestUrlOf("sofascore-team-2877")).toBe("/crests/hercules-cf.webp");
    expect(crestUrlOf("sofascore-team-999999")).toBeNull();
    expect(observedCrest({ id: "team-real-jaen", name: "Real Jaén" }).crestUrl).toBe("/crests/real-jaen.webp");
    expect(observedCrest({ id: "sofascore-team-999999", name: "Equipo Sin Escudo" })).toMatchObject({ crestUrl: null, shortName: "Equipo Sin Escudo" });
  });

  it("give every Group 2 team its own file, served from public/ and never from the import folder", () => {
    const urls = GROUP_2.map(crestUrlOf);
    expect(urls.every((url) => url !== null)).toBe(true);
    expect(new Set(urls).size).toBe(GROUP_2.length);
    for (const url of urls) {
      expect(url).toMatch(/^\/crests\/[a-z0-9-]+\.webp$/);
      expect(existsSync(join(process.cwd(), "public", url!)), url!).toBe(true);
    }
  });

  it("draw the image, and the neutral shield without one or when it fails to load", () => {
    const { container, rerender } = render(<TeamCrest team={observedCrest({ id: "team-real-jaen", name: "Real Jaén" })} />);
    const image = container.querySelector('[data-crest="image"] img')!;
    expect(image).toHaveAttribute("src", "/crests/real-jaen.webp");
    fireEvent.error(image);
    expect(container.querySelector('[data-crest="fallback"]')).toHaveTextContent("RJ");
    rerender(<TeamCrest team={observedCrest({ id: "sofascore-team-999999", name: "Equipo Sin Escudo" })} />);
    expect(container.querySelector('[data-crest="image"]')).toBeNull();
    expect(container.querySelector('[data-crest="fallback"]')).toHaveTextContent("ESE");
  });
});
