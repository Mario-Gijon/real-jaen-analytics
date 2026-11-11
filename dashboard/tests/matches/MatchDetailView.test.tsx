import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, getCompetitions, getMatch, getMatchComments } from "@/client/api";
import { TEAM_STAT_KEYS, type MatchComments, type MatchDetail, type TeamMatchStats, type TeamRef } from "@/contract";
import { MatchDetailView } from "@/features/matches/MatchDetailView";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getMatch: vi.fn(), getCompetitions: vi.fn(), getMatchComments: vi.fn(),
}));
const home: TeamRef = {
  id: "home", name: "Real Jaén CF", shortName: "Real Jaén", crestUrl: null,
  colors: { primary: "#4b2677", secondary: "#ffffff" },
};
const away: TeamRef = { ...home, id: "away", name: "CD Rival", shortName: "Rival" };
const stats = Object.fromEntries(TEAM_STAT_KEYS.map((key) => [key, { value: 0 }])) as TeamMatchStats;
const match: MatchDetail = {
  id: "match-test", seasonId: "s1", round: 1, home, away,
  kickoff: "2026-08-22T10:00:00Z", status: "finished", score: { home: 2, away: 1 },
  lineups: null, events: [], playerStats: {},
  teamStats: { home: { ...stats, xg: { value: 1.98 }, shots: { value: 11 } },
    away: { ...stats, xg: { value: 1.39 }, shots: { value: 12 } } },
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getMatch).mockResolvedValue(match);
  vi.mocked(getMatchComments).mockResolvedValue({ matchId: match.id, availability: "not_collected", capturedAt: null,
    responseSha256: null, lastAttempt: { availability: "not_collected", occurredAt: null, httpStatus: null, errorCode: null },
    comments: [] } satisfies MatchComments);
  vi.mocked(getCompetitions).mockResolvedValue([{
    id: "c1", name: "Liga Demo", shortName: "Demo",
    seasons: [{ id: "s1", competitionId: "c1", name: "2026/27", rounds: 38, currentRound: 14 }],
  }]);
});

describe("Match quick summary", () => {
  it("loads through the typed client, identifies teams and compares statistics", async () => {
    render(<MatchDetailView matchId={match.id} />);
    expect(await screen.findByText("2 – 1")).toBeVisible();
    expect(getMatch).toHaveBeenCalledWith(match.id, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByText("Liga Demo · 2026/27")).toBeVisible();
    expect(screen.getByText("Jornada 1")).toBeVisible();
    expect(screen.getByText("Finalizado")).toBeVisible();
    expect(screen.getByLabelText("Real Jaén CF: 1,98")).toBeVisible();
    expect(screen.getByLabelText("CD Rival: 1,39")).toBeVisible();
    expect(screen.getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "Cronología" })).toBeEnabled();
    expect(screen.getByRole("tab", { name: "Alineaciones" })).toBeEnabled();
    expect(screen.getByRole("link", { name: /Volver a partidos/ })).toHaveAttribute("href", "/matches");
  });

  it("returns to the «Inicio» it was opened from, with its season and target", async () => {
    render(<MatchDetailView matchId="match-test" homeReturnHref="/?season=s1&match=target" />);
    expect(await screen.findByRole("link", { name: "‹ Volver a Inicio" })).toHaveAttribute("href", "/?season=s1&match=target");
    expect(screen.queryByRole("link", { name: /Volver a partidos/ })).toBeNull();
  });

  it("switches all sections by keyboard and restores summary", async () => {
    const user = userEvent.setup();
    render(<MatchDetailView matchId={match.id} />);
    await screen.findByText("2 – 1");
    screen.getByRole("tab", { name: "Resumen" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Cronología" })).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "Cronología" })).toHaveTextContent("No se registraron eventos del partido.");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Alineaciones" })).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "Alineaciones" })).toHaveTextContent("No hay alineaciones disponibles para este partido.");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Eventos" })).toHaveFocus();
    expect(await screen.findByRole("status")).toHaveTextContent("no está disponible para este partido");
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tabpanel", { name: "Resumen" })).toHaveTextContent("Estadísticas de equipo");
    await user.click(screen.getByRole("tab", { name: "Cronología" }));
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Resumen" })).toHaveFocus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Eventos" })).toHaveFocus();
    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: "Alineaciones" })).toHaveFocus();
  });

  it("keeps a 0–0 result and zero statistics as real values", async () => {
    vi.mocked(getMatch).mockResolvedValue({ ...match, score: { home: 0, away: 0 },
      teamStats: { home: stats, away: stats } });
    render(<MatchDetailView matchId={match.id} />);
    expect(await screen.findByText("0 – 0")).toBeVisible();
    const summary = screen.getByRole("tabpanel", { name: "Resumen" });
    expect(within(summary).getAllByLabelText("Real Jaén CF: 0")).toHaveLength(7);
    expect(within(summary).getByLabelText("Real Jaén CF: 0 %")).toBeVisible();
    expect(within(summary).queryByText("—")).toBeNull();
    expect(summary.querySelector('[data-bar]')).toBeNull();
    expect(summary.querySelector('[data-comparison]')).toBeNull();
  });

  it.each(["not_available", "not_collected"] as const)("preserves %s without substituting zero", async (reason) => {
    vi.mocked(getMatch).mockResolvedValue({ ...match, teamStats: {
      home: { ...stats, xg: { value: null, reason } }, away: { ...stats, xg: { value: 2 } },
    } });
    render(<MatchDetailView matchId={match.id} />);
    const missing = await screen.findByLabelText(`Real Jaén CF: ${reason === "not_collected" ? "Dato no recogido" : "Dato no disponible"}`);
    expect(missing).toHaveTextContent("—");
    expect(screen.getAllByLabelText("CD Rival: 0")).toHaveLength(6);
    expect(screen.getByLabelText("CD Rival: 2,00")).toBeVisible();
    expect(missing.closest("dd")!.parentElement!.querySelector('[data-bar]')).toBeNull();
    expect(missing.closest("dd")!.parentElement!.querySelector('[data-comparison]')).toBeNull();
  });

  it.each([[12, 16, "75%", "100%"], [16, 12, "100%", "75%"]])(
    "scales %s vs %s independently against the larger value", async (local, visitor, localWidth, visitorWidth) => {
      vi.mocked(getMatch).mockResolvedValue({ ...match, teamStats: {
        home: { ...stats, shots: { value: Number(local) } }, away: { ...stats, shots: { value: Number(visitor) } },
      } });
      render(<MatchDetailView matchId={match.id} />);
      const value = await screen.findByLabelText(`Real Jaén CF: ${local}`);
      const row = value.closest("dd")!.parentElement!;
      expect(row.querySelector('[data-bar="home"]')).toHaveStyle({ width: localWidth });
      expect(row.querySelector('[data-bar="away"]')).toHaveStyle({ width: visitorWidth });
    },
  );

  it.each(["home", "away"] as const)("draws no bar for zero on the %s side", async (zeroSide) => {
    vi.mocked(getMatch).mockResolvedValue({ ...match, teamStats: {
      home: { ...stats, shots: { value: zeroSide === "home" ? 0 : 16 } },
      away: { ...stats, shots: { value: zeroSide === "away" ? 0 : 16 } },
    } });
    render(<MatchDetailView matchId={match.id} />);
    const positive = await screen.findByLabelText(`${zeroSide === "home" ? away.name : home.name}: 16`);
    const row = positive.closest("dd")!.parentElement!;
    expect(within(row).getByLabelText(`${zeroSide === "home" ? home.name : away.name}: 0`)).toHaveTextContent("0");
    expect(row.querySelector(`[data-bar="${zeroSide}"]`)).toBeNull();
    expect(row.querySelector('[data-bar]')).toHaveStyle({ width: "100%" });
  });

  it("shows kickoff and an explicit empty summary for a scheduled match", async () => {
    vi.mocked(getMatch).mockResolvedValue({ ...match, status: "scheduled", score: null, teamStats: null });
    render(<MatchDetailView matchId={match.id} />);
    expect(await screen.findByText("Programado")).toBeVisible();
    expect(screen.getByText("12:00")).toBeVisible();
    expect(screen.getByText(/22 ago 2026 · 12:00/)).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Las estadísticas estarán disponibles después del partido.");
    expect(screen.queryByText("2 – 1")).toBeNull();
  });

  it("handles a postponed match without a date, result or statistics", async () => {
    vi.mocked(getMatch).mockResolvedValue({ ...match, status: "postponed", kickoff: null, score: null, teamStats: null });
    render(<MatchDetailView matchId={match.id} />);
    expect(await screen.findByText("Aplazado")).toBeVisible();
    expect(screen.getByText("Fecha por definir")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Partido aplazado. No hay estadísticas disponibles.");
  });

  it("offers retry on a failed request", async () => {
    vi.mocked(getMatch).mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    render(<MatchDetailView matchId={match.id} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo cargar el partido");
    await user.click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("2 – 1")).toBeVisible();
  });

  it("distinguishes an unknown match from a transient failure", async () => {
    vi.mocked(getMatch).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "Partido no encontrado."));
    render(<MatchDetailView matchId="unknown" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Partido no encontrado");
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
  });
});
