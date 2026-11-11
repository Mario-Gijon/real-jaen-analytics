import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError, getCompetitions, getSeasonMatches, getSeasonTeams, getSource, getTeamObservedMatches, getTeamObservedPerformance,
} from "@/client/api";
import type { ObservedCompetition, ObservedMatch, ObservedTeam, ObservedTeamMatches, ObservedTeamPerformance } from "@/contract";
import { MatchesView } from "@/features/matches/MatchesView";
import { mergeTeamMatches, teamOptions } from "@/features/matches/real-matches";
import { REAL_SOURCE } from "../shared/sources";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getSource: vi.fn(), getTeamObservedMatches: vi.fn(), getTeamObservedPerformance: vi.fn(),
  getCompetitions: vi.fn(), getSeasonMatches: vi.fn(), getSeasonTeams: vi.fn(),
}));

const jaen = { id: "team-real-jaen", name: "Real Jaén" };
const ibiza = { id: "sofascore-team-24368", name: "UD Ibiza" };
const alcorcon = { id: "sofascore-team-24322", name: "AD Alcorcón" };
const europa = { id: "sofascore-team-135672", name: "CE Europa" };
const match = (id: string, fields: Partial<ObservedMatch>): ObservedMatch => ({
  id, kickoff: "2026-08-30T17:15:00Z", status: "finished", competition: "Primera Federacion, Group 2",
  season: "26/27", round: 1, home: europa, away: jaen, score: { home: 0, away: 3 }, ...fields,
});
const GROUP_2: ObservedCompetition = { name: "Primera Federación, Grupo 2", season: "26/27" };
const real = (team: ObservedTeam, matches: ObservedMatch[], competitions = [GROUP_2]): ObservedTeamMatches => ({
  teamId: team.id, scope: "Partidos del equipo.", source: { synthetic: false, label: "Datos reales" }, competitions,
  matches: matches.map((m) => ({ match: m, side: m.home.id === team.id ? "home" : "away" })),
});
/** The real catalog: the analysable teams, in the source's order (not the selector's). */
const catalog = (teams = [ibiza, jaen, alcorcon]) => ({ teamId: jaen.id, teams, competitions: [], scope: "", source: REAL_SOURCE }) as unknown as ObservedTeamPerformance;

const finished = match("m1", {});
const goalless = match("m2", { kickoff: "2026-10-03T11:00:00Z", round: 6, score: { home: 0, away: 0 } });
const pending = match("m3", { kickoff: "2026-10-28T19:00:00Z", status: "scheduled", round: 9, home: jaen, away: ibiza, score: null });
const unknown = match("m5", { kickoff: "2026-11-01T10:00:00Z", status: null, score: null, round: 10 });
const undated = match("m4", { kickoff: null, status: "scheduled", round: 20, home: jaen, away: { id: "sofascore-team-4", name: null }, score: null });
const ibizaOnly = match("m6", { kickoff: "2026-09-12T17:00:00Z", round: 3, home: ibiza, away: alcorcon, score: { home: 1, away: 1 } });
const LISTS: Record<string, ObservedTeamMatches> = {
  [jaen.id]: real(jaen, [finished, goalless, pending, unknown, undated]),
  [ibiza.id]: real(ibiza, [ibizaOnly, pending]),
  [alcorcon.id]: real(alcorcon, [ibizaOnly]),
};

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/matches");
  vi.mocked(getSource).mockResolvedValue(REAL_SOURCE);
  vi.mocked(getTeamObservedPerformance).mockResolvedValue(catalog());
  vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) => LISTS[teamId] ?? real({ id: teamId, name: null }, []));
});

const list = () => screen.findByRole("list", { name: "Partidos reales" });
const rows = () => within(screen.getByRole("list", { name: "Partidos reales" })).getAllByRole("listitem");
const hrefs = () => within(screen.getByRole("list", { name: "Partidos reales" })).getAllByRole("link").map((l) => l.getAttribute("href"));
const destinations = (matches: readonly ObservedMatch[], matchHref: (id: string) => string) => matches.flatMap((m) => [
  `/teams?team=${m.home.id}`, matchHref(m.id), `/teams?team=${m.away.id}`,
]);
const teamSelect = () => screen.getByRole("combobox", { name: "Equipo" });
/** A row's link: its real detail, carrying the list's team and status to return to. */
const link = (id: string, context = "") => `/matches/${id}?from=matches${context}`;

describe("Partidos with the real source", () => {
  it("opens on Real Jaén with only Equipo and Estado above, no provenance block and no competition per row", async () => {
    render(<MatchesView />);
    await list();
    expect(getTeamObservedMatches).toHaveBeenCalledWith(jaen.id, expect.anything());
    expect(teamSelect()).toHaveValue(jaen.id);
    expect(screen.getAllByRole("combobox").map((c) => c.getAttribute("aria-label") ?? c.closest("label")!.firstChild!.textContent))
      .toEqual(["Equipo", "Estado"]);
    expect(screen.queryByText("Datos reales")).toBeNull();
    expect(screen.queryByText("Competición")).toBeNull();
    expect(screen.queryByText("Temporada")).toBeNull();
    expect(screen.queryByText("Primera Federacion, Group 2")).toBeNull();
    expect(within(rows()[0]).getByText("26/27 · J1")).toBeVisible();
    expect(rows()[0]).toHaveTextContent("0 – 3Finalizado");
    // A pending match shows its time, never 0–0; unknown states and dates are said so.
    expect(rows()[2]).toHaveTextContent(/\d\d:\d\dProgramado/);
    expect(rows()[3]).toHaveTextContent("Estado desconocido");
    expect(rows()[4]).toHaveTextContent("Fecha por definir");
    expect(hrefs()).toEqual(destinations([finished, goalless, pending, unknown, undated], (id) => link(id)));
    expect(screen.getByRole("list", { name: "Partidos reales" }).querySelectorAll("a a")).toHaveLength(0);
    expect(window.location.search).toBe("");
    for (const synthetic of [getCompetitions, getSeasonMatches, getSeasonTeams]) expect(synthetic).not.toHaveBeenCalled();
  });

  it("offers the real catalog, club first, then «Todos los equipos», and shows the chosen team's matches with the same status filter", async () => {
    const user = userEvent.setup();
    render(<MatchesView />);
    await list();
    expect([...teamSelect().querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Real Jaén", "AD Alcorcón", "UD Ibiza", "Todos los equipos"]);
    await user.selectOptions(screen.getByRole("combobox", { name: "Estado" }), "finished");
    await user.selectOptions(teamSelect(), ibiza.id);
    await vi.waitFor(() => expect(hrefs()).toHaveLength(3));
    expect(getTeamObservedMatches).toHaveBeenLastCalledWith(ibiza.id, expect.anything());
    expect(window.location.search).toBe(`?team=${ibiza.id}&state=finished`);
    // Status is kept: only Ibiza's finished match, opening with this team and status.
    expect(screen.getByRole("combobox", { name: "Estado" })).toHaveValue("finished");
    expect(hrefs()).toEqual(destinations([ibizaOnly], (id) => link(id, `&team=${ibiza.id}&state=finished`)));
  });

  it("lists every team's matches once with «Todos los equipos», asking for one team at a time", async () => {
    const order: string[] = [];
    let busy = 0;
    vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) => {
      expect(busy).toBe(0);
      busy++;
      order.push(teamId);
      await new Promise((resolve) => setTimeout(resolve, 5));
      busy--;
      return LISTS[teamId];
    });
    render(<MatchesView initialTeamId="all" />);
    await list();
    expect(teamSelect()).toHaveValue("all");
    expect(order).toEqual([jaen.id, alcorcon.id, ibiza.id]);
    // m3 (Jaén – Ibiza) and m6 (Ibiza – Alcorcón) appear in two lists, listed once; by kickoff, undated last.
    expect(hrefs()).toEqual(destinations([finished, ibizaOnly, goalless, pending, unknown, undated], (id) => link(id, "&team=all")));
    await userEvent.setup().selectOptions(screen.getByRole("combobox", { name: "Estado" }), "pending");
    expect(hrefs()).toEqual(destinations([pending, undated], (id) => link(id, "&team=all&state=pending")));
  });

  it("opens the team of the URL, and falls back to Real Jaén for one the catalog does not offer, saying so", async () => {
    const { unmount } = render(<MatchesView initialTeamId={ibiza.id} />);
    await list();
    expect(teamSelect()).toHaveValue(ibiza.id);
    expect(getTeamObservedMatches).toHaveBeenCalledWith(ibiza.id, expect.anything());
    unmount();
    vi.mocked(getTeamObservedMatches).mockClear();
    window.history.replaceState(null, "", "/matches?team=team-montealto");
    render(<MatchesView initialTeamId="team-montealto" />);
    expect(await screen.findByText("El equipo indicado no está disponible en la fuente real: se muestran los partidos del Real Jaén.")).toBeVisible();
    await list();
    expect(teamSelect()).toHaveValue(jaen.id);
    expect(getTeamObservedMatches).not.toHaveBeenCalledWith("team-montealto", expect.anything());
    expect(window.location.search).toBe("?team=team-montealto");
  });

  it("opens the status of the URL too, as a match page returns to it", async () => {
    render(<MatchesView initialTeamId="all" initialState="pending" />);
    await list();
    expect(screen.getByRole("combobox", { name: "Estado" })).toHaveValue("pending");
    expect(hrefs()).toEqual(destinations([pending, undated], (id) => link(id, "&team=all&state=pending")));
  });

  it("keeps to the current season of the scope: another season never shows, a match without a season name stays", async () => {
    vi.mocked(getTeamObservedMatches).mockResolvedValue(real(jaen, [finished, match("m7", { season: "25/26", kickoff: "2026-05-01T10:00:00Z" }),
      match("m8", { season: null, kickoff: "2026-09-20T10:00:00Z" })]));
    render(<MatchesView />);
    await list();
    const seasonless = match("m8", { season: null, kickoff: "2026-09-20T10:00:00Z" });
    expect(hrefs()).toEqual(destinations([finished, seasonless], (id) => link(id)));
  });

  it("brings the competition and season selectors back when the source serves more than one", async () => {
    const cup = match("m9", { competition: "Copa del Rey", kickoff: "2026-10-28T19:00:00Z" });
    vi.mocked(getTeamObservedMatches).mockResolvedValue(real(jaen, [finished, cup], [GROUP_2, { name: "Copa del Rey", season: "26/27" }]));
    const user = userEvent.setup();
    render(<MatchesView />);
    await list();
    await user.selectOptions(screen.getByRole("combobox", { name: "Competición" }), "Copa del Rey");
    expect(hrefs()).toEqual(destinations([cup], (id) => link(id)));
    expect(screen.getByRole("combobox", { name: "Temporada" })).toBeVisible();
  });

  it("says when the source has no matches, and shows failures with retry, never synthetic matches", async () => {
    vi.mocked(getTeamObservedMatches).mockResolvedValueOnce(real(jaen, []));
    const { unmount } = render(<MatchesView />);
    expect(await screen.findByText("La fuente no tiene partidos de este equipo.")).toBeVisible();
    unmount();
    vi.mocked(getTeamObservedMatches).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No"));
    render(<MatchesView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudieron cargar los partidos reales.");
    expect(getSeasonMatches).not.toHaveBeenCalled();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await list()).toBeVisible();
  });

  it("still shows the club's matches when the team list fails, with its own retry", async () => {
    vi.mocked(getTeamObservedPerformance).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No"));
    render(<MatchesView />);
    await list();
    expect(screen.getByRole("alert")).toHaveTextContent("No se pudo cargar la lista de equipos.");
    expect(teamSelect()).toBeDisabled();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("option", { name: "Todos los equipos" })).toBeInTheDocument();
  });

  it("says a synthetic season from the URL does not apply, without asking for real matches", async () => {
    render(<MatchesView initialSeasonId="season-demo-2026-27" initialTeamId="team-real-jaen" />);
    expect(await screen.findByText(/La temporada seleccionada no corresponde a la fuente real de partidos/)).toBeVisible();
    expect(getTeamObservedMatches).not.toHaveBeenCalled();
  });

  it("assumes nothing when the source of the section cannot be known", async () => {
    vi.mocked(getSource).mockRejectedValue(new Error("down"));
    render(<MatchesView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo determinar la fuente de los partidos.");
    expect(getTeamObservedMatches).not.toHaveBeenCalled();
    expect(getCompetitions).not.toHaveBeenCalled();
  });
});

describe("real match lists", () => {
  it("order the catalog club first, then by name", () => {
    expect(teamOptions([ibiza, alcorcon, jaen]).map((t) => t.id)).toEqual([jaen.id, alcorcon.id, ibiza.id]);
  });

  it("merge several teams' matches without repeating one, by kickoff, undated last", () => {
    const merged = mergeTeamMatches([LISTS[jaen.id], LISTS[ibiza.id], LISTS[alcorcon.id]]);
    expect(merged.matches.map((m) => m.id)).toEqual(["m1", "m6", "m2", "m3", "m5", "m4"]);
    expect(merged.competitions).toEqual([GROUP_2]);
    expect(mergeTeamMatches([])).toEqual({ matches: [], competitions: [] });
  });
});
