import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  altoSeason,
  competitions,
  playerProfile,
  rjSeason,
  SEASON_ID,
  squad,
  standings,
} from "./fixtures.test-data";
import { TeamsView } from "@/features/teams/TeamsView";
import { DEMO_SOURCE } from "../shared/sources";


/** Answers the typed client's GETs from the fixtures, like `/api/v1` would. */
function serveApi() {
  const fetch = vi.fn(async (input: string) => {
    const url = new URL(input, "http://localhost");
    const path = url.pathname.replace("/api/v1", "");
    const body =
      path === "/source"
        ? DEMO_SOURCE
        : path === "/competitions"
        ? competitions
        : path === `/seasons/${SEASON_ID}/teams`
          ? standings
          : path === `/seasons/${SEASON_ID}/teams/team-real-jaen`
            ? rjSeason
            : path.startsWith(`/seasons/${SEASON_ID}/teams/`)
              ? altoSeason
              : path.startsWith("/players/")
                ? playerProfile(squad.find((row) => path.endsWith(row.player.id))!)
                : null;
    return body
      ? new Response(JSON.stringify(body))
      : new Response(JSON.stringify({ error: { code: "NOT_FOUND", message: "No" } }), {
          status: 404,
        });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

const requested = (fetch: ReturnType<typeof serveApi>) =>
  fetch.mock.calls.map((call) => String((call as unknown[])[0]));

let fetch: ReturnType<typeof serveApi>;
beforeEach(() => {
  fetch = serveApi();
  vi.stubGlobal("matchMedia", (query: string) => ({ matches: false, media: query }));
});
afterEach(() => vi.unstubAllGlobals());

async function openTeams() {
  render(<TeamsView />);
  await screen.findByRole("heading", { level: 2, name: "Real Jaén CF" });
  await screen.findByRole("heading", { level: 3, name: "Rendimiento" });
}

const grid = () => screen.getByRole("group", { name: "Equipo" });
const resultsTable = () => screen.getByRole("table", { name: "Partidos disputados" });

describe("Equipos", () => {
  it("offers only competition and season, and selects Real Jaén by default", async () => {
    await openTeams();
    expect(screen.getAllByRole("combobox").map((s) => s.closest("label")?.textContent)).toEqual([
      expect.stringContaining("Competición"),
      expect.stringContaining("Temporada"),
    ]);
    expect(within(grid()).getByRole("radio", { name: "Real Jaén CF" })).toBeChecked();
    expect(requested(fetch)).toContain(`/api/v1/seasons/${SEASON_ID}/teams/team-real-jaen`);
  });

  it("lists Real Jaén first and the rest alphabetically", async () => {
    await openTeams();
    expect(
      within(grid())
        .getAllByRole("radio")
        .map((r) => r.getAttribute("value")),
    ).toEqual(["team-real-jaen", "team-bravo", "team-alto", "team-vega"]);
  });

  it("changes team on click", async () => {
    const user = userEvent.setup();
    await openTeams();
    await user.click(within(grid()).getByText("Montealto"));
    expect(await screen.findByRole("heading", { level: 2, name: "CD Montealto" })).toBeVisible();
    expect(within(grid()).getByRole("radio", { name: "CD Montealto" })).toBeChecked();
  });

  it("keeps the chosen team, season and tab in the URL, replacing it without new history entries", async () => {
    window.history.replaceState(null, "", "/teams");
    const entries = window.history.length;
    const user = userEvent.setup();
    await openTeams();
    expect(window.location.search).toBe("");
    await user.click(within(grid()).getByText("Montealto"));
    await screen.findByRole("heading", { level: 2, name: "CD Montealto" });
    await user.click(screen.getByRole("tab", { name: "Jugadores" }));
    expect(window.location.search).toBe("?team=team-alto&tab=players");
    await user.selectOptions(screen.getByRole("combobox", { name: "Temporada" }), SEASON_ID);
    expect(window.location.search).toBe(`?season=${SEASON_ID}&team=team-alto&tab=players`);
    expect(window.history.length).toBe(entries);
  });

  it("moves through the grid with the arrow keys", async () => {
    const user = userEvent.setup();
    await openTeams();
    within(grid()).getByRole("radio", { name: "Real Jaén CF" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(within(grid()).getByRole("radio", { name: "Atlético Bravo" })).toBeChecked();
    expect(within(grid()).getByRole("radio", { name: "Atlético Bravo" })).toHaveFocus();
    expect(await screen.findByRole("heading", { level: 2, name: "Atlético Bravo" })).toBeVisible();
  });

  it("draws a neutral shield for clubs without crest, never another crest", async () => {
    await openTeams();
    const rj = within(grid()).getByRole("radio", { name: "Real Jaén CF" }).closest("label")!;
    const alto = within(grid()).getByRole("radio", { name: "CD Montealto" }).closest("label")!;
    expect(rj.querySelector('[data-crest="image"] img')).toHaveAttribute(
      "src",
      "/crests/real-jaen.webp",
    );
    expect(alto.querySelector('[data-crest="fallback"]')).toHaveTextContent("MON");
    expect(alto.querySelector("img")).toBeNull();
  });

  it("switches between the two tabs, also with the keyboard", async () => {
    const user = userEvent.setup();
    await openTeams();
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((t) => t.textContent)).toEqual(["Rendimiento del equipo", "Jugadores"]);
    await user.click(screen.getByRole("tab", { name: "Jugadores" }));
    expect(screen.getByRole("tabpanel")).toHaveAccessibleName("Jugadores");
    expect(await screen.findByRole("table", { name: "Jugadores de la plantilla" })).toBeVisible();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Rendimiento del equipo" })).toHaveFocus();
    expect(screen.getByRole("heading", { level: 3, name: "Temporada" })).toBeVisible();
  });
});

describe("Rendimiento del equipo", () => {
  const zone = (name: string) => screen.getByRole("region", { name });
  /** The text of one indicator (value, label and any comparison). */
  const indicator = (name: string) =>
    zone("Rendimiento").querySelector(`[data-indicator="${name}"]`)!.closest("div")!;

  it("shows three zones in order: Temporada, Rendimiento and Partidos", async () => {
    await openTeams();
    const zones = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(zones).toEqual(["Temporada", "Rendimiento", "Partidos"]);
  });

  it("TEMPORADA: position, points, balance and home/away from finished matches only", async () => {
    await openTeams();
    const season = zone("Temporada");
    const value = (term: string) =>
      within(season).getByText(term, { selector: "dt, abbr" }).closest("div")!.querySelector("dd");
    expect(value("Clasificación")).toHaveTextContent("2.º");
    expect(value("Puntos")).toHaveTextContent("5");
    expect(
      within(season).getByTitle("Partidos jugados").closest("div")!.querySelector("dd"),
    ).toHaveTextContent("4");
    expect(
      within(season).getByTitle("Goles a favor").closest("div")!.querySelector("dd"),
    ).toHaveTextContent("4");
    expect(
      within(season).getByTitle("Goles en contra").closest("div")!.querySelector("dd"),
    ).toHaveTextContent("4");
    // Postponed (away) and scheduled (home) matches are not counted.
    expect(value("Local")).toHaveTextContent(/^1V0E1D$/);
    expect(value("Visitante")).toHaveTextContent(/^0V2E0D$/);
  });

  it("TEMPORADA: one result per finished match, with round, rival, venue and score", async () => {
    const user = userEvent.setup();
    await openTeams();
    const chips = within(screen.getByRole("list", { name: "Resultados por jornada" })).getAllByRole(
      "button",
    );
    // Only the letter on each chip; the round lives in its label and in the detail line.
    expect(chips.map((c) => c.textContent)).toEqual(["V", "E", "D", "E"]);
    expect(chips[2]).toHaveAccessibleName("Jornada 3: Derrota 1-2 en casa contra Atlético Bravo");
    await user.click(chips[1]);
    expect(chips[1]).toHaveAttribute("aria-pressed", "true");
    const detail = screen.getAllByRole("link", { name: "Ver equipo UD Vegaclara" })
      .find(link => link.closest("p")?.textContent?.includes("J2"))!.closest("p")!;
    for (const text of ["J2", "Visitante", "UD Vegaclara", "E 1-1"])
      expect(detail).toHaveTextContent(text);
  });

  it("RENDIMIENTO · Temporada: per-match attack, defence and context over all finished matches", async () => {
    await openTeams();
    expect(screen.getByRole("button", { name: "Temporada" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // The header holds only the title and the period selector, no match count.
    expect(
      screen.getByRole("heading", { level: 3, name: "Rendimiento" }).parentElement,
    ).toHaveTextContent(/^RendimientoTemporadaÚltimos 3$/);
    const expected: [string, string][] = [
      ["goals", "1,00"],
      ["xg", "1,13*"],
      ["shots", "11,0"],
      ["shotsOnTarget", "4,5"],
      ["goalsAgainst", "1,00"],
      ["xgAgainst", "1,20*"],
      ["shotsAgainst", "9,5"],
      ["shotsOnTargetAgainst", "4,0"],
      ["possession", "53,0 %"],
      ["corners", "5,0"],
    ];
    for (const [name, text] of expected) expect(indicator(name), name).toHaveTextContent(text);
    expect(zone("Rendimiento")).not.toHaveTextContent("vs temporada");
    expect(zone("Rendimiento")).not.toHaveTextContent(/Faltas|Amarillas|Rojas|Tarjetas/);
    expect(within(indicator("xg")).getByTitle("Media de 3 de 4 partidos")).toBeInTheDocument();
    expect(zone("Rendimiento")).toHaveTextContent("Media solo de los partidos con dato.");
  });

  it("RENDIMIENTO · Últimos 3: the three latest finished matches, compared with the season", async () => {
    const user = userEvent.setup();
    await openTeams();
    await user.click(screen.getByRole("button", { name: "Últimos 3" }));
    expect(screen.getByRole("button", { name: "Últimos 3" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(zone("Rendimiento")).not.toHaveTextContent("Jornadas");
    expect(indicator("goals")).toHaveTextContent("0,67");
    expect(indicator("goals")).toHaveTextContent("−0,33 vs temporada");
    expect(indicator("goalsAgainst")).toHaveTextContent("±0,00 vs temporada");
    expect(indicator("shotsAgainst")).toHaveTextContent("10,0");
    expect(indicator("shotsAgainst")).toHaveTextContent("+0,5 vs temporada");
    // xG: 2 of 3 matches, and the comparison is flagged as partial.
    expect(indicator("xg")).toHaveTextContent("0,95*");
    expect(indicator("xg")).toHaveTextContent("−0,18 vs temporada*");
    expect(
      within(indicator("xg")).getByTitle("Comparación con datos parciales"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Temporada" }));
    expect(indicator("goals")).toHaveTextContent("1,00");
    expect(zone("Rendimiento")).not.toHaveTextContent("vs temporada");
  });

  it("PARTIDOS: full history of finished matches; Últimos 3 only highlights its three", async () => {
    const user = userEvent.setup();
    await openTeams();
    const rows = () => within(resultsTable()).getAllByRole("row").slice(1);
    const highlighted = () =>
      rows()
        .filter((r) => r.hasAttribute("data-in-period"))
        .map((r) => r.getAttribute("data-match-id"));
    expect(rows().map((r) => r.getAttribute("data-match-id"))).toEqual([
      "m-6",
      "m-3",
      "m-2",
      "m-1",
    ]);
    expect(highlighted()).toEqual([]);
    for (const row of rows()) {
      expect(within(row).getByRole("link", { name: /^Ver equipo/ })).toHaveAttribute("href", expect.stringMatching(/^\/teams\?season=/));
      expect(within(row).getByRole("link", { name: /^Ver partido/ })).toHaveAttribute("href", `/matches/${row.getAttribute("data-match-id")}?from=teams&season=${SEASON_ID}&team=team-real-jaen`);
    }
    expect(rows()[1]).toHaveTextContent("D 1-2");
    expect(rows()[1]).toHaveTextContent("Local");
    expect(rows()[0]).toHaveTextContent("Visitante");
    expect(within(rows()[1]).getByLabelText("Local")).toHaveAccessibleName("Local");
    expect(within(rows()[0]).getByLabelText("Visitante")).toHaveAccessibleName("Visitante");
    expect(rows()[1].querySelector(".rival a [data-crest]")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Últimos 3" }));
    expect(rows()).toHaveLength(4);
    expect(highlighted()).toEqual(["m-6", "m-3", "m-2"]);
    for (const row of rows()) expect(within(row).getByRole("link", { name: /^Ver partido/ })).toHaveAttribute("href", `/matches/${row.getAttribute("data-match-id")}?from=teams&season=${SEASON_ID}&team=team-real-jaen`);
    expect(rows()[0]).toHaveTextContent("(en «Últimos 3»)");
    expect(zone("Partidos")).not.toHaveTextContent("Partidos de «Últimos 3»");
  });
});

describe("Jugadores", () => {
  async function openPlayers() {
    const user = userEvent.setup();
    await openTeams();
    await user.click(screen.getByRole("tab", { name: "Jugadores" }));
    await screen.findByRole("table", { name: "Jugadores de la plantilla" });
    return user;
  }

  it("are the demonstration's squad, never asking for real players", async () => {
    await openPlayers();
    expect(requested(fetch).filter((url) => /\/teams\/[^/]+\/(players|observed-)/.test(url))).toEqual([]);
  });
  const names = () =>
    within(screen.getByRole("table", { name: "Jugadores de la plantilla" }))
      .getAllByRole("link", { name: /Ver ficha de/ })
      .map((b) => b.getAttribute("aria-label")!.replace("Ver ficha de ", ""));
  const row = (name: string) =>
    screen.getByRole("link", { name: `Ver ficha de ${name}` }).closest("tr")!;

  it("lists every squad player by line and shirt number, including those without minutes", async () => {
    await openPlayers();
    expect(names()).toEqual([
      "Ángel Portero",
      "Tomás Reserva",
      "Lucas Defensa",
      "Mario Medio",
      "Iker Cantera",
      "Diego Goleador",
    ]);
    expect(Array.from(row("Tom\u00e1s Reserva").querySelectorAll("td")).slice(1, 6).map(cell => cell.textContent)).toEqual(["0", "0", "0", "0", "0"]);
  });

  it("shows a real 0 as 0 and a missing total as «—»", async () => {
    await openPlayers();
    const cells = row("Iker Cantera").querySelectorAll("td");
    expect(cells[4]).toHaveTextContent("0"); // goals
    expect(cells[5]).toHaveTextContent("—"); // assists, not available
  });

  it("sorts by a column and filters by position", async () => {
    const user = await openPlayers();
    const goals = screen.getByRole("columnheader", { name: /^Goles/ });
    await user.click(within(goals).getByRole("button"));
    expect(names()[0]).toBe("Diego Goleador");
    expect(goals).toHaveAttribute("aria-sort", "descending");
    await user.click(within(goals).getByRole("button"));
    expect(goals).toHaveAttribute("aria-sort", "ascending");
    expect(names().at(-1)).toBe("Diego Goleador");

    await user.click(screen.getByRole("button", { name: /Porteros/ }));
    expect(names()).toEqual(["Ángel Portero", "Tomás Reserva"]);
    expect(screen.getByRole("button", { name: /Porteros/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: /Todos/ }));
    expect(names()).toHaveLength(6);
  });

  it("links directly to the player with season and team context, without a panel", async () => {
    await openPlayers();
    const link = screen.getByRole("link", { name: "Ver ficha de Mario Medio" });
    expect(link).toHaveAttribute("href", `/players/p-mid?season=${SEASON_ID}&from=team&origin=team-real-jaen`);
    link.focus();
    expect(link).toHaveFocus();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("link", { name: /Ver ficha completa/ })).toBeNull();
    await waitFor(() => expect(row("Mario Medio").querySelectorAll("td")[6]).toHaveTextContent("181 cm"));
    expect(row("Mario Medio").querySelectorAll("td")[7]).toHaveTextContent("Izquierda");
  });

  it("shows missing personal data as dashes while preserving real statistics", async () => {
    await openPlayers();
    await waitFor(() => expect(row("Lucas Defensa").querySelectorAll("td")[6]).toHaveTextContent("\u2014"));
    expect(row("Lucas Defensa").querySelectorAll("td")[7]).toHaveTextContent("\u2014");
    expect(row("Lucas Defensa").querySelectorAll("td")[5]).toHaveTextContent("1");
  });

  it("restores the originating team, season and players tab", async () => {
    render(<TeamsView initialTeamId="team-alto" initialSeasonId={SEASON_ID} initialTab="players" />);
    expect(await screen.findByRole("heading", { level: 2, name: "CD Montealto" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");
    expect(requested(fetch)).toContain(`/api/v1/seasons/${SEASON_ID}/teams/team-alto`);
  });
});
