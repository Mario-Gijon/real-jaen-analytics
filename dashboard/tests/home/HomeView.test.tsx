import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, expect, it, vi } from "vitest";
import { getCompetitions, getSeasonMatches, getTeamSeason, getSeasonTeams, getSource } from "@/client/api";
import { HomeView } from "@/features/home/HomeView";
import { MatchesView } from "@/features/matches/MatchesView";
import { NOW, season, target, second, rival, other, teamSeason, history } from "./fixtures";
import { DEMO_SOURCE } from "../shared/sources";

const { push } = vi.hoisted(() => ({ push: vi.fn() }));
// No URL of its own: the explorer opened here takes its props (as without a router).
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), useSearchParams: () => null }));
vi.mock("@/client/api", () => ({ getCompetitions: vi.fn(), getSeasonMatches: vi.fn(), getTeamSeason: vi.fn(), getSeasonTeams: vi.fn(), getSource: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getCompetitions).mockResolvedValue([{ id: "c1", name: "Liga", shortName: "Liga", seasons: [season] }]);
  vi.mocked(getSeasonMatches).mockResolvedValue([target, second]);
  vi.mocked(getTeamSeason).mockImplementation(async (_, id) => ({ ...teamSeason, team: id === other.id ? other : rival }));
  vi.mocked(getSeasonTeams).mockResolvedValue([{ ...teamSeason.standing, team: rival }]);
  vi.mocked(getSource).mockResolvedValue(DEMO_SOURCE);
});

it("suggests the next fixture and links the rival and only its last five matches", async () => {
  render(<HomeView now={NOW} />);
  const recent = await screen.findByRole("list", { name: "Últimos partidos del rival" });
  expect(screen.queryByText(/Sugerencia inicial/)).not.toBeInTheDocument();
  expect(screen.queryByText("Rival en análisis")).not.toBeInTheDocument();
  expect(getSeasonMatches).toHaveBeenCalledWith("s1", { teamId: "team-real-jaen" }, expect.anything());
  expect(screen.getByRole("link", { name: "Analizar Rival" })).toHaveAttribute("href", "/teams?season=s1&team=rival");
  const target = screen.getByRole("region", { name: "Encuentro de preparación" });
  expect(within(target).getByRole("link", { name: "Ver equipo Real Jaén CF" })).toHaveAttribute("href", "/teams?season=s1&team=team-real-jaen");
  expect(within(target).getByRole("link", { name: "Ver equipo CD Rival" })).toHaveAttribute("href", "/teams?season=s1&team=rival");
  const section = screen.getByRole("region", { name: "Últimos 5 partidos del CD Rival" });
  expect(within(section).getByRole("link", { name: "Ver todos" })).toHaveAttribute("href", "/matches?season=s1&team=rival&round=all");
  expect(screen.getAllByRole("link", { name: /Ver todos|Sus partidos/ })).toHaveLength(1);
  expect(within(recent).getAllByRole("listitem").flatMap((row) => within(row).getAllByRole("link").map(link => link.getAttribute("href"))))
    .toEqual([10, 9, 8, 7, 6].flatMap(i => [
      "/teams?season=s1&team=rival", `/matches/played-${i}?from=home&season=s1&match=target`, "/teams?season=s1&team=other",
    ]));
  expect(recent.querySelectorAll("a a")).toHaveLength(0);
  const form = screen.getByText("Forma", { selector: "dt" }).nextElementSibling!;
  expect(form).toHaveTextContent(/5\s*V\s*0\s*E\s*0\s*D/);
  expect(form.querySelectorAll(":scope > [data-result]")).toHaveLength(3);
  expect(form.querySelector('[data-result="win"] abbr')).toHaveAttribute("title", "Victorias");
  expect(form.querySelector('[data-result="draw"] abbr')).toHaveAttribute("title", "Empates");
  expect(form.querySelector('[data-result="loss"] abbr')).toHaveAttribute("title", "Derrotas");
  expect(screen.getAllByRole("list", { name: /del rival/ })).toHaveLength(1);
  expect(screen.queryByText(/Partidos anteriores|Material complementario/)).toBeNull();
});

it("names fewer previous matches by their real count", async () => {
  vi.mocked(getTeamSeason).mockResolvedValue({ ...teamSeason, matches: history.slice(0, 3) });
  render(<HomeView now={NOW} />);
  const section = await screen.findByRole("region", { name: "Últimos 3 partidos del CD Rival" });
  expect(within(section).getAllByRole("listitem")).toHaveLength(3);
  expect(within(section).getByRole("link", { name: "Ver todos" })).toBeInTheDocument();
});

it("limits the selector, restores focus, updates URL and derives an away opponent from the chosen fixture", async () => {
  const user = userEvent.setup();
  vi.mocked(getSeasonMatches).mockResolvedValue([target, second, ...Array.from({ length: 6 }, (_, i) => ({ ...second, id: `later-${i}`, round: 13 + i, kickoff: `2026-12-${String(i + 1).padStart(2, "0")}T11:00:00Z` }))]);
  const view = render(<HomeView now={NOW} />);
  const trigger = await screen.findByRole("button", { name: "Cambiar rival" });
  await waitFor(() => expect(trigger).toBeEnabled());
  await user.click(trigger);
  const panel = screen.getByRole("region", { name: "Próximos encuentros del Real Jaén" });
  const buttons = within(panel).getAllByRole("button");
  expect(buttons).toHaveLength(6);
  expect(buttons[0]).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("region", { name: "Próximos encuentros del Real Jaén" })).toBeNull();
  await user.keyboard("{Enter}{Tab}{Enter}");
  expect(push).toHaveBeenCalledWith("/?season=s1&match=second", { scroll: false });
  expect(trigger).toHaveFocus();
  view.rerender(<HomeView now={NOW} seasonId="s1" matchId="second" />);
  expect(await screen.findByRole("link", { name: "Analizar Otro Rival" })).toBeInTheDocument();
  const header = screen.getByRole("region", { name: "Encuentro de preparación" });
  expect(within(header).getByText(other.name).parentElement).toHaveTextContent("Local");
  expect(within(header).getByText("Real Jaén CF").parentElement).toHaveTextContent("Visitante");
  expect(screen.queryByText(/Sugerencia inicial/)).toBeNull();
});

it("shows partial xG coverage and unavailable data without converting them to zeros", async () => {
  vi.mocked(getTeamSeason).mockResolvedValue({ ...teamSeason, matches: history.slice(0, 2).map((entry, i) => ({ ...entry, teamStats: { own: { ...entry.teamStats!.own, xg: i ? { value: null, reason: "not_available" } : { value: 0 } }, opponent: { ...entry.teamStats!.opponent, xg: { value: null, reason: "not_collected" } } } })) });
  render(<HomeView now={NOW} />);
  await screen.findByRole("list", { name: "Últimos partidos del rival" });
  expect(screen.getByText("1/2 partidos con dato")).toBeInTheDocument();
  expect(screen.getByTitle("Datos disponibles en 1 de 2 partidos")).toHaveTextContent("0");
  expect(screen.getByTitle("Dato no recogido")).toHaveTextContent("—");
});

it("does not replace an invalid explicit target with a suggestion", async () => {
  render(<HomeView now={NOW} seasonId="s1" matchId="unknown" />);
  expect(await screen.findByRole("alert")).toHaveTextContent("no es un próximo partido válido");
  expect(getTeamSeason).not.toHaveBeenCalled();
});

it("shows an honest empty state when no valid future fixtures remain", async () => {
  vi.mocked(getSeasonMatches).mockResolvedValue([]);
  render(<HomeView now={NOW} />);
  expect(await screen.findByText("No hay próximos encuentros con fecha disponible para preparar.")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Cambiar rival" })).toBeDisabled();
});

it("initializes the existing explorer with the rival and full season context", async () => {
  render(<MatchesView initialSeasonId="s1" initialTeamId="rival" />);
  await screen.findByRole("list", { name: "Partidos de la temporada" });
  expect(screen.getByLabelText("Jornada")).toHaveValue("all");
  await waitFor(() => expect(screen.getByLabelText("Equipo")).toHaveValue("rival"));
  expect(getSeasonMatches).toHaveBeenLastCalledWith("s1", { round: undefined, teamId: "rival" }, expect.anything());
});
