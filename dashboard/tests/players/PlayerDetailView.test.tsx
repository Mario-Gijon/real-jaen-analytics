import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, getPlayer, getSeasonMatches } from "@/client/api";
import { PLAYER_STAT_KEYS, type GoalEvent, type PlayerMatch, type PlayerMatchStats,
  type PlayerSeason, type PlayerSeasonStats, type SeasonStat, type TeamRef } from "@/contract";
import { PlayerDetailView } from "@/features/players/PlayerDetailView";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(), getPlayer: vi.fn(), getSeasonMatches: vi.fn(),
}));
const team: TeamRef = { id: "team", name: "Real Jaén CF", shortName: "Real Jaén", crestUrl: null,
  colors: { primary: "#4b2677", secondary: "#ffffff" } };
const rival: TeamRef = { ...team, id: "rival", name: "CD Rival de Nombre Largo", shortName: "Rival" };
const complete = (value: number, of = 3): SeasonStat => ({ value, coverage: "complete", matches: of, of });
const baseStats = Object.fromEntries([...PLAYER_STAT_KEYS, "appearances", "starts"].map(key => [key, complete(0)])) as PlayerSeasonStats;
const matchStats = Object.fromEntries(PLAYER_STAT_KEYS.map(key => [key, { value: 0 }])) as PlayerMatchStats;
const profile: PlayerSeason = {
  player: { id: "player", name: "Jugador de Prueba", position: "midfielder",
    birthDate: "2000-01-10", heightCm: 180, preferredFoot: "right" },
  team, shirtNumber: 14,
  season: { id: "season", name: "2026/27", competitionId: "competition", rounds: 38, currentRound: 14 },
  stats: { ...baseStats, appearances: complete(3), starts: complete(2), minutes: complete(210),
    goals: complete(2), assists: complete(2), shots: complete(7), passes: complete(120), passAccuracy: complete(82.5) },
  matches: [],
};
const goal = (id: string, fields: Partial<GoalEvent> = {}): GoalEvent => ({
  id, matchId: "match", type: "goal", teamId: team.id, minute: 47, addedTime: null,
  player: profile.player, assist: null, penalty: false, ownGoal: false, clip: null, ...fields,
});
const appearance = (events: GoalEvent[], round = 14, away = false): PlayerMatch => ({
  match: { id: `match-${round}`, seasonId: "season", round, kickoff: "2026-12-12T11:00:00Z",
    status: "finished", home: away ? rival : team, away: away ? team : rival, score: { home: 2, away: 1 } },
  side: away ? "away" : "home", started: true, stats: matchStats, events,
});
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getPlayer).mockResolvedValue(profile);
  vi.mocked(getSeasonMatches).mockResolvedValue([]);
});
async function load(data = profile) {
  vi.mocked(getPlayer).mockResolvedValue(data);
  render(<PlayerDetailView playerId={data.player.id} seasonId="season" />);
  await screen.findByRole("heading", { name: data.player.name });
}
function metric(region: string, label: string) {
  const term = within(screen.getByRole("region", { name: region })).getByText(label, { selector: "dt" });
  return term.nextElementSibling!;
}

describe("Player season detail", () => {
  it("provides a deterministic internal return without relying on browser history", async () => {
    await load();
    expect(screen.getByRole("link", { name: "← Volver" })).toHaveAttribute("href", "/teams?season=season&tab=players");
  });

  it("uses the explicit originating match and tab for the return action", async () => {
    render(<PlayerDetailView playerId="player" seasonId="season" returnHref="/matches/m1?tab=lineups" />);
    await screen.findByRole("heading", { name: profile.player.name });
    expect(screen.getByRole("link", { name: "← Volver" })).toHaveAttribute("href", "/matches/m1?tab=lineups");
  });

  it("opens Temporada by default and does not load the calendar", async () => {
    await load();
    expect(screen.getByRole("tab", { name: "Temporada" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Temporada" })).toBeVisible();
    expect(getSeasonMatches).not.toHaveBeenCalled();
  });

  it("opens Partidos on the origin match and keeps it when returning to the tab", async () => {
    const played = appearance([], 12);
    const latest = appearance([], 14);
    vi.mocked(getPlayer).mockResolvedValue({ ...profile, matches: [played, latest] });
    vi.mocked(getSeasonMatches).mockResolvedValue([played.match, latest.match]);
    render(<PlayerDetailView playerId="player" seasonId="season" returnHref="/matches/match-12?tab=timeline"
      initialTab="matches" initialMatchId="match-12" />);
    const panel = await screen.findByRole("region", { name: "Detalle del partido" });
    expect(screen.getByRole("tab", { name: "Partidos" })).toHaveAttribute("aria-selected", "true");
    expect(panel).toHaveTextContent("J12 · vs CD Rival de Nombre Largo");
    expect(screen.getByRole("link", { name: "← Volver" })).toHaveAttribute("href", "/matches/match-12?tab=timeline");
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Temporada" }));
    await user.click(screen.getByRole("tab", { name: "Partidos" }));
    expect(await screen.findByRole("region", { name: "Detalle del partido" })).toHaveTextContent("J12 · vs");
  });

  it("activates Partidos by keyboard and restores the approved season without reloading the player", async () => {
    await load();
    expect(getSeasonMatches).not.toHaveBeenCalled();
    screen.getByRole("tab", { name: "Temporada" }).focus();
    await userEvent.setup().keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Partidos" })).toHaveFocus();
    expect(await screen.findByText("Sin apariciones esta temporada.")).toBeVisible();
    expect(screen.getByRole("tabpanel", { name: "Partidos" })).toBeVisible();
    await userEvent.setup().keyboard("{ArrowLeft}");
    expect(screen.getByRole("tabpanel", { name: "Temporada" })).toBeVisible();
    expect(metric("Finalización", "Tiros")).toHaveTextContent("7");
    expect(getPlayer).toHaveBeenCalledTimes(1);
  });
  it("loads once through getPlayer and shows identity, participation and outfield groups", async () => {
    await load();
    expect(getPlayer).toHaveBeenCalledTimes(1);
    expect(getPlayer).toHaveBeenCalledWith("player", "season", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByLabelText("Dorsal 14")).toBeVisible();
    expect(screen.getByRole("link", { name: "Real Jaén CF" })).toHaveAttribute("href", "/teams?season=season&team=team");
    expect(metric("Participación", "PJ")).toHaveTextContent("3");
    expect(metric("Participación", "Titularidades")).toHaveTextContent("2");
    expect(metric("Participación", "Minutos")).toHaveTextContent("210");
    expect(metric("Finalización", "Tiros")).toHaveTextContent("7");
    const performance = within(screen.getByRole("region", { name: "Rendimiento" }));
    expect(performance.queryByText("Goles")).toBeNull();
    expect(performance.queryByText("Asistencias")).toBeNull();
    expect(screen.getAllByRole("heading", { name: "Goles" })).toHaveLength(1);
    expect(screen.getAllByRole("heading", { name: "Asistencias" })).toHaveLength(1);
    expect(metric("Juego", "Precisión de pase")).toHaveTextContent("82,5 %");
    expect(metric("Recuperación", "Recuperaciones")).toHaveTextContent("0");
    expect(metric("Disciplina", "Amarillas")).toHaveTextContent("0");
    expect(screen.queryByRole("region", { name: "Portería" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Temporada" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel", { name: "Temporada" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Partidos" })).toBeEnabled();
    expect(screen.queryByTitle(/Dato de/)).toBeNull();
  });

  it("prioritizes saves and conceded goals for goalkeepers without repeating participation", async () => {
    await load({ ...profile, player: { ...profile.player, position: "goalkeeper" },
      stats: { ...profile.stats, saves: complete(36), goalsConceded: complete(12) } });
    expect(metric("Portería", "Paradas")).toHaveTextContent("36");
    expect(metric("Portería", "Goles encajados")).toHaveTextContent("12");
    expect(screen.queryByRole("region", { name: "Finalización" })).toBeNull();
    expect(screen.queryByText("Sin participación esta temporada.")).toBeNull();
    expect(screen.getByRole("region", { name: "Aportación" })).toBeVisible();
    expect(screen.queryByText("Tiros")).toBeNull();
    expect(screen.getAllByText("PJ", { selector: "dt" })).toHaveLength(1);
    expect(screen.queryByText(/porterías a cero/i)).toBeNull();
  });

  it.each(["goalkeeper", "midfielder"] as const)("preserves zero participation without observed performance or contribution for a %s", async position => {
    const stats = Object.fromEntries([...PLAYER_STAT_KEYS, "appearances", "starts"].map(key => [key, complete(0, 0)])) as PlayerSeasonStats;
    await load({ ...profile, player: { ...profile.player, position }, stats, matches: [] });
    for (const label of ["PJ", "Titularidades", "Minutos"]) expect(metric("Participación", label)).toHaveTextContent("0");
    const performance = screen.getByRole("region", { name: "Rendimiento" });
    expect(performance).toHaveTextContent("Sin participación esta temporada.");
    expect(within(performance).queryByRole("definition")).toBeNull();
    expect(screen.queryByText("Paradas")).toBeNull();
    expect(screen.queryByText("Goles encajados")).toBeNull();
    expect(screen.queryByRole("region", { name: "Aportación" })).toBeNull();
    expect(screen.queryByRole("list")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("handles nullable personal data and an unknown shirt number", async () => {
    await load({ ...profile, shirtNumber: null,
      player: { ...profile.player, birthDate: null, heightCm: null, preferredFoot: null } });
    for (const label of ["Nacimiento", "Estatura", "Pierna dominante"]) {
      expect(screen.getByText(label, { selector: "dt" }).nextElementSibling).toHaveTextContent("—");
    }
    expect(screen.queryByLabelText(/Dorsal/)).toBeNull();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it("marks partial coverage using appearances rather than the season calendar", async () => {
    await load({ ...profile, stats: { ...profile.stats,
      passes: { value: 120, coverage: "partial", matches: 2, of: 3 },
      passAccuracy: { value: 82.5, coverage: "partial", matches: 2, of: 3 } } });
    expect(screen.getAllByTitle("Dato de 2 de 3 partidos")).toHaveLength(2);
    expect(metric("Juego", "Pases")).toHaveTextContent("120*");
    expect(metric("Juego", "Precisión de pase")).toHaveTextContent("82,5*");
    expect(metric("Juego", "Pases")).toHaveTextContent("dato de 2 de 3 partidos");
    expect(screen.queryByText(/de 38 partidos/)).toBeNull();
    expect(within(screen.getByRole("region", { name: "Juego" })).getAllByText("* Datos disponibles en 2 de 3 partidos")).toHaveLength(1);
  });

  it("identifies the affected metrics when partial coverages differ within a group", async () => {
    await load({ ...profile, stats: { ...profile.stats,
      passes: { value: 120, coverage: "partial", matches: 2, of: 3 },
      passAccuracy: { value: 82.5, coverage: "partial", matches: 1, of: 3 },
      dribbles: { value: 4, coverage: "partial", matches: 2, of: 3 } } });
    const group = within(screen.getByRole("region", { name: "Juego" }));
    expect(group.getByText("* Pases / Regates: Datos disponibles en 2 de 3 partidos")).toBeVisible();
    expect(group.getByText("* Precisión de pase: Datos disponibles en 1 de 3 partidos")).toBeVisible();
    expect(metric("Juego", "Pases")).toHaveTextContent("120*");
  });

  it.each(["not_available", "not_collected"] as const)("preserves none coverage and %s", async reason => {
    await load({ ...profile, stats: { ...profile.stats,
      passes: { value: null, coverage: "none", reason, matches: 0, of: 3 } } });
    const note = `${reason === "not_collected" ? "Dato no recogido" : "Dato no disponible"} · 0 de 3 partidos`;
    expect(screen.getByLabelText(note)).toHaveTextContent("—");
    expect(screen.getByTitle(note)).toBeVisible();
    expect(metric("Juego", "Pases")).not.toHaveTextContent("0");
  });

  it("shows multiple goals, penalties and added time with match context, without clips", async () => {
    await load({ ...profile, matches: [appearance([
      goal("late", { minute: 90, addedTime: 3 }), goal("penalty", { minute: 26, penalty: true }),
    ], 14, true)] });
    const history = screen.getByRole("list", { name: "Histórico de goles" });
    const rows = within(history).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("26′");
    expect(rows[0]).toHaveTextContent("Penalti");
    expect(rows[1]).toHaveTextContent("90+3′");
    expect(within(history).getByLabelText("Gol en el minuto 90+3")).toBeVisible();
    expect(rows[0]).toHaveTextContent("J14 · vs CD Rival de Nombre Largo");
    expect(within(rows[0]).getByRole("link", { name: "CD Rival de Nombre Largo" })).toHaveAttribute("href", "/teams?season=season&team=rival");
    expect(rows[0]).toHaveTextContent("Visitante");
    expect(within(history).getAllByRole("link", { name: "Ver partido: CD Rival de Nombre Largo contra Real Jaén CF" })).toHaveLength(2);
    expect(within(rows[0]).getByRole("link", { name: "Ver partido: CD Rival de Nombre Largo contra Real Jaén CF" }))
      .toHaveAttribute("href", "/matches/match-14?from=player&player=player&season=season");
    expect(rows[0].querySelector("a a")).toBeNull();
    expect(screen.queryByRole("button", { name: /vídeo|clip/i })).toBeNull();
    expect(screen.queryByText(/Sin vídeo/)).toBeNull();
  });

  it("uses credited assist ids and the assisted goal's minute for multiple assists", async () => {
    const scorer = { ...profile.player, id: "scorer", name: "Autor del Gol" };
    await load({ ...profile, matches: [appearance([
      goal("assist-1", { player: scorer, assist: profile.player, minute: 12 }),
      goal("assist-2", { player: scorer, assist: profile.player, minute: 45, addedTime: 2 }),
      goal("other-assist", { player: scorer, assist: { ...profile.player, id: "other" }, minute: 80 }),
    ])] });
    const history = screen.getByRole("list", { name: "Histórico de asistencias" });
    expect(within(history).getAllByRole("listitem")).toHaveLength(2);
    expect(within(history).getByLabelText("Gol asistido en el minuto 12")).toBeVisible();
    expect(within(history).getByLabelText("Gol asistido en el minuto 45+2")).toBeVisible();
    expect(screen.getByText("El minuto corresponde al gol asistido.")).toBeVisible();
    expect(screen.queryByRole("list", { name: "Histórico de goles" })).toBeNull();
  });

  it("excludes own goals and goals credited to another player from individual goals", async () => {
    await load({ ...profile, stats: { ...profile.stats, goals: complete(0), assists: complete(0) },
      matches: [appearance([goal("own", { ownGoal: true }),
        goal("other", { player: { ...profile.player, id: "other" } })])] });
    expect(screen.getByRole("region", { name: "Goles" })).toHaveTextContent("Goles0");
    expect(screen.queryByRole("list", { name: "Histórico de goles" })).toBeNull();
  });

  it("requires season without fetching or selecting one automatically", () => {
    render(<PlayerDetailView playerId="player" seasonId={null} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Selecciona una temporada");
    expect(getPlayer).not.toHaveBeenCalled();
  });

  it("shows loading while the request is pending", () => {
    vi.mocked(getPlayer).mockReturnValue(new Promise(() => {}));
    render(<PlayerDetailView playerId="player" seasonId="season" />);
    expect(screen.getByRole("status")).toHaveTextContent("Cargando jugador");
  });

  it("distinguishes an unknown player or season from a transient error", async () => {
    vi.mocked(getPlayer).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No encontrado"));
    render(<PlayerDetailView playerId="unknown" seasonId="season" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Jugador no encontrado en esta temporada");
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });

  it("allows retry after a transient failure", async () => {
    vi.mocked(getPlayer).mockRejectedValueOnce(new Error("offline"));
    render(<PlayerDetailView playerId="player" seasonId="season" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo cargar");
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("heading", { name: profile.player.name })).toBeVisible();
  });
});
