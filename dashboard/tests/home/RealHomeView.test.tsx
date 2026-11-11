import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError, getCompetitions, getObservedMatchDetail, getSeasonMatches, getSource, getTeamObservedMatches,
  getTeamObservedPerformance, getTeamSeason,
} from "@/client/api";
import { HomeView } from "@/features/home/HomeView";
import {
  afterTarget, clubMatches, jaen, later, next, NOW, observed, other, postponed, rival, rivalMatches, rivalPerformance,
  rivalPlayed, target,
} from "./real-fixtures";
import { DEMO_SOURCE, REAL_SOURCE } from "../shared/sources";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getSource: vi.fn(), getTeamObservedMatches: vi.fn(), getTeamObservedPerformance: vi.fn(), getObservedMatchDetail: vi.fn(),
  getCompetitions: vi.fn(), getSeasonMatches: vi.fn(), getTeamSeason: vi.fn(),
}));

const algecirasPlayed = observed(201, "2026-10-04T17:00:00Z", "finished", later, other, 6, { home: 0, away: 1 });

beforeEach(() => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/");
  vi.mocked(getSource).mockResolvedValue(REAL_SOURCE);
  vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) =>
    teamId === jaen.id ? clubMatches() : teamId === later.id ? rivalMatches([algecirasPlayed, next], later) : rivalMatches());
  vi.mocked(getTeamObservedPerformance).mockImplementation(async (teamId) =>
    teamId === later.id ? rivalPerformance([algecirasPlayed], later) : rivalPerformance([...rivalPlayed, afterTarget]));
});

// Several chained loads (source, calendar, rival, details): more margin than the default under a parallel run.
const WAIT = { timeout: 4000 };
const recent = () => screen.findByRole("list", { name: "Últimos partidos del rival" }, WAIT);
const summary = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling!;

describe("Inicio con la fuente real", () => {
  it("prepares the club's next current match: its rival, its five previous matches and their summary, loading nothing synthetic", async () => {
    render(<HomeView now={NOW} />);
    const list = await recent();
    expect(screen.queryByRole("heading", { level: 1 })).toBeNull();
    const header = screen.getByRole("region", { name: "Encuentro de preparación" });
    expect(within(header).getByText("Jornada 9 · 25 oct 2026 · Primera Federación, Grupo 2 · 26/27")).toBeVisible();
    expect(within(header).getByRole("link", { name: "Ver equipo Atlético Madrileño" })).toHaveAttribute("href", "/teams?team=sofascore-team-24326");
    expect(within(header).getByRole("link", { name: "Ver equipo Real Jaén" })).toHaveAttribute("href", "/teams?team=team-real-jaen");
    expect(screen.getByRole("link", { name: "Analizar Atlético Madrileño" })).toHaveAttribute("href", "/teams?team=sofascore-team-24326");
    // Inicio carries no provenance block of its own.
    expect(screen.queryByText("Datos reales")).toBeNull();
    // By date, not by round; the target and the match after it are left out.
    expect(within(list).getAllByRole("listitem").flatMap((row) => within(row).getAllByRole("link").map((l) => l.getAttribute("href")))).toEqual(
      [
        ["sofascore-team-2840", "107", "sofascore-team-24326"],
        ["sofascore-team-24326", "108", "sofascore-team-2840"],
        ["sofascore-team-2840", "104", "sofascore-team-24326"],
        ["sofascore-team-24326", "103", "sofascore-team-2840"],
        ["sofascore-team-2840", "102", "sofascore-team-24326"],
      ].flatMap(([home, id, away]) => [
        `/teams?team=${home}`, `/matches/sofascore-match-${id}?from=home&match=sofascore-match-9`, `/teams?team=${away}`,
      ]));
    expect(screen.getByRole("heading", { level: 2, name: "Últimos 5 partidos del Atlético Madrileño" })).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Últimos 5 partidos del Atlético Madrileño" })).getByRole("link", { name: "Ver todos" }))
      .toHaveAttribute("href", "/matches?team=sofascore-team-24326");
    expect(summary("Forma")).toHaveTextContent(/0\s*V\s*3\s*E\s*2\s*D/);
    expect(summary("Goles")).toHaveTextContent(/9\s*GF\s*11\s*GC/);
    // xG oriented to the rival's side in each match: (0.39 + 2.15 + 1.74 + 1.32 + 1.27) / 5 = 1.374 and (1.56 + 3.13 + 2.33 + 3.81 + 0) / 5.
    expect(await screen.findByText("1,37", {}, WAIT)).toBeVisible();
    expect(screen.getByText("2,17")).toBeVisible();
    // From the analysed rival's side: its own xG, and its opponents'.
    expect(summary("Expectativa")).toHaveTextContent(/1,37\s*xG a favor\s*2,17\s*xG en contra/);
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
    expect(screen.queryByText("Cargando xG…")).toBeNull();
    expect(getCompetitions).not.toHaveBeenCalled();
    expect(getSeasonMatches).not.toHaveBeenCalled();
    expect(getTeamSeason).not.toHaveBeenCalled();
  });

  it("recalculates the rival and its history against a chosen target's date, without later matches", async () => {
    const user = userEvent.setup();
    const view = render(<HomeView now={NOW} />);
    await recent();
    await user.click(screen.getByRole("button", { name: "Cambiar rival" }));
    const options = within(screen.getByRole("region", { name: "Próximos encuentros del Real Jaén" })).getAllByRole("button");
    expect(options.map((o) => o.textContent)).toEqual([expect.stringContaining("Jornada 9 · Atlético Madrileño"), expect.stringContaining("Jornada 10 · Algeciras CF")]);
    await user.click(options[1]);
    expect(push).toHaveBeenCalledWith("/?match=sofascore-match-10", { scroll: false });
    view.rerender(<HomeView now={NOW} matchId="sofascore-match-10" />);
    expect(await screen.findByRole("link", { name: "Analizar Algeciras CF" }, WAIT)).toBeVisible();
    const list = await recent();
    expect(within(list).getAllByRole("link").map((l) => l.getAttribute("href")))
      .toEqual(["/teams?team=sofascore-team-4489", "/matches/sofascore-match-201?from=home&match=sofascore-match-10", "/teams?team=sofascore-team-2840"]);
    expect(screen.getByRole("region", { name: "Último partido del Algeciras CF" })).toBeVisible();
    expect(screen.getByText(/1 de 5: son todos los partidos finalizados del rival antes del encuentro/)).toBeVisible();
    expect(summary("Expectativa")).toHaveTextContent(/—\s*xG a favor\s*—\s*xG en contra/);
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
  });

  it("never proposes a postponed match, which is said to be still pending without a confirmed date", async () => {
    vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) =>
      teamId === jaen.id ? clubMatches([postponed, target, next]) : rivalMatches());
    render(<HomeView now={NOW} />);
    await recent();
    expect(screen.getByText("J5 · Gimnàstic de Tarragona: aplazado, pendiente de disputar sin nueva fecha confirmada. No se propone para preparar.")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Encuentro de preparación" })).getByText(/^Jornada 9 ·/)).toBeVisible();
  });

  it("tells the rival's matches still to be played before the target from overdue ones, and says unknown statuses without guessing them", async () => {
    const unknownClub = observed(11, "2026-10-24T11:00:00Z", null, jaen, other, 11);
    vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) => teamId === jaen.id ? clubMatches([unknownClub, target, next]) : rivalMatches([
      ...rivalPlayed, observed(112, "2026-10-24T17:00:00Z", "scheduled", rival, other, 12),
      observed(113, "2026-10-20T17:00:00Z", null, other, rival, 13), target]));
    render(<HomeView now={NOW} />);
    await recent();
    expect(screen.getByText("Jornada 11 · 24 oct 2026 · Gimnàstic de Tarragona: estado desconocido en la fuente. No se considera para elegir el próximo encuentro.")).toBeVisible();
    expect(within(screen.getByRole("region", { name: "Encuentro de preparación" })).getByText(/^Jornada 9 ·/)).toBeVisible();
    expect(screen.getByText("1 partido del rival previsto antes del encuentro todavía no se ha disputado.")).toBeVisible();
    expect(screen.getByText("1 partido del rival con estado desconocido en la fuente no se considera: no se sabe si se disputó.")).toBeVisible();
    expect(screen.queryByText(/con fecha ya pasada/)).toBeNull();
  });

  it("says when the next scheduled match's date has passed, and when there is no next match", async () => {
    const stale = observed(8, "2026-10-18T16:15:00Z", "scheduled", jaen, other, 8);
    vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) => teamId === jaen.id ? clubMatches([stale]) : rivalMatches());
    render(<HomeView now={NOW} />);
    expect(await screen.findByText("No hay próximos encuentros vigentes del Real Jaén con fecha confirmada en los datos importados.")).toBeVisible();
    expect(screen.getByText(/Jornada 8 · 18 oct 2026 · Gimnàstic de Tarragona: figura como programado aunque su fecha ya ha pasado/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Cambiar rival" })).toBeDisabled();
    expect(getTeamObservedPerformance).not.toHaveBeenCalled();
  });

  it("does not analyse a rival without an export of its own, nor stand in for it with other data", async () => {
    vi.mocked(getTeamObservedPerformance).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
    render(<HomeView now={NOW} />);
    expect(await screen.findByText("Este rival no tiene exportación propia en la fuente real: no se muestran sus últimos partidos.", {}, WAIT)).toBeVisible();
    expect(screen.queryByRole("list", { name: "Últimos partidos del rival" })).toBeNull();
    expect(screen.queryByRole("link", { name: /^Analizar/ })).toBeNull();
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
  });

  it("tells a failure of the rival's data apart, with a retry", async () => {
    vi.mocked(getTeamObservedMatches).mockImplementationOnce(async () => clubMatches())
      .mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No"));
    render(<HomeView now={NOW} />);
    expect(await screen.findByText("No se pudieron cargar los partidos del rival.", {}, WAIT)).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await recent()).toBeVisible();
  });

  it("shows fewer matches and what is left out, with missing xG distinct from a real zero and partial coverage", async () => {
    const uncounted = rivalPlayed.slice(3);
    vi.mocked(getTeamObservedMatches).mockImplementation(async (teamId) => teamId === jaen.id ? clubMatches() : rivalMatches(
      [...uncounted, observed(109, "2026-10-21T17:00:00Z", "postponed", rival, other, 9), target]));
    const performance = rivalPerformance(uncounted.filter((m) => m.id !== "sofascore-match-108"));
    const countedResults = performance.competitions[0].results;
    const away = countedResults.find(result => result.match.id === "sofascore-match-107")!;
    const homeResult = countedResults.find(result => result.match.id === "sofascore-match-104")!;
    away.xgFor = null;
    away.xgAgainst = 0;
    homeResult.xgFor = null;
    homeResult.xgAgainst = null;
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance);
    render(<HomeView now={NOW} />);
    const list = await recent();
    expect(screen.getByRole("region", { name: "Últimos 3 partidos del Atlético Madrileño" })).toBeVisible();
    expect(screen.getByText(/^3 de 5: son todos/)).toBeVisible();
    expect(screen.getByText("1 partido sin resultado contabilizado (marcador no válido o versiones contradictorias) queda fuera de la síntesis.")).toBeVisible();
    expect(screen.getByText("1 partido del rival con fecha ya pasada sigue sin resultado en los datos importados (aplazado o sin actualizar) y no se incluye.")).toBeVisible();
    expect(screen.queryByText(/todavía no se ha/)).toBeNull();
    expect(within(list).getByRole("link", { name: /resultado no contabilizado/ })).toHaveTextContent("—");
    expect(summary("Forma")).toHaveTextContent(/0\s*V\s*0\s*E\s*2\s*D/);
    // The away rival's xG is absent; the opponent's xG is a true zero for only one of two counted matches.
    expect(await screen.findByTitle("Dato no disponible", {}, WAIT)).toHaveTextContent("—");
    expect(screen.getByTitle("Datos disponibles en 1 de 2 partidos")).toHaveTextContent("0,00");
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
  });

  it("keeps the existing retry for failed performance data", async () => {
    vi.mocked(getTeamObservedPerformance).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No"));
    render(<HomeView now={NOW} />);
    expect(await screen.findByText("No se pudieron cargar los partidos del rival.", {}, WAIT)).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("1,37", {}, WAIT)).toBeVisible();
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
  });

  it("says a synthetic season in the URL does not belong to the real source, and the demonstration never asks the real one", async () => {
    const { unmount } = render(<HomeView now={NOW} seasonId="season-demo-2026-27" matchId="sofascore-match-9" />);
    expect(await screen.findByText(/La temporada indicada no corresponde a la fuente real/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Ver la preparación con datos reales" })).toHaveAttribute("href", "/");
    expect(getTeamObservedMatches).not.toHaveBeenCalled();
    unmount();
    vi.mocked(getSource).mockResolvedValue(DEMO_SOURCE);
    vi.mocked(getCompetitions).mockResolvedValue([]);
    render(<HomeView now={NOW} />);
    await vi.waitFor(() => expect(getCompetitions).toHaveBeenCalled());
    expect(getTeamObservedMatches).not.toHaveBeenCalled();
    expect(getTeamObservedPerformance).not.toHaveBeenCalled();
  });
});
