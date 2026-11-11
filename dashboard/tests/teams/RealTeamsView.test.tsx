import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError, getCompetitions, getObservedPlayers, getObservedSquad, getObservedTeam, getPlayer, getSeasonTeams, getSource,
  getTeamObservedPerformance, getTeamSeason,
} from "@/client/api";
import {
  TEAM_OUTPUT_INDICATORS, type ObservedCompetitionPerformance, type ObservedMatch, type ObservedSquad, type ObservedSquadCompetition,
  type ObservedSquadPlayer, type ObservedTeamPerformance, type SeasonStat, type TeamOutputIndicator,
} from "@/contract";
import { TeamsView } from "@/features/teams/TeamsView";
import { PlayedZone, type Played } from "@/features/teams/TeamPerformanceParts";
import { DEMO_SOURCE, REAL_SOURCE } from "../shared/sources";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getSource: vi.fn(), getTeamObservedPerformance: vi.fn(), getObservedPlayers: vi.fn(), getObservedSquad: vi.fn(),
  getCompetitions: vi.fn(), getSeasonTeams: vi.fn(), getTeamSeason: vi.fn(), getPlayer: vi.fn(), getObservedTeam: vi.fn(),
}));

const jaen = { id: "team-real-jaen", name: "Real Jaén" };
const match = (id: string, round: number, home: { id: string; name: string | null }, away: { id: string; name: string | null },
  score: [number, number]): ObservedMatch => ({
  id, kickoff: `2026-09-0${round}T17:00:00Z`, status: "finished", competition: "Primera Federacion, Group 2", season: "26/27",
  round, home, away, score: { home: score[0], away: score[1] },
});
const europa = { id: "sofascore-team-135672", name: "CE Europa" };
const huesca = { id: "sofascore-team-24265", name: "Huesca" };
const GROUP_2 = "sofascore-scope-94191-97382";
const stat = (value: number | null, matches: number, of: number): SeasonStat => value === null
  ? { value: null, coverage: "none", reason: "not_available", matches: 0, of }
  : { value, coverage: matches === of ? "complete" : "partial", matches, of };
const averages = (of: number, values: Partial<Record<TeamOutputIndicator, SeasonStat>>) =>
  Object.fromEntries(TEAM_OUTPUT_INDICATORS.map((key) => [key, values[key] ?? stat(null, 0, of)])) as Record<TeamOutputIndicator, SeasonStat>;
/** As computed by the source: shots on target missing everywhere, xG and possession in two of three matches. */
const OUTPUT: ObservedCompetitionPerformance["output"] = {
  season: averages(3, { goals: stat(2, 3, 3), goalsAgainst: stat(2 / 3, 3, 3), xg: stat(1.4, 2, 3), xgAgainst: stat(0.95, 2, 3),
    shots: stat(12, 3, 3), shotsAgainst: stat(0, 3, 3), possession: stat(52.5, 2, 3), corners: stat(4, 3, 3) }),
  last3: { matchIds: ["sofascore-match-2", "sofascore-match-3"], values: averages(2, { goals: stat(1.5, 2, 2), goalsAgainst: stat(1, 2, 2),
    xg: stat(1.15, 2, 2), shots: stat(10, 2, 2), shotsAgainst: stat(0, 2, 2), possession: stat(50, 2, 2), corners: stat(3, 2, 2) }) },
  undated: 0,
};
const competition = (fields: Partial<ObservedCompetitionPerformance> = {}): ObservedCompetitionPerformance => ({
  id: GROUP_2, name: "Primera Federación, Grupo 2", season: "26/27",
  matches: { inScope: 37, finished: 3, pending: 29, counted: 3, finishedWithoutValidScore: 0, excludedByVersionConflict: 0, unknownStatus: 0, replacedLocally: 0 },
  record: { played: 3, wins: 2, draws: 1, losses: 0, goalsFor: 6, goalsAgainst: 2, goalDifference: 4 },
  venues: { home: { win: 1, draw: 1, loss: 0 }, away: { win: 1, draw: 0, loss: 0 } },
  results: [
    { match: match("sofascore-match-1", 1, europa, jaen, [0, 3]), side: "away", result: "win", goalsFor: 3, goalsAgainst: 0, xgFor: null, xgAgainst: null },
    { match: match("sofascore-match-2", 2, jaen, huesca, [2, 2]), side: "home", result: "draw", goalsFor: 2, goalsAgainst: 2, xgFor: null, xgAgainst: null },
    { match: match("sofascore-match-3", 3, jaen, europa, [1, 0]), side: "home", result: "win", goalsFor: 1, goalsAgainst: 0, xgFor: null, xgAgainst: null },
  ],
  output: OUTPUT,
  ...fields,
});
const SCOPE = "Resultados de los partidos finalizados observados en esta competición. No es necesariamente la temporada completa: sin clasificación ni puntos.";
const performance = (competitions = [competition()]): ObservedTeamPerformance => ({
  teamId: "team-real-jaen", competitions, teams: [jaen], scope: SCOPE, source: { synthetic: false, label: "Datos reales" },
});
/**
 * Observed players as the service accumulates them: a starter, a substitute with one call-up it could not
 * resolve, one unused in every call-up, a complete 0 and a player with nothing resolved.
 */
const squadPlayer = (id: number, name: string, position: ObservedSquadPlayer["player"]["position"], callUps: number,
  stats: ObservedSquadPlayer["stats"], fields: Partial<ObservedSquadPlayer> = {}): ObservedSquadPlayer => ({
  player: { id: `sofascore-player-${id}`, name, position }, heightCm: null, preferredFoot: null, lastShirtNumber: null, callUps, stats, ...fields,
});
const PLAYERS = [
  squadPlayer(942663, "Marco Siverio", "forward", 7, { appearances: stat(7, 7, 7), starts: stat(6, 7, 7), minutes: stat(504, 7, 7),
    goals: stat(6, 7, 7), assists: stat(0, 7, 7) }, { heightCm: 184, preferredFoot: "right", lastShirtNumber: 9 }),
  squadPlayer(1982588, "Francisco Gallego", "midfielder", 8, { appearances: stat(5, 7, 8), starts: stat(0, 8, 8),
    minutes: stat(70, 7, 8), goals: stat(0, 7, 8), assists: stat(0, 7, 8) }, { lastShirtNumber: 7 }),
  squadPlayer(1086428, "Salvi", "goalkeeper", 8, { appearances: stat(0, 8, 8), starts: stat(0, 8, 8), minutes: stat(0, 8, 8),
    goals: stat(0, 8, 8), assists: stat(0, 8, 8) }, { heightCm: 190, lastShirtNumber: 13 }),
  squadPlayer(966303, "Moha Keita", "midfielder", 6, { appearances: stat(6, 6, 6), starts: stat(4, 6, 6), minutes: stat(365, 6, 6),
    goals: stat(0, 6, 6), assists: stat(1, 6, 6) }),
  squadPlayer(5, "Jugador Sin Resolver", "defender", 1, { appearances: stat(null, 0, 1), starts: stat(0, 1, 1), minutes: stat(null, 0, 1),
    goals: stat(null, 0, 1), assists: stat(null, 0, 1) }),
];
const squadCompetition = (fields: Partial<ObservedSquadCompetition> = {}): ObservedSquadCompetition => ({
  id: GROUP_2, name: "Primera Federación, Grupo 2", season: "26/27", matches: { counted: 8, withCallUps: 8 },
  callUps: { total: 30, played: 18, unused: 10, unknown: 2, contradictions: 0, mismatches: 0, teamWarnings: 0, duplicateRecords: 0, discardedRecords: 0 }, players: PLAYERS, ...fields,
});
const SQUAD_SCOPE = "Jugadores observados en las convocatorias de los partidos finalizados contados en esta competición.";
const squad = (competitions = [squadCompetition()]): ObservedSquad => ({
  teamId: "team-real-jaen", competitions, scope: SQUAD_SCOPE, source: { synthetic: false, label: "Datos reales" },
});
const ibiza = { id: "sofascore-team-24368", name: "UD Ibiza" };
const real = () => vi.mocked(getSource).mockResolvedValue(REAL_SOURCE);

beforeEach(() => {
  vi.resetAllMocks();
  real();
  vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance());
  vi.mocked(getObservedTeam).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
});

const loaded = () => screen.findByRole("heading", { level: 2, name: "Real Jaén" });
const item = (short: string) => within(screen.getByLabelText("Balance")).getByTitle(new RegExp(`^${{
  PJ: "Partidos jugados", V: "Victorias", E: "Empates", D: "Derrotas", GF: "Goles a favor", GC: "Goles en contra", DG: "Diferencia de goles",
}[short]}$`)).closest("div")!;

describe("Equipos con la fuente real", () => {
  it("keeps the current scope internally without its controls, subtitle or provenance block", async () => {
    render(<TeamsView />);
    await loaded();
    expect(getTeamObservedPerformance).toHaveBeenCalledWith("team-real-jaen", expect.anything());
    expect(screen.queryByRole("combobox", { name: "Competición" })).toBeNull();
    expect(screen.queryByText("Competición")).toBeNull();
    expect(screen.queryByRole("combobox", { name: "Temporada" })).toBeNull();
    expect(screen.queryByText("Primera Federación, Grupo 2 · 26/27")).toBeNull();
    expect(screen.queryByText("Datos reales")).toBeNull();
    expect(screen.queryByText(SCOPE)).toBeNull();
    expect(screen.getByRole("button", { name: "Temporada" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Jornada" })).toBeVisible();
    // The grid keeps its design with the only team with correspondence.
    expect(screen.getAllByRole("radio")).toHaveLength(1);
    for (const synthetic of [getCompetitions, getSeasonTeams, getTeamSeason]) expect(synthetic).not.toHaveBeenCalled();
  });

  it("shows the balance with goal difference, home and away, and the result sequence, without standings nor advanced metrics", async () => {
    render(<TeamsView />);
    await loaded();
    for (const [short, value] of [["PJ", "3"], ["V", "2"], ["E", "1"], ["D", "0"], ["GF", "6"], ["GC", "2"], ["DG", "+4"]]) {
      expect(item(short), short).toHaveTextContent(`${short}${value}`);
    }
    expect(within(screen.getByLabelText("Local y visitante")).getByText("Local").parentElement).toHaveTextContent("Local1V1E0D");
    expect(within(screen.getByLabelText("Local y visitante")).getByText("Visitante").parentElement).toHaveTextContent("Visitante1V0E0D");
    const chips = within(screen.getByRole("list", { name: "Resultados por jornada" })).getAllByRole("button");
    expect(chips.map((chip) => chip.getAttribute("aria-label"))).toEqual([
      "Jornada 1: Victoria 3-0 fuera contra CE Europa",
      "Jornada 2: Empate 2-2 en casa contra Huesca",
      "Jornada 3: Victoria 1-0 en casa contra CE Europa",
    ]);
    expect(screen.queryByText(/Clasificación|Puntos/)).toBeNull();
    expect(screen.queryByText("Datos reales")).toBeNull();
    expect(screen.queryByText(SCOPE)).toBeNull();
  });

  it("shows the source's production and concession with their coverage; a missing value is «—», never 0", async () => {
    const { container } = render(<TeamsView />);
    await loaded();
    expect(screen.getByRole("heading", { name: "Rendimiento" })).toBeVisible();
    const value = (indicator: string) => container.querySelector(`[data-indicator="${indicator}"]`)!;
    expect(value("goals")).toHaveTextContent(/^2,00$/);
    expect(value("goalsAgainst")).toHaveTextContent(/^0,67$/);
    expect(value("shots")).toHaveTextContent(/^12,0$/);
    // A real zero is a value; a missing one says so.
    expect(value("shotsAgainst")).toHaveTextContent(/^0,0$/);
    expect(value("shotsOnTarget")).toHaveTextContent(/^—$/);
    expect(within(value("shotsOnTarget") as HTMLElement).getByTitle("Sin datos")).toBeVisible();
    // Partial coverage: the average of the matches with data, marked and explained.
    expect(value("xg")).toHaveTextContent(/^1,40\*$/);
    expect(within(value("xg") as HTMLElement).getByTitle("Media de 2 de 3 partidos")).toBeVisible();
    expect(value("possession")).toHaveTextContent(/^52,5 %\*$/);
    expect(screen.getByText(/Media solo de los partidos con dato\./)).toBeVisible();
    for (const label of ["Goles / partido", "xG / partido", "Tiros / partido", "Tiros a puerta / partido",
      "Goles encajados / partido", "xG concedidos / partido", "Tiros concedidos / partido",
      "Tiros a puerta concedidos / partido", "Córners / partido", "Posesión media"]) {
      expect(screen.getByText(label)).toBeVisible();
      expect(screen.getByText(label)).not.toHaveAttribute("title");
    }
  });

  it("switches to «Últimos 3» with the source's matches, compared with the season and highlighted in the list", async () => {
    const { container } = render(<TeamsView />);
    await loaded();
    await userEvent.setup().click(screen.getByRole("button", { name: "Últimos 3" }));
    expect(screen.getByRole("button", { name: "Últimos 3" })).toHaveAttribute("aria-pressed", "true");
    const goals = container.querySelector('[data-indicator="goals"]')!;
    expect(goals).toHaveTextContent("1,50");
    expect(goals).toHaveTextContent("−0,50 vs temporada");
    const rows = within(screen.getByRole("table", { name: "Partidos disputados" })).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.hasAttribute("data-in-period"))).toEqual([true, true, false]);
  });

  it("keeps the real balance and results when the production figures could not be read", async () => {
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance([competition({ output: null })]));
    render(<TeamsView />);
    await loaded();
    expect(screen.getByText("No se pudieron leer la producción y la concesión de estos partidos.")).toBeVisible();
    expect(item("DG")).toHaveTextContent("DG+4");
    expect(screen.getByRole("table", { name: "Partidos disputados" })).toBeVisible();
    expect(getTeamSeason).not.toHaveBeenCalled();
  });

  it("says when counted matches without a date stay out of «Últimos 3»", async () => {
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance([competition({ output: { ...OUTPUT!, undated: 1 } })]));
    render(<TeamsView />);
    await loaded();
    expect(screen.getByText(/1 partido sin fecha no entra en «Últimos 3»\./)).toBeVisible();
  });

  it("lists the played matches newest first, linked to their real detail", async () => {
    real();
    const { unmount } = render(<TeamsView />);
    await loaded();
    const table = screen.getByRole("table", { name: "Partidos disputados" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows.map((row) => row.textContent)).toEqual([
      expect.stringMatching(/^3.*LocalL.*CE Europa.*V 1-0$/), expect.stringMatching(/^2.*LocalL.*Huesca.*E 2-2$/),
      expect.stringMatching(/^1.*VisitanteV.*CE Europa.*V 3-0$/),
    ]);
    expect(within(rows[0]).getByLabelText("Local")).toHaveAccessibleName("Local");
    expect(within(rows[0]).getByLabelText("Local")).toHaveTextContent("L");
    expect(within(rows[2]).getByLabelText("Visitante")).toHaveAccessibleName("Visitante");
    expect(within(rows[2]).getByLabelText("Visitante")).toHaveTextContent("V");
    expect(rows[0].querySelector(".rival a [data-crest]")).toBeNull();
    expect(within(table).getAllByRole("link", { name: /^Ver partido/ }).map((link) => link.getAttribute("href"))).toEqual(
      ["sofascore-match-3", "sofascore-match-2", "sofascore-match-1"].map((id) => `/matches/${id}?from=teams`));
    expect(within(table).getAllByRole("link", { name: /^Ver equipo/ }).map((link) => link.getAttribute("href"))).toEqual([
      "/teams?team=sofascore-team-135672", "/teams?team=sofascore-team-24265", "/teams?team=sofascore-team-135672",
    ]);
    unmount();
  });

  it("keeps a real match row without a detail destination inert outside its team link", async () => {
    const user = userEvent.setup();
    const played: Played = {
      id: "observed-without-detail", round: 9, kickoff: "2026-10-25T17:00:00Z", side: "away",
      opponent: { id: "sofascore-team-135672", name: "CE Europa", shortName: "Europa", crestUrl: null,
        colors: { primary: "#123456", secondary: "#ffffff" } },
      result: "draw", goalsFor: 1, goalsAgainst: 1, href: null, opponentHref: "/teams?team=sofascore-team-135672",
    };
    render(<PlayedZone played={[played]} highlighted={new Set()} />);
    const table = screen.getByRole("table", { name: "Partidos disputados" });
    const row = within(table).getAllByRole("row")[1];
    expect(row).toHaveAttribute("data-linked", "false");
    expect(within(row).queryByRole("link", { name: /^Ver partido/ })).toBeNull();
    expect(within(row).getByLabelText("Visitante")).toHaveAccessibleName("Visitante");
    const pathBeforeClick = window.location.pathname;
    await user.click(within(row).getByRole("cell", { name: "9" }));
    expect(window.location.pathname).toBe(pathBeforeClick);
  });

  it("says what is left out: finished matches without a valid score, with contradictory versions and unknown states", async () => {
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance([competition({
      matches: { inScope: 38, finished: 5, pending: 28, counted: 3, finishedWithoutValidScore: 1, excludedByVersionConflict: 1, unknownStatus: 1, replacedLocally: 0 },
    })]));
    render(<TeamsView />);
    await loaded();
    expect(screen.getByText(/1 finalizado sin marcador válido no se incluye\. 1 finalizado con versiones contradictorias no se incluye\. 1 partido con estado desconocido no se incluye\./)).toBeVisible();
    expect(screen.queryByText(/decisión local/)).toBeNull();
  });

  it("counts an event replaced by a local decision apart from the current matches", async () => {
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance([competition({
      matches: { inScope: 38, finished: 3, pending: 35, counted: 3, finishedWithoutValidScore: 0, excludedByVersionConflict: 0, unknownStatus: 0, replacedLocally: 1 },
    })]));
    render(<TeamsView />);
    await loaded();
    expect(screen.getByText("1 evento sustituido por otro por decisión local se conserva sin contar.")).toBeVisible();
  });

  it("tells zero finished matches apart from a failure, which retries", async () => {
    vi.mocked(getTeamObservedPerformance).mockResolvedValueOnce(performance([competition({
      matches: { inScope: 29, finished: 0, pending: 29, counted: 0, finishedWithoutValidScore: 0, excludedByVersionConflict: 0, unknownStatus: 0, replacedLocally: 0 },
      record: { played: 0, wins: 0, draws: 0, losses: 0, goalsFor: 0, goalsAgainst: 0, goalDifference: 0 },
      venues: { home: { win: 0, draw: 0, loss: 0 }, away: { win: 0, draw: 0, loss: 0 } }, results: [],
    })]));
    const { unmount } = render(<TeamsView />);
    expect(await screen.findByText("Sin partidos finalizados observados en esta competición.")).toBeVisible();
    expect(screen.queryByLabelText("Balance")).toBeNull();
    unmount();

    vi.mocked(getTeamObservedPerformance).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No"));
    render(<TeamsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudieron cargar los datos reales del equipo.");
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    await loaded();
    expect(getTeamSeason).not.toHaveBeenCalled();
  });

  it("shows an identity-only team without substituting Real Jaén's performance", async () => {
    const { unmount } = render(<TeamsView initialSeasonId="season-demo-2026-27" />);
    expect(await screen.findByText(/La temporada indicada no corresponde a la fuente real de equipos/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Ver la temporada real" })).toHaveAttribute("href", "/teams");
    unmount();
    vi.mocked(getTeamObservedPerformance).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
    vi.mocked(getObservedTeam).mockResolvedValue(europa);
    render(<TeamsView initialTeamId={europa.id} />);
    expect(await screen.findByRole("heading", { level: 2, name: "CE Europa" })).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("está identificado, pero no tiene estadísticas propias disponibles");
    expect(screen.queryByLabelText("Balance")).toBeNull();
    expect(screen.queryByRole("table", { name: "Partidos disputados" })).toBeNull();
    expect(getTeamObservedPerformance).toHaveBeenCalledWith(europa.id, expect.anything());
    expect(getObservedTeam).toHaveBeenCalledWith(europa.id, expect.anything());
    // Return to the real club without relying on browser history.
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance());
    await userEvent.setup().click(screen.getByRole("link", { name: "Ver el Real Jaén" }));
    await loaded();
  });

  it("leaves a team with no resolvable identity as an honest unavailable state", async () => {
    vi.mocked(getTeamObservedPerformance).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
    vi.mocked(getObservedTeam).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
    render(<TeamsView initialTeamId="team-montealto" />);
    expect(await screen.findByText("La fuente real no pudo resolver la identidad de este equipo.")).toBeVisible();
    expect(screen.queryByRole("heading", { level: 2, name: "Real Jaén" })).toBeNull();
  });

  describe("URL of the shown team (?team=, season and tab)", () => {
    beforeEach(() => {
      window.history.replaceState(null, "", "/teams");
      vi.mocked(getTeamObservedPerformance).mockImplementation(async (teamId) => ({ ...performance(), teamId, teams: [jaen, ibiza] }));
      vi.mocked(getObservedSquad).mockImplementation(async (teamId) => ({ ...squad(), teamId }));
    });

    it("leaves the default club out, then writes each chosen team, season and tab, without new history entries", async () => {
      const entries = window.history.length;
      const user = userEvent.setup();
      render(<TeamsView />);
      await loaded();
      expect(window.location.search).toBe("");
      await user.click(screen.getByRole("radio", { name: /UD Ibiza/ }));
      expect(await screen.findByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();
      expect(window.location.search).toBe("?team=sofascore-team-24368");
      await user.click(screen.getByRole("tab", { name: "Jugadores" }));
      expect(window.location.search).toBe("?team=sofascore-team-24368&tab=players");
      expect(screen.queryByRole("combobox", { name: "Temporada" })).toBeNull();
      expect(window.history.length).toBe(entries);
    });

    it("opens the team, season and tab of the URL directly (reload or shared link)", async () => {
      render(<TeamsView initialSeasonId={GROUP_2} initialTab="players" initialTeamId="sofascore-team-24368" />);
      expect(await screen.findByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();
      expect(getTeamObservedPerformance).toHaveBeenCalledWith("sofascore-team-24368", expect.anything());
      expect(screen.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");
    });

    it("never replaces an unknown or unavailable team, nor a synthetic season, with the club in the URL", async () => {
      window.history.replaceState(null, "", "/teams?team=team-montealto");
      vi.mocked(getTeamObservedPerformance).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
      vi.mocked(getObservedTeam).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
      const { unmount } = render(<TeamsView initialTeamId="team-montealto" />);
      expect(await screen.findByText(/no pudo resolver la identidad/)).toBeVisible();
      expect(window.location.search).toBe("?team=team-montealto");
      expect(screen.queryByRole("heading", { level: 2, name: "Real Jaén" })).toBeNull();
      unmount();
      vi.mocked(getTeamObservedPerformance).mockImplementation(async (teamId) => ({ ...performance(), teamId, teams: [jaen, ibiza] }));
      window.history.replaceState(null, "", "/teams?season=season-demo-2026-27");
      render(<TeamsView initialSeasonId="season-demo-2026-27" />);
      expect(await screen.findByText(/La temporada indicada no corresponde a la fuente real de equipos/)).toBeVisible();
      expect(window.location.search).toBe("?season=season-demo-2026-27");
    });
  });

  it("opens the real season from the URL: the one a real profile returns to", async () => {
    render(<TeamsView initialSeasonId={GROUP_2} />);
    await loaded();
    expect(screen.queryByRole("combobox", { name: "Temporada" })).toBeNull();
  });

  it.each([
    { id: "another-season", season: "27/28" },
    { id: "another-competition", name: "Copa del Rey" },
  ])("rejects a URL for another scope even when the API offers it ($id)", async (other) => {
    vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance([competition(other), competition()]));
    vi.mocked(getObservedSquad).mockResolvedValue(squad());
    render(<TeamsView initialSeasonId={other.id} initialTab="players" />);
    expect(await screen.findByText(/La temporada indicada no corresponde/)).toBeVisible();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the real observed players, returning to this real season", async () => {
    real();
    vi.mocked(getObservedSquad).mockResolvedValue(squad());
    const { unmount } = render(<TeamsView initialTab="players" />);
    const link = await screen.findByRole("link", { name: "Ver ficha de Moha Keita" });
    expect(link).toHaveAttribute("href", `/players/sofascore-player-966303?view=profile&season=${GROUP_2}&from=team&origin=team-real-jaen`);
    expect(getObservedPlayers).not.toHaveBeenCalled();
    expect(getTeamSeason).not.toHaveBeenCalled();
    unmount();
  });

  describe("Jugadores con acumulados reales", () => {
    beforeEach(() => {
      real();
      vi.mocked(getObservedSquad).mockResolvedValue(squad());
    });
    const table = () => screen.findByRole("table", { name: "Jugadores observados" });
    const row = (name: string) => screen.getByRole("link", { name: `Ver ficha de ${name}` }).closest("tr")!;
    const cells = (name: string) => [...row(name).querySelectorAll("td")].map((cell) => cell.textContent);
    const names = () => within(screen.getByRole("table", { name: "Jugadores observados" })).getAllByRole("row").slice(1)
      .map((r) => within(r).getByRole("link").getAttribute("aria-label")!.replace("Ver ficha de ", ""));
    const sortBy = (name: RegExp) => within(screen.getByRole("columnheader", { name })).getByRole("button");

    it("keeps the original table with the source's figures and personal data, without one request per player", async () => {
      render(<TeamsView initialTab="players" />);
      await table();
      expect(within(screen.getByRole("table", { name: "Jugadores observados" })).getAllByRole("columnheader")
        .map((header) => header.textContent)).toEqual([
        "Jugador", "Posición", "Partidos", "Titularidades", "Minutos", "Goles", "Asistencias", "Estatura", "Pierna",
      ]);
      // Position, PJ, Tit., Min., Goles, Asist., Estatura, Pierna: clear figures for the matches analysed.
      expect(cells("Marco Siverio")).toEqual(["DelanteroDEL", "7", "6", "504", "6", "0", "184 cm", "Derecha"]);
      // One call-up the service could not resolve: only this player's figures are partial.
      expect(cells("Francisco Gallego")).toEqual(["CentrocampistaMED", "5* (participación conocida en 7 de 8 convocatorias)", "0",
        "70* (dato de 7 de 8 convocatorias)", "0* (dato de 7 de 8 convocatorias)", "0* (dato de 7 de 8 convocatorias)", "—", "—"]);
      expect(within(row("Francisco Gallego")).getByTitle("Participación conocida en 7 de 8 convocatorias")).toBeVisible();
      // Unused in every call-up: 0 played and 0 minutes, read like an unused squad member.
      expect(cells("Salvi")).toEqual(["PorteroPOR", "0", "0", "0", "0", "0", "190 cm", "—"]);
      expect(row("Salvi")).toHaveAttribute("data-unused");
      // Nothing resolved: «Sin datos», never 0.
      expect(cells("Jugador Sin Resolver")).toEqual(["DefensaDEF", "—Sin datos", "0", "—Sin datos", "—Sin datos", "—Sin datos", "—", "—"]);
      expect(screen.getByTitle("Partidos jugados (convocatorias con participación)")).toBeVisible();
      expect(within(row("Marco Siverio")).getByTitle("Dorsal en su última convocatoria observada")).toHaveTextContent("9");
      expect(getPlayer).not.toHaveBeenCalled();
      for (const synthetic of [getTeamSeason, getObservedPlayers]) expect(synthetic).not.toHaveBeenCalled();
    });

    it("explains the coverage briefly, with what was set aside", async () => {
      vi.mocked(getObservedSquad).mockResolvedValue(squad([squadCompetition({ matches: { counted: 8, withCallUps: 7 },
        callUps: { ...squadCompetition().callUps, contradictions: 1, mismatches: 1, duplicateRecords: 1 } })]));
      const { unmount } = render(<TeamsView initialTab="players" />);
      await table();
      expect(screen.queryByText(SQUAD_SCOPE)).toBeNull();
      expect(screen.queryByText("Datos reales")).toBeNull();
      expect(screen.getByText("Convocatorias en 7 de 8 partidos finalizados.")).toBeVisible();
      expect(screen.getByText("1 dato de goles o asistencias contradice las incidencias; 1 registro duplicado se cuenta una vez.")).toBeVisible();
      const note = screen.getByRole("complementary", { name: "Nota sobre los datos" });
      expect(note).toHaveTextContent("El asterisco (*) indica que la cifra se basa en datos de solo algunas convocatorias y puede estar incompleta.");
      const playersTable = screen.getByRole("table", { name: "Jugadores observados" });
      expect(playersTable.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      expect(playersTable.parentElement).not.toContainElement(note);
      expect(screen.queryByText(/al ordenar|sin participación resuelta/)).toBeNull();
      unmount();
      // Without uncertainties, no warning beyond the general coverage.
      vi.mocked(getObservedSquad).mockResolvedValue(squad([squadCompetition({ players: [PLAYERS[0]], callUps: { total: 7, played: 7, unused: 0, unknown: 0, contradictions: 0, mismatches: 0, teamWarnings: 0, duplicateRecords: 0, discardedRecords: 0 } })]));
      render(<TeamsView initialTab="players" />);
      await table();
      expect(screen.queryByText(/sin participación resuelta|llevan \*/)).toBeNull();
      expect(screen.queryByRole("complementary", { name: "Nota sobre los datos" })).toBeNull();
    });

    it("shows the note only while filtered rows contain partial values, including partial goals with known participation", async () => {
      const user = userEvent.setup();
      vi.mocked(getObservedSquad).mockResolvedValue(squad([squadCompetition({ players: [PLAYERS[2], {
        ...PLAYERS[0], stats: { ...PLAYERS[0].stats, goals: stat(5, 6, 7) },
      }], callUps: { ...squadCompetition().callUps, unknown: 0, mismatches: 1 } })]));
      render(<TeamsView initialTab="players" />);
      await table();
      expect(screen.getByRole("complementary", { name: "Nota sobre los datos" })).toBeVisible();
      expect(within(row("Marco Siverio")).getByTitle("Dato de 6 de 7 convocatorias")).toHaveTextContent("5*");
      await user.click(screen.getByRole("button", { name: /^Porteros/ }));
      expect(screen.queryByRole("complementary", { name: "Nota sobre los datos" })).toBeNull();
      await user.click(screen.getByRole("button", { name: /^Todos/ }));
      expect(screen.getByRole("complementary", { name: "Nota sobre los datos" })).toBeVisible();
    });

    it("sorts complete figures first, then partial ones, then missing ones, in both directions", async () => {
      const user = userEvent.setup();
      render(<TeamsView initialTab="players" />);
      await table();
      await user.click(sortBy(/^Goles/));
      // Gallego's 0* covers 7 of 8 call-ups: after the complete ones, neither a total nor missing.
      expect(names()).toEqual(["Marco Siverio", "Salvi", "Moha Keita", "Francisco Gallego", "Jugador Sin Resolver"]);
      await user.click(sortBy(/^Goles/));
      expect(screen.getByRole("columnheader", { name: /^Goles/ })).toHaveAttribute("aria-sort", "ascending");
      expect(names()).toEqual(["Salvi", "Moha Keita", "Marco Siverio", "Francisco Gallego", "Jugador Sin Resolver"]);
      await user.click(sortBy(/^Min/));
      expect(names()).toEqual(["Marco Siverio", "Moha Keita", "Salvi", "Francisco Gallego", "Jugador Sin Resolver"]);
      expect(screen.getByRole("columnheader", { name: /^Min/ })).toHaveAttribute("aria-sort", "descending");
    });

    it("filters by line with the keyboard", async () => {
      const user = userEvent.setup();
      render(<TeamsView initialTab="players" />);
      await table();
      screen.getByRole("button", { name: /^Centrocampistas/ }).focus();
      await user.keyboard("{Enter}");
      expect(screen.getByRole("button", { name: /^Centrocampistas/ })).toHaveAttribute("aria-pressed", "true");
      expect(names()).toEqual(["Francisco Gallego", "Moha Keita"]);
    });

    it("keeps the current season even when another one precedes it in the response", async () => {
      const next = "sofascore-scope-94191-99999";
      vi.mocked(getTeamObservedPerformance).mockResolvedValue(performance([competition({ id: next, season: "27/28" }), competition()]));
      vi.mocked(getObservedSquad).mockResolvedValue(squad([squadCompetition(), squadCompetition({ id: next, season: "27/28",
        players: [PLAYERS[3]], callUps: { total: 6, played: 6, unused: 0, unknown: 0, contradictions: 0, mismatches: 0, teamWarnings: 0, duplicateRecords: 0, discardedRecords: 0 } })]));
      render(<TeamsView initialTab="players" />);
      await table();
      expect(names()).toHaveLength(5);
      expect(screen.queryByRole("combobox", { name: "Temporada" })).toBeNull();
      expect(screen.getByRole("link", { name: "Ver ficha de Moha Keita" })).toHaveAttribute("href", expect.stringContaining(`season=${GROUP_2}`));
      expect(getObservedSquad).toHaveBeenCalledTimes(1);
    });

    it("says so when the competition has no players, and when the source has nothing for it", async () => {
      vi.mocked(getObservedSquad).mockResolvedValue(squad([squadCompetition({ players: [], matches: { counted: 0, withCallUps: 0 },
        callUps: { total: 0, played: 0, unused: 0, unknown: 0, contradictions: 0, mismatches: 0, teamWarnings: 0, duplicateRecords: 0, discardedRecords: 0 } })]));
      const { unmount } = render(<TeamsView initialTab="players" />);
      expect(await screen.findByText("La fuente no tiene convocatorias de este equipo en esta competición.")).toBeVisible();
      unmount();
      vi.mocked(getObservedSquad).mockResolvedValue(squad([]));
      render(<TeamsView initialTab="players" />);
      expect(await screen.findByText("La fuente real no tiene jugadores observados de este equipo en esta competición.")).toBeVisible();
    });

    it("shows loading, then a failure with retry, never synthetic players", async () => {
      let fail: (error: Error) => void = () => {};
      vi.mocked(getObservedSquad).mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }));
      render(<TeamsView initialTab="players" />);
      expect(await screen.findByText("Cargando jugadores…")).toBeVisible();
      fail(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "La fuente real no responde."));
      expect(await screen.findByRole("alert")).toHaveTextContent("La fuente real no responde.");
      await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
      await table();
      for (const synthetic of [getTeamSeason, getObservedPlayers, getPlayer]) expect(synthetic).not.toHaveBeenCalled();
    });
  });

  it("asks nothing when the source cannot be determined, saying why, and never asks the real source in the demonstration", async () => {
    vi.mocked(getSource).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "Configuración de datos no válida: DATA_MODE=x no es válido: usa real o demo."));
    const { unmount } = render(<TeamsView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo determinar la fuente de los equipos. Configuración de datos no válida: DATA_MODE=x no es válido");
    expect(getTeamObservedPerformance).not.toHaveBeenCalled();
    expect(getCompetitions).not.toHaveBeenCalled();
    unmount();
    vi.mocked(getSource).mockResolvedValue(DEMO_SOURCE);
    vi.mocked(getCompetitions).mockResolvedValue([]);
    render(<TeamsView />);
    await vi.waitFor(() => expect(getCompetitions).toHaveBeenCalled());
    expect(getTeamObservedPerformance).not.toHaveBeenCalled();
  });
});
