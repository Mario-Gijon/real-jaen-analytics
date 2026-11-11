import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getSeasonMatches } from "@/client/api";
import { PLAYER_STAT_KEYS, type GoalEvent, type MatchEvent, type MatchSummary, type PlayerMatch,
  type PlayerMatchStats, type PlayerSeason, type PlayerSeasonStats, type TeamRef } from "@/contract";
import { PlayerMatches } from "@/features/players/PlayerMatches";

vi.mock("@/client/api", async importOriginal => ({
  ...await importOriginal<typeof import("@/client/api")>(), getSeasonMatches: vi.fn(),
}));
const team: TeamRef = { id: "team", name: "Real Jaén CF", shortName: "Jaén", crestUrl: null,
  colors: { primary: "#4b2677", secondary: "#ffffff" } };
const rival = { ...team, id: "rival", name: "CD Rival", shortName: "Rival" };
const match = (round: number, status: MatchSummary["status"] = "finished"): MatchSummary => ({
  id: `match-${round}`, seasonId: "season", round, kickoff: `2026-09-${String(round + 1).padStart(2, "0")}T10:00:00Z`,
  home: team, away: rival, status, score: status === "finished" ? { home: 2, away: 1 } : null,
});
const stats = Object.fromEntries(PLAYER_STAT_KEYS.map(key => [key, { value: 0 }])) as PlayerMatchStats;
const player = { id: "player", name: "Jugador de Prueba", position: "midfielder" as const,
  birthDate: null, heightCm: null, preferredFoot: null };
const appearance = (round: number, started = true, minutes = 90, events: MatchEvent[] = []): PlayerMatch => ({
  match: match(round), side: "home", started, stats: { ...stats, minutes: { value: minutes }, passes: { value: 52 }, passAccuracy: { value: 81.5 } }, events,
});
const profile: PlayerSeason = {
  player, team, shirtNumber: 14, season: { id: "season", name: "2026/27", competitionId: "competition", rounds: 38, currentRound: 5 },
  stats: {} as PlayerSeasonStats, matches: [appearance(1), appearance(3, false, 20)],
};
const goal = (id: string, overrides: Partial<GoalEvent> = {}): GoalEvent => ({
  id, matchId: "match-3", teamId: team.id, type: "goal", player, assist: null,
  minute: 80, addedTime: null, clip: null, ownGoal: false, penalty: false, ...overrides,
});
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getSeasonMatches).mockResolvedValue([match(5, "scheduled"), match(3), match(4, "postponed"), match(1), match(2)]);
});
async function load(data = profile, initialMatchId: string | null = null) {
  render(<PlayerMatches data={data} initialMatchId={initialMatchId} />);
  await screen.findByRole("list", { name: "Histórico de partidos" });
}
function detail() { return screen.getByRole("region", { name: "Detalle del partido" }); }
function history() { return screen.getByRole("list", { name: "Histórico de partidos" }); }
function historyItem(round: number) {
  return within(history()).getAllByRole("listitem").find(item => item.textContent?.includes("J" + round))!;
}
function historyRow(round: number) { return historyItem(round).firstElementChild as HTMLElement; }
function metric(label: string) { return within(detail()).getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement; }

describe("Player matches", () => {
  it("loads only the team calendar and lists finished matches with minutes and role, without a participation band", async () => {
    await load();
    expect(getSeasonMatches).toHaveBeenCalledWith("season", { teamId: "team" }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.queryByRole("heading", { name: "Participación" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Secuencia de participación por partido" })).toBeNull();
    const rows = within(history()).getAllByRole("listitem");
    expect(rows).toHaveLength(3);
    expect(within(history()).queryByText("J4")).toBeNull();
    expect(within(history()).queryByText("J5")).toBeNull();
    expect(rows[0]).toHaveTextContent("J3");
    expect(rows[0]).toHaveTextContent("Suplente · 20′");
    expect(rows[2]).toHaveTextContent("J1");
    expect(rows[2]).toHaveTextContent("Titular · 90′");
    expect(rows[1]).not.toHaveTextContent(/\d+′/);
    expect(rows[0]).toHaveTextContent("Real Jaén CF · CD Rival");
    expect(within(historyRow(3)).getByRole("link", { name: "Real Jaén CF" })).toHaveAttribute("href", "/teams?season=season&team=team");
    expect(within(historyRow(3)).getByRole("link", { name: "CD Rival" })).toHaveAttribute("href", "/teams?season=season&team=rival");
    expect(historyRow(3).querySelector("button a, a button")).toBeNull();
    expect(rows[0]).toHaveTextContent("Local");
    expect(rows[1]).toHaveTextContent("No participó");
    expect(within(rows[1]).queryByRole("button")).toBeNull();
    expect(rows[1]).not.toHaveTextContent(/lesión|sanción|convocado/);
  });

  it("selects the latest appearance initially and changes detail without navigation", async () => {
    await load({ ...profile, matches: [appearance(3, false, 20), appearance(1)] });
    const recent = within(history()).getByRole("button", { name: /J3/ });
    expect(recent).toHaveAttribute("aria-pressed", "true");
    expect(detail()).toHaveTextContent("J3 · vs CD Rival");
    expect(detail()).toHaveTextContent("Suplente · 20′");
    const older = within(history()).getByRole("button", { name: /J1/ });
    older.focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(older).toHaveAttribute("aria-pressed", "true");
    expect(recent).toHaveAttribute("aria-pressed", "false");
    expect(detail()).toHaveTextContent("Titular · 90′");
    expect(within(detail()).getByRole("link", { name: "Ver partido completo" })).toHaveAttribute("href", "/matches/match-1");
    expect(within(older).queryByRole("link")).toBeNull();
    const inline = screen.getByRole("region", { name: "Detalle inline del partido" });
    expect(inline.parentElement).toBe(older.closest("li"));
    expect(inline).toHaveTextContent("J1 · vs CD Rival");
    expect(older).toHaveAttribute("aria-expanded", "true");
  });

  it("synchronizes the historical row with both detail locations", async () => {
    await load();
    const first = within(history()).getByRole("button", { name: /J1/ });
    await userEvent.setup().click(first);
    expect(first).toHaveAttribute("aria-pressed", "true");
    expect(within(history()).getByRole("button", { name: /J3/ })).toHaveAttribute("aria-expanded", "false");
    expect(detail()).toHaveTextContent("J1 · vs CD Rival");
    const inline = screen.getByRole("region", { name: "Detalle inline del partido" });
    expect(inline).toHaveTextContent("J1 · vs CD Rival");
    expect(within(inline).getByRole("link", { name: "Ver partido completo" })).toHaveAttribute("href", "/matches/match-1");
  });

  it.each([
    ["below", 600, 680, 1000, 480],
    ["above", 20, 100, 1000, 120],
    ["visible", 120, 200, 1000, 200],
    ["below the visible page portion", 320, 380, 350, 230],
  ])("reveals an initially requested row %s the internal viewport without scrolling the page", async (_position, top, bottom, viewportHeight, expectedScroll) => {
    const pageScroll = vi.spyOn(window, "scrollTo");
    const isHistory = (element: Element) => element.getAttribute("aria-label") === "Histórico de partidos";
    // The history is a scrollable 300px viewport, already scrolled by 200px.
    vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockImplementation(function (this: HTMLElement) { return isHistory(this) ? 1000 : 0; });
    vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockImplementation(function (this: HTMLElement) {
      return isHistory(this) ? 300 : this === document.documentElement ? viewportHeight : 0;
    });
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      if (isHistory(this)) {
        if (!this.hasAttribute("data-scrolled")) { this.setAttribute("data-scrolled", ""); this.scrollTop = 200; }
        return { top: 100, bottom: 400 } as DOMRect;
      }
      if (this instanceof HTMLElement && this.dataset.matchId === "match-1") return { top, bottom } as DOMRect;
      return { top: 100, bottom: 100 } as DOMRect;
    });
    await load(profile, "match-1");
    // The reveal runs in a passive effect after the history is rendered.
    await waitFor(() => expect(history().scrollTop).toBe(expectedScroll));
    expect(within(history()).getByRole("button", { name: /J1/ })).toHaveAttribute("aria-pressed", "true");
    expect(pageScroll).not.toHaveBeenCalled();
    vi.restoreAllMocks();
  });

  it("selects the latest actual appearance even if more recent finished games have no appearance", async () => {
    vi.mocked(getSeasonMatches).mockResolvedValue([match(1), match(2), match(3), match(4)]);
    await load();
    expect(within(history()).getAllByRole("listitem")[0]).toHaveTextContent("J4");
    expect(within(history()).getByRole("button", { name: /J3/ })).toHaveAttribute("aria-pressed", "true");
    expect(detail()).toHaveTextContent("J3 · vs CD Rival");
  });

  it("shows all outfield groups, real zero and percentage without season coverage notes", async () => {
    await load();
    for (const title of ["Finalización", "Juego", "Recuperación", "Disciplina"]) {
      expect(within(detail()).getByRole("region", { name: title })).toBeVisible();
    }
    expect(metric("Tiros")).toHaveTextContent("0");
    expect(metric("Pases")).toHaveTextContent("52");
    expect(metric("Precisión de pase")).toHaveTextContent("81,5 %");
    expect(within(detail()).queryByText(/Datos disponibles en/)).toBeNull();
    expect(within(detail()).queryByText("Paradas")).toBeNull();
  });

  it("shows goalkeeper statistics instead of outfield performance", async () => {
    const keeper = appearance(3);
    await load({ ...profile, player: { ...player, position: "goalkeeper" }, matches: [{ ...keeper,
      stats: { ...keeper.stats, saves: { value: 4 }, goalsConceded: { value: 0 } } }] });
    expect(metric("Paradas")).toHaveTextContent("4");
    expect(metric("Goles encajados")).toHaveTextContent("0");
    expect(within(detail()).queryByText("Tiros")).toBeNull();
  });

  it.each(["not_available", "not_collected"] as const)("preserves %s without turning missing minutes into no participation", async reason => {
    const played = appearance(3);
    await load({ ...profile, matches: [{ ...played, stats: { ...played.stats,
      minutes: { value: null, reason }, passes: { value: null, reason } } }] });
    const label = reason === "not_collected" ? "Dato no recogido" : "Dato no disponible";
    expect(within(metric("Pases")).getByLabelText(label)).toHaveTextContent("—");
    expect(metric("Pases")).not.toHaveTextContent("0");
    expect(historyItem(3)).toHaveTextContent("Titular · —");
  });

  it("shows goals and credited assists in the row and preserves action added time", async () => {
    const scorer = { ...player, id: "scorer", name: "Autor del Gol" };
    await load({ ...profile, matches: [appearance(3, false, 4, [
      goal("g1"), goal("g2", { minute: 88, penalty: true }),
      goal("assist", { player: scorer, assist: player, minute: 90, addedTime: 3 }),
    ])] });
    const row = historyItem(3);
    expect(within(row).getByLabelText("2 goles")).toBeVisible();
    expect(within(row).getByLabelText("1 asistencia")).toBeVisible();
    expect(within(row).getByLabelText("1 asistencia").parentElement?.parentElement).toHaveTextContent("Suplente · 4′");
    const actions = within(detail()).getByRole("list", { name: "Acciones del jugador en el partido" });
    expect(within(actions).getAllByRole("listitem")).toHaveLength(3);
    expect(actions).toHaveTextContent("90+3′");
    expect(actions).toHaveTextContent("Asistencia · Gol de Autor del Gol");
    expect(actions).toHaveTextContent("Minuto del gol asistido");
    expect(actions).toHaveTextContent("Gol · Penalti");
  });

  it("shows an own goal explicitly as an action without counting it as an individual goal", async () => {
    await load({ ...profile, matches: [appearance(3, true, 90, [goal("own", { ownGoal: true })])] });
    const row = historyItem(3);
    expect(within(row).queryByLabelText(/\d+ goles?/)).toBeNull();
    expect(detail()).toHaveTextContent("Gol en propia puerta");
  });

  it.each(["yellow", "second_yellow", "red"] as const)("shows %s cards only in actions", async card => {
    const event: MatchEvent = { id: "card", matchId: "match-3", teamId: team.id, type: "card", player,
      minute: 45, addedTime: 2, clip: null, card };
    await load({ ...profile, matches: [appearance(3, true, 90, [event])] });
    const label = card === "yellow" ? "Amarilla" : card === "second_yellow" ? "Segunda amarilla" : "Roja";
    expect(within(detail()).getByRole("region", { name: "Acciones" })).toHaveTextContent(label);
    expect(detail()).toHaveTextContent("45+2′");
    expect(historyRow(3)).not.toHaveTextContent(label);
  });

  it.each([true, false])("shows substitution direction for entering=%s", async enters => {
    const other = { ...player, id: "other", name: "Otro Jugador" };
    const event: MatchEvent = { id: "sub", matchId: "match-3", teamId: team.id, type: "substitution",
      playerIn: enters ? player : other, playerOut: enters ? other : player, minute: 66, addedTime: null, clip: null };
    await load({ ...profile, matches: [appearance(3, !enters, enters ? 24 : 66, [event])] });
    const actions = within(detail()).getByRole("region", { name: "Acciones" });
    expect(actions).toHaveTextContent(enters ? "Entra" : "Sale");
    expect(actions).toHaveTextContent("66′");
  });

  it("keeps a genuine zero-minute appearance selectable", async () => {
    await load({ ...profile, matches: [appearance(3, false, 0)] });
    expect(historyItem(3)).toHaveTextContent("Suplente · 0′");
    expect(detail()).toHaveTextContent("Suplente · 0′");
  });

  it("shows nonparticipation for every finished match and no invented detail when appearances are empty", async () => {
    await load({ ...profile, matches: [] });
    expect(within(history()).getAllByText("No participó")).toHaveLength(3);
    expect(within(history()).queryByRole("button")).toBeNull();
    expect(detail()).toHaveTextContent("Sin apariciones esta temporada.");
    expect(within(detail()).queryByRole("definition")).toBeNull();
    expect(within(detail()).queryByRole("link")).toBeNull();
  });

  it("handles an empty finished calendar without treating scheduled games as absences", async () => {
    vi.mocked(getSeasonMatches).mockResolvedValue([match(5, "scheduled"), match(4, "postponed")]);
    await load({ ...profile, matches: [] });
    expect(screen.getByText("Todavía no hay partidos finalizados del equipo.")).toBeVisible();
    expect(within(history()).queryByRole("listitem")).toBeNull();
  });

  describe("initial match from navigation", () => {
    it("selects the requested appearance instead of the latest and synchronizes history and detail", async () => {
      await load(profile, "match-1");
      const row = within(history()).getByRole("button", { name: /J1/ });
      expect(row).toHaveAttribute("aria-pressed", "true");
      expect(row).toHaveAttribute("aria-expanded", "true");
      expect(within(history()).getByRole("button", { name: /J3/ })).toHaveAttribute("aria-pressed", "false");
      expect(detail()).toHaveTextContent("J1 · vs CD Rival");
      expect(detail()).toHaveTextContent("Titular · 90′");
      expect(screen.getByRole("region", { name: "Detalle inline del partido" })).toHaveTextContent("J1 · vs CD Rival");
      expect(screen.queryByRole("status")).toBeNull();
      // Later selections keep working as before.
      await userEvent.setup().click(within(history()).getByRole("button", { name: /J3/ }));
      expect(detail()).toHaveTextContent("Suplente · 20′");
    });

    it("selects a finished team match without appearance and shows nonparticipation without stats or reasons", async () => {
      await load(profile, "match-2");
      const row = within(history()).getAllByRole("listitem")[1];
      expect(row.firstElementChild).toHaveAttribute("data-selected", "true");
      expect(row).toHaveTextContent("No participó");
      expect(within(history()).getAllByRole("button").every(button => button.getAttribute("aria-pressed") === "false")).toBe(true);
      for (const region of [detail(), screen.getByRole("region", { name: "Detalle inline del partido" })]) {
        expect(region).toHaveTextContent("J2 · vs CD Rival");
        expect(region).toHaveTextContent("No participó");
        expect(region).toHaveTextContent("Sin minutos ni estadísticas individuales en este partido.");
        expect(within(region).queryByRole("definition")).toBeNull();
        expect(region).not.toHaveTextContent(/′|lesión|sanción|convocado/);
        expect(within(region).getByRole("link", { name: "Ver partido completo" })).toHaveAttribute("href", "/matches/match-2");
      }
      await userEvent.setup().click(within(history()).getByRole("button", { name: /J1/ }));
      expect(row.firstElementChild).not.toHaveAttribute("aria-current");
      expect(detail()).toHaveTextContent("Titular · 90′");
    });

    it.each([
      ["a nonexistent match", "missing"],
      ["a match outside the team calendar", "match-other-team"],
      ["a team match that is not finished", "match-5"],
    ])("selects nothing for %s and explains it", async (_case, matchId) => {
      await load(profile, matchId);
      expect(screen.getByRole("status")).toHaveTextContent("El partido indicado no figura entre los partidos finalizados de Real Jaén CF en 2026/27.");
      expect(within(history()).getAllByRole("button").every(button => button.getAttribute("aria-pressed") === "false")).toBe(true);
      expect(within(history()).getAllByRole("listitem").every(item => !item.querySelector("[aria-current]"))).toBe(true);
      expect(screen.queryByRole("region", { name: "Detalle inline del partido" })).toBeNull();
      expect(detail()).toHaveTextContent("Selecciona un partido del histórico.");
      expect(within(detail()).queryByRole("definition")).toBeNull();
      await userEvent.setup().click(within(history()).getByRole("button", { name: /J3/ }));
      expect(screen.queryByRole("status")).toBeNull();
      expect(detail()).toHaveTextContent("Suplente · 20′");
    });
  });
});
