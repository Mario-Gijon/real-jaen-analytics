import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getCompetitions, getSeasonMatches, getSeasonTeams, getSource } from "@/client/api";
import type { MatchSummary, Season, TeamRef } from "@/contract";
import { MatchesView } from "@/features/matches/MatchesView";
import { MatchList } from "@/features/matches/MatchList";
import { DEMO_SOURCE } from "../shared/sources";

vi.mock("@/client/api", () => ({
  getCompetitions: vi.fn(), getSeasonMatches: vi.fn(), getSeasonTeams: vi.fn(), getSource: vi.fn(),
}));
const season: Season = { id: "s1", name: "2026/27", competitionId: "c1", rounds: 38, currentRound: 14 };
const jaen: TeamRef = {
  id: "team-real-jaen", name: "Real Jaén CF", shortName: "Real Jaén", crestUrl: null,
  colors: { primary: "#4b2677", secondary: "#ffffff" },
};
const rival: TeamRef = { ...jaen, id: "rival", name: "CD Rival", shortName: "Rival" };
const match: MatchSummary = {
  id: "m14", seasonId: "s1", round: 14, home: jaen, away: rival,
  kickoff: "2026-11-21T17:00:00Z", status: "finished", score: { home: 2, away: 1 },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getCompetitions).mockResolvedValue([{
    id: "c1", name: "Liga Demo", shortName: "Demo",
    seasons: [season, { ...season, id: "s2", name: "2025/26", currentRound: null }],
  }]);
  vi.mocked(getSeasonTeams).mockResolvedValue([
    { rank: 1, team: jaen, record: { played: 13, wins: 6, draws: 3, losses: 4, goalsFor: 20, goalsAgainst: 15, points: 21 } },
  ]);
  vi.mocked(getSeasonMatches).mockResolvedValue([match]);
  vi.mocked(getSource).mockResolvedValue(DEMO_SOURCE);
});

describe("Matches explorer", () => {
  it("starts on currentRound and combines filters without excluding match statuses", async () => {
    const user = userEvent.setup();
    render(<MatchesView />);
    await screen.findByRole("list", { name: "Partidos de la temporada" });
    expect(screen.getByLabelText("Jornada")).toHaveValue("14");
    expect(getSeasonMatches).toHaveBeenLastCalledWith("s1", { round: 14, teamId: undefined }, expect.anything());
    await waitFor(() => expect(screen.getByRole("option", { name: "Real Jaén CF" })).toBeInTheDocument());
    await user.selectOptions(screen.getByLabelText("Equipo"), jaen.id);
    await user.selectOptions(screen.getByLabelText("Jornada"), "all");
    await waitFor(() => expect(getSeasonMatches).toHaveBeenLastCalledWith("s1", {
      round: undefined, teamId: jaen.id,
    }, expect.anything()));
    expect(screen.queryByRole("button", { name: "Jornada anterior" })).toBeNull();
    await user.selectOptions(screen.getByLabelText("Jornada"), "8");
    await waitFor(() => expect(getSeasonMatches).toHaveBeenLastCalledWith("s1", {
      round: 8, teamId: jaen.id,
    }, expect.anything()));
    await user.selectOptions(screen.getByLabelText("Equipo"), "all");
    await waitFor(() => expect(getSeasonMatches).toHaveBeenLastCalledWith("s1", {
      round: 8, teamId: undefined,
    }, expect.anything()));
  });

  it("bounds round navigation and resets filters when season changes", async () => {
    const user = userEvent.setup();
    render(<MatchesView />);
    await screen.findByRole("list");
    await user.click(screen.getByRole("button", { name: "Jornada anterior" }));
    expect(screen.getByLabelText("Jornada")).toHaveValue("13");
    await user.click(screen.getByRole("button", { name: "Jornada siguiente" }));
    expect(screen.getByLabelText("Jornada")).toHaveValue("14");
    await user.selectOptions(screen.getByLabelText("Jornada"), "38");
    expect(screen.getByRole("button", { name: "Jornada siguiente" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("Equipo"), jaen.id);
    await user.selectOptions(screen.getByLabelText("Temporada"), "s2");
    expect(screen.getByLabelText("Jornada")).toHaveValue("1");
    expect(screen.getByLabelText("Equipo")).toHaveValue("all");
    expect(screen.getByRole("button", { name: "Jornada anterior" })).toBeDisabled();
  });

  it("offers retry after an API failure and shows an empty filtered result", async () => {
    vi.mocked(getSeasonMatches).mockRejectedValueOnce(new Error("offline")).mockResolvedValue([]);
    const user = userEvent.setup();
    render(<MatchesView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudieron cargar los partidos.");
    await user.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("No hay partidos con estos filtros.")).toBeVisible();
  });

  it("shows finished, scheduled and postponed fixtures with separate team and match links", () => {
    render(<MatchList showRound matches={[
      { ...match, id: "undated", round: 9, kickoff: null, status: "postponed", score: null },
      { ...match, id: "future", round: 15, kickoff: "2026-11-28T17:00:00Z", status: "scheduled", score: null },
      match,
    ]} />);
    const links = within(screen.getByRole("list")).getAllByRole("link");
    expect(links.map((link) => link.getAttribute("href"))).toEqual([
      "/teams?season=s1&team=team-real-jaen", "/matches/m14", "/teams?season=s1&team=rival",
      "/teams?season=s1&team=team-real-jaen", "/matches/future", "/teams?season=s1&team=rival",
      "/teams?season=s1&team=team-real-jaen", "/matches/undated", "/teams?season=s1&team=rival",
    ]);
    expect(links[1]).toHaveTextContent("2 – 1");
    expect(links[1]).toHaveAccessibleName(/Finalizado/);
    expect(links[4]).toHaveTextContent("18:00");
    expect(links[4]).toHaveAccessibleName(/Programado/);
    expect(links[7]).toHaveTextContent("Aplazado");
    expect(links[7]).toHaveAccessibleName(/jornada 9/);
    expect(links[7]).toHaveAccessibleName(/Fecha por definir/);
    expect(screen.getByRole("list").querySelectorAll("a a")).toHaveLength(0);
  });
});
