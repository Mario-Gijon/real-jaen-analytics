import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError, getAllEnrichedComments, getMatchVideo, getPlayerObservedMatches, getPlayerProfile, locateMatchVideoEvent,
} from "@/client/api";
import {
  OBSERVED_MATCH_STAT_KEYS, type EnrichedComment, type EnrichedCommentsPage, type EnrichedParticipant,
  type MatchVideoReference, type ObservedPlayerMatch, type PlayerProfile, type Stat, type VideoEventLocation,
} from "@/contract";
import { PlayerMatchEvents } from "@/features/players/PlayerMatchEvents";
import { PlayerProfileView } from "@/features/players/PlayerProfileView";

// Same pattern as MatchCommentsPanel's tests: a stand-in that exposes the seek request and counts mounts.
const youtube = vi.hoisted(() => ({ mounts: 0 }));
vi.mock("@/features/matches/YouTubePlayer", async () => {
  const { useEffect } = await import("react");
  return {
    YouTubePlayer: ({ videoId, seekRequest }: { videoId: string; seekRequest: { sequence: number; seconds: number } | null }) => {
      useEffect(() => { youtube.mounts++; }, []);
      return <div data-testid="video-player" data-video-id={videoId}
        data-seek-to={seekRequest?.seconds ?? ""} data-sequence={seekRequest?.sequence ?? ""} />;
    },
  };
});

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getAllEnrichedComments: vi.fn(), getMatchVideo: vi.fn(), locateMatchVideoEvent: vi.fn(),
  getPlayerProfile: vi.fn(), getPlayerObservedMatches: vi.fn(),
}));

// Real roles and ids (match 16599928 and 16599363 of the local data).
const KEITA = "sofascore-player-966303";
const SIVERIO = "sofascore-player-942663";
const MATCH = "sofascore-match-16599363";

function participant(sourceRole: string, role: string | null, sourceId: number, name: string): EnrichedParticipant {
  return { sourceRole, role, normalizerRole: null, sourceId, name, player: { id: `sofascore-player-${sourceId}`, name, position: null } };
}
let comment = 0;
function goal(minute: number, position: number, participants: EnrichedParticipant[], overrides: Partial<EnrichedComment> = {}): EnrichedComment {
  comment++;
  return {
    id: String(40778800 + comment), captureId: 3, position, text: "¡Gol!", originalType: "scoreChange", minute, periodName: "2ND",
    isHome: false, score: { home: 0, away: 1 }, category: "goal",
    attributes: { goal_status: "valid", goal_kind: "ordinary" }, participants, ...overrides,
  };
}
const page = (items: EnrichedComment[]): EnrichedCommentsPage => ({ enrichmentVersion: "rules-v1", total: items.length, limit: 100, offset: 0, items });
const count = (value: number): Stat => ({ value });

const siverioGoal = goal(63, 40, [participant("player", "scorer", 942663, "Marco Siverio Toro"), participant("assist1", "assistant", 861277, "Antonio Caballero")]);
const keitaGoal = goal(76, 21, [participant("player", "scorer", 966303, "Moha Keita"), participant("assist1", "assistant", 1545211, "David Serrano")]);
const keitaAssist = goal(69, 30, [participant("player", "scorer", 942663, "Marco Siverio Toro"), participant("assist1", "assistant", 966303, "Moha Keita")]);
const siverioPenalty = goal(14, 98, [participant("player", "penalty_scorer", 942663, "Marco Siverio Toro")],
  { originalType: "penaltyScored", periodName: "1ST", score: null });
const ownGoal = goal(80, 24, [{ ...participant("player", "own_goal_author", 966303, "Moha Keita"), normalizerRole: "scorer" },
  participant("assist1", "assistant", 942663, "Marco Siverio Toro")], { attributes: { goal_status: "valid", goal_kind: "own_goal" } });
const cancelled = goal(55, 69, [participant("player", "disallowed_scorer", 966303, "Moha Keita")],
  { originalType: "varCancelledGoal", category: "goal_review", score: null, attributes: { goal_status: "disallowed", goal_kind: null } });

const heading = () => screen.getByRole("heading", { level: 3, name: "Eventos" });
const card = () => screen.getByRole("region", { name: "Eventos del partido" });
/** Each row as [minute tag, event (main line), related information (secondary line, or null)]. */
const events = () => within(card()).getAllByRole("listitem").map((row) => [
  row.querySelector("[class*=minute]")!.textContent, row.querySelector("strong")!.textContent,
  row.querySelector("[class*=detail]")?.textContent ?? null,
]);
const NOTICE = "La narración menciona posibles asistencias, pero no se pueden confirmar con las estadísticas disponibles y no se atribuyen.";

beforeEach(() => {
  vi.resetAllMocks();
  youtube.mounts = 0;
  vi.mocked(getMatchVideo).mockResolvedValue({ available: false });
});

describe("PlayerMatchEvents", () => {
  it("titles the section with a static zone title, without any expand control", () => {
    vi.mocked(getAllEnrichedComments).mockReturnValue(new Promise(() => {}));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(heading().className).toMatch(/zoneTitle/);
    expect(heading()).toHaveTextContent(/^Eventos$/);
    expect(screen.getByRole("region", { name: "Eventos" })).toContainElement(heading());
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector("[aria-expanded], [aria-controls], [hidden]")).toBeNull();
  });

  it("loads the match's events once on mount and shows them directly", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaGoal]));
    const { rerender } = render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(getAllEnrichedComments).toHaveBeenCalledWith("16599363", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(await screen.findByText("Según la narración, asistencia de David Serrano")).toBeVisible();
    // Re-rendering the same match reuses the answer.
    rerender(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(screen.getByText("Según la narración, asistencia de David Serrano")).toBeVisible();
    expect(getAllEnrichedComments).toHaveBeenCalledTimes(1);
  });

  it("shows a loading state, never the empty message, while loading", async () => {
    let resolve!: (value: EnrichedCommentsPage) => void;
    vi.mocked(getAllEnrichedComments).mockReturnValue(new Promise((done) => { resolve = done; }));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(screen.getByRole("status")).toHaveTextContent("Cargando eventos…");
    expect(screen.queryByText(/Sin goles ni asistencias/)).toBeNull();
    resolve(page([]));
    expect(await screen.findByText("Sin goles ni asistencias registrados en este partido.")).toBeVisible();
  });

  it("never shows a previous match's events while the new one loads", async () => {
    let resolve!: (value: EnrichedCommentsPage) => void;
    vi.mocked(getAllEnrichedComments).mockResolvedValueOnce(page([keitaGoal]))
      .mockReturnValueOnce(new Promise((done) => { resolve = done; }));
    const { rerender } = render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    await screen.findByText("Según la narración, asistencia de David Serrano");
    rerender(<PlayerMatchEvents matchId="sofascore-match-16599928" playerId={KEITA} assists={count(0)} />);
    expect(screen.queryByText("Según la narración, asistencia de David Serrano")).toBeNull();
    expect(screen.getByRole("status")).toHaveTextContent("Cargando eventos…");
    resolve(page([]));
    expect(await screen.findByText("Sin goles ni asistencias registrados en este partido.")).toBeVisible();
    expect(getAllEnrichedComments).toHaveBeenLastCalledWith("16599928", expect.anything());
  });

  it("lists the player's goals, penalty goals and confirmed assists in match order, without own or disallowed goals", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaGoal, ownGoal, siverioPenalty, keitaAssist, cancelled, siverioGoal]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(0)} />);
    await screen.findByRole("region", { name: "Eventos del partido" });
    expect(events()).toEqual([
      ["14′", "Gol de penalti", null], ["63′", "Gol", "Según la narración, asistencia de Antonio Caballero"], ["69′", "Gol", "Según la narración, asistencia de Moha Keita"],
    ]);
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("shows confirmed assists with the scorer, and no own or disallowed goal of the player", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaGoal, ownGoal, keitaAssist, cancelled]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(1)} />);
    await screen.findByRole("region", { name: "Eventos del partido" });
    expect(events()).toEqual([["69′", "Asistencia confirmada", "Gol de Marco Siverio Toro"], ["76′", "Gol", "Según la narración, asistencia de David Serrano"]]);
    expect(screen.queryByText(NOTICE)).toBeNull();
  });

  it("omits assists the official count does not confirm, with a notice, keeping the goals", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaGoal, keitaAssist]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(await screen.findByText(NOTICE)).toBeVisible();
    expect(events()).toEqual([["76′", "Gol", "Según la narración, asistencia de David Serrano"]]);
  });

  it("shows the empty state, with the notice, when the only assists are unconfirmed", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaAssist]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={{ value: null, reason: "not_available" }} />);
    expect(await screen.findByText("No hay goles ni asistencias confirmadas que mostrar en este partido.")).toBeVisible();
    expect(screen.getByText(NOTICE)).toBeVisible();
  });

  it("reports assists in the official stat when no identifiable narrative event matches", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(1)} />);
    expect(await screen.findByText(
      "Las estadísticas oficiales indican asistencias, pero no se han podido identificar todos sus eventos; se omiten las atribuciones no confirmadas.",
    )).toBeVisible();
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("labels a scorer's narrative mention without presenting it as the assistant's confirmed event", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaAssist]));
    const view = render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(await screen.findByText(NOTICE)).toBeVisible();
    expect(screen.queryByText("Asistencia confirmada")).toBeNull();

    view.rerender(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(0)} />);
    expect(await screen.findByText("Según la narración, asistencia de Moha Keita")).toBeVisible();
    expect(screen.queryByText("Asistencia confirmada")).toBeNull();
  });

  it("reports an error and retries", async () => {
    vi.mocked(getAllEnrichedComments)
      .mockRejectedValueOnce(new ApiRequestError(503, "ENRICHMENT_INCOMPLETE", "No se pudieron cargar todos los eventos del partido."))
      .mockResolvedValueOnce(page([keitaGoal]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No se pudieron cargar los eventos. No se pudieron cargar todos los eventos del partido.");
    expect(screen.queryByText(/Sin goles ni asistencias/)).toBeNull();
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("Según la narración, asistencia de David Serrano")).toBeVisible();
    expect(getAllEnrichedComments).toHaveBeenCalledTimes(2);
  });

  it("says the events are not available for a match without a SofaScore id, requesting nothing", () => {
    render(<PlayerMatchEvents matchId="synthetic-match-4" playerId={KEITA} assists={count(0)} />);
    expect(screen.getByText("Los eventos de este partido no están disponibles.")).toBeVisible();
    expect(getAllEnrichedComments).not.toHaveBeenCalled();
  });

  it("shows minute tag, icon and two-line text, with stoppage time and no invented information", async () => {
    const unnamed = goal(50, 45, [participant("player", "scorer", 942663, "Marco Siverio Toro"),
      { ...participant("assist1", "assistant", 861277, "x"), name: null }]);
    const firstHalfStoppage = goal(47, 70, [participant("player", "scorer", 942663, "Marco Siverio Toro")], { periodName: "1ST" });
    const secondHalfStoppage = goal(92, 10, [participant("player", "scorer", 966303, "Moha Keita"),
      participant("assist1", "assistant", 942663, "Marco Siverio Toro")]);
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([siverioPenalty, unnamed, firstHalfStoppage, secondHalfStoppage]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(1)} />);
    await screen.findByRole("region", { name: "Eventos del partido" });
    expect(card().className).toMatch(/statBlock/);
    expect(events()).toEqual([
      ["14′", "Gol de penalti", null], ["45+2′", "Gol", null], ["50′", "Gol", null], ["90+2′", "Asistencia confirmada", "Gol de Moha Keita"],
    ]);
    // Existing goal and assist icons, decorative here: the text already says the kind.
    const rows = within(card()).getAllByRole("listitem");
    expect(rows.map((row) => row.querySelector("[aria-hidden=true] svg")?.getAttribute("aria-label")))
      .toEqual(["Gol", "Gol", "Gol", "Asistencia"]);
    expect(within(card()).queryByRole("img")).toBeNull();
    expect(within(card()).queryByText("—")).toBeNull();
  });

  it("without a video, shows no Play nor player and says so", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaGoal]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={KEITA} assists={count(0)} />);
    expect(await screen.findByText("Este partido no tiene vídeo asociado.")).toBeVisible();
    expect(screen.getByText("Según la narración, asistencia de David Serrano")).toBeVisible();
    expect(getMatchVideo).toHaveBeenCalledWith(MATCH, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByTestId("video-player")).toBeNull();
    expect(locateMatchVideoEvent).not.toHaveBeenCalled();
  });
});

describe("Eventos in the player profile", () => {
  const missing = { value: null, reason: "not_available" } as const;
  const noStats = Object.fromEntries(OBSERVED_MATCH_STAT_KEYS.map((key) => [key, missing])) as ObservedPlayerMatch["stats"];
  const base: ObservedPlayerMatch = {
    match: { id: MATCH, kickoff: "2026-11-15T16:15:00Z", status: "finished", competition: "Primera Federación", season: "26/27",
      round: 12, home: { id: "sofascore-team-1", name: "CE Europa" }, away: { id: "team-real-jaen", name: "Real Jaén" },
      score: { home: 0, away: 3 } },
    side: "away", role: "starter", position: "midfielder", participation: "confirmed",
    stats: { ...noStats, minutes: { value: 90 }, goals: { value: 1 }, assists: { value: 1 } },
  };
  const other: ObservedPlayerMatch = {
    ...base, match: { ...base.match, id: "sofascore-match-16599928", kickoff: "2026-11-08T16:15:00Z", round: 11,
      home: { id: "team-real-jaen", name: "Real Jaén" }, away: { id: "sofascore-team-2", name: "Rayo Majadahonda" } },
    side: "home", stats: { ...noStats, minutes: { value: 90 }, goals: { value: 0 }, assists: { value: 0 } },
  };
  const unknown: ObservedPlayerMatch = { ...other, match: { ...other.match, id: "sofascore-match-16599100", round: 10 },
    participation: "unknown", stats: noStats };
  const keita: PlayerProfile = {
    player: { id: KEITA, name: "Moha Keita", position: "midfielder", birthDate: null, heightCm: null, preferredFoot: null, country: null },
    scope: "", source: { synthetic: false, label: "Datos reales" },
  };

  async function profile(matches: ObservedPlayerMatch[]) {
    vi.mocked(getPlayerProfile).mockResolvedValue(keita);
    vi.mocked(getPlayerObservedMatches).mockResolvedValue({ playerId: KEITA, matches, scope: "", ratingSource: "SofaScore",
      source: { synthetic: false, label: "Datos reales" } });
    render(<PlayerProfileView playerId={KEITA} returnHref="/teams" />);
    await screen.findByRole("list", { name: "Histórico de partidos" });
  }

  it("sits in the confirmed match detail and loads only that match's events, not the whole history", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([keitaGoal, keitaAssist]));
    await profile([base, other]);
    const detail = screen.getByRole("region", { name: "Detalle del partido" });
    expect(within(detail).getByRole("heading", { level: 3, name: "Eventos" })).toBeVisible();
    await screen.findByRole("region", { name: "Eventos del partido" });
    expect(events()).toEqual([["69′", "Asistencia confirmada", "Gol de Marco Siverio Toro"], ["76′", "Gol", "Según la narración, asistencia de David Serrano"]]);
    expect(within(detail).getByRole("region", { name: "Eventos" })).toBeVisible();
    // Only the open match: the other call-up's detail is not mounted, so it requests nothing.
    expect(getAllEnrichedComments).toHaveBeenCalledTimes(1);
    expect(getAllEnrichedComments).toHaveBeenCalledWith("16599363", expect.anything());
  });

  it("loads the newly selected match, without the previous match's events", async () => {
    vi.mocked(getAllEnrichedComments).mockImplementation(async (eventId) =>
      eventId === "16599363" ? page([keitaGoal, keitaAssist]) : page([goal(32, 90, [
        participant("player", "scorer", 1010341, "Peque Polo"), participant("assist1", "assistant", 966303, "Moha Keita")])]));
    await profile([base, other]);
    await screen.findByText("Según la narración, asistencia de David Serrano");
    await userEvent.setup().click(screen.getByRole("button", { name: /^Seleccionar J11/ }));
    expect(screen.queryByText("Según la narración, asistencia de David Serrano")).toBeNull();
    // Moha Keita, 16599928: the assist is not in his official stats.
    expect(await screen.findByText("No hay goles ni asistencias confirmadas que mostrar en este partido.")).toBeVisible();
    expect(screen.getByText(NOTICE)).toBeVisible();
    expect(getAllEnrichedComments).toHaveBeenLastCalledWith("16599928", expect.anything());
    await waitFor(() => expect(getAllEnrichedComments).toHaveBeenCalledTimes(2));
  });

  it("is absent without confirmed participation, and keeps the photo fallback", async () => {
    await profile([unknown]);
    expect(screen.queryByRole("heading", { name: "Eventos" })).toBeNull();
    expect(getAllEnrichedComments).not.toHaveBeenCalled();
    expect(document.querySelector("[data-avatar]")).toBeTruthy();
  });
});

describe("Event playback", () => {
  const VIDEO = { available: true, youtubeVideoId: "h08Q4YlEiAc", synchronizationStatus: "calibrated" } as const;
  const resolved = (videoSecond: number): VideoEventLocation => ({ status: "resolved", videoSecond,
    eventVideoSecond: videoSecond, eventClockSecond: 3720, eventClockSecondEstimate: 3760, leadSeconds: 0, estimated: true,
    extrapolatedSeconds: 0, method: "minute_tail", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [] });
  const play63 = () => screen.getByRole("button", { name: "Reproducir gol del minuto 63" });
  const play69 = () => screen.getByRole("button", { name: "Reproducir gol del minuto 69" });
  const player = () => screen.getByTestId("video-player");
  function deferred() {
    let resolve!: (value: VideoEventLocation) => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<VideoEventLocation>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
  }
  async function siverio(video: MatchVideoReference = VIDEO, items: EnrichedComment[] = [siverioGoal, keitaAssist]) {
    vi.mocked(getMatchVideo).mockResolvedValue(video);
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    render(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(0)} />);
    await screen.findByRole("region", { name: "Eventos del partido" });
  }

  it("loads events without waiting for the video reference, and shows Play once a video exists", async () => {
    let resolveVideo!: (value: MatchVideoReference) => void;
    vi.mocked(getMatchVideo).mockReturnValue(new Promise((done) => { resolveVideo = done; }));
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([siverioGoal, keitaAssist]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(0)} />);
    expect(getMatchVideo).toHaveBeenCalledTimes(1);
    expect(getAllEnrichedComments).toHaveBeenCalledTimes(1);
    await screen.findByRole("region", { name: "Eventos del partido" });
    expect(screen.queryByRole("button")).toBeNull();
    resolveVideo(VIDEO);
    expect(await screen.findByRole("button", { name: "Reproducir gol del minuto 63" })).toBeVisible();
    expect(play69()).toHaveAttribute("aria-pressed", "false");
    // Nothing located nor mounted until Play.
    expect(locateMatchVideoEvent).not.toHaveBeenCalled();
    expect(screen.queryByTestId("video-player")).toBeNull();
    expect(youtube.mounts).toBe(0);
  });

  it("names each Play by event kind and minute, stoppage time included", async () => {
    const penalty = goal(14, 98, [participant("player", "penalty_scorer", 942663, "Marco Siverio Toro")], { originalType: "penaltyScored", periodName: "1ST" });
    const stoppage = goal(47, 70, [participant("player", "scorer", 942663, "Marco Siverio Toro")], { periodName: "1ST" });
    const assist = goal(92, 10, [participant("player", "scorer", 966303, "Moha Keita"), participant("assist1", "assistant", 942663, "Marco Siverio Toro")]);
    vi.mocked(getMatchVideo).mockResolvedValue(VIDEO);
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([penalty, stoppage, assist]));
    render(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(1)} />);
    expect((await screen.findAllByRole("button")).map((button) => button.getAttribute("aria-label"))).toEqual([
      "Reproducir gol de penalti del minuto 14", "Reproducir gol del minuto 45+2", "Reproducir asistencia confirmada del minuto 90+2",
    ]);
  });

  it("reports a failed video lookup with a retry, keeping the events", async () => {
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([siverioGoal]));
    vi.mocked(getMatchVideo).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo."))
      .mockResolvedValueOnce(VIDEO);
    render(<PlayerMatchEvents matchId={MATCH} playerId={SIVERIO} assists={count(0)} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("No se pudo consultar el vídeo del partido. No se pudo consultar el servicio de vídeo.");
    expect(screen.getByText("Según la narración, asistencia de Antonio Caballero")).toBeVisible();
    await userEvent.setup().click(within(alert).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("button", { name: "Reproducir gol del minuto 63" })).toBeVisible();
    expect(getMatchVideo).toHaveBeenCalledTimes(2);
  });

  it("locates on Play with the shared parameters and mounts one player 45 s before the located second", async () => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolved(2600));
    await siverio();
    await userEvent.setup().click(play63());
    expect(locateMatchVideoEvent).toHaveBeenCalledWith(MATCH, "second", "63", "other", undefined,
      expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2555");
    expect(player()).toHaveAttribute("data-video-id", "h08Q4YlEiAc");
    expect(play63()).toHaveAttribute("aria-pressed", "true");
    expect(play63().closest("li")).toHaveAttribute("data-selected", "true");
    expect(play69()).toHaveAttribute("aria-pressed", "false");
  });

  it("never seeks before 0", async () => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValueOnce(resolved(30)).mockResolvedValueOnce(resolved(45));
    await siverio();
    const user = userEvent.setup();
    await user.click(play63());
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "0");
    await user.click(play69());
    await waitFor(() => expect(player()).toHaveAttribute("data-sequence", "2"));
    expect(player()).toHaveAttribute("data-seek-to", "0");
  });

  it("reuses the single player for another event and for the same event again, without locating it twice", async () => {
    vi.mocked(locateMatchVideoEvent).mockImplementation(async (_match, _period, minute) => resolved(minute === "63" ? 2600 : 2960));
    await siverio();
    const user = userEvent.setup();
    await user.click(play63());
    await screen.findByTestId("video-player");
    await user.click(play69());
    await waitFor(() => expect(player()).toHaveAttribute("data-seek-to", "2915"));
    expect(play69()).toHaveAttribute("aria-pressed", "true");
    expect(play63()).toHaveAttribute("aria-pressed", "false");
    await user.click(play63());
    await waitFor(() => expect(player()).toHaveAttribute("data-sequence", "3"));
    expect(player()).toHaveAttribute("data-seek-to", "2555");
    expect(screen.getAllByTestId("video-player")).toHaveLength(1);
    expect(youtube.mounts).toBe(1);
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(2);
  });

  it("locates first-half stoppage time as 45+N and keeps second-half minutes", async () => {
    const stoppage = goal(47, 70, [participant("player", "scorer", 942663, "Marco Siverio Toro")], { periodName: "1ST" });
    const late = goal(92, 10, [participant("player", "scorer", 942663, "Marco Siverio Toro")]);
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolved(1000));
    await siverio(VIDEO, [stoppage, late]);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Reproducir gol del minuto 45+2" }));
    await user.click(screen.getByRole("button", { name: "Reproducir gol del minuto 90+2" }));
    await waitFor(() => expect(locateMatchVideoEvent).toHaveBeenCalledTimes(2));
    expect(vi.mocked(locateMatchVideoEvent).mock.calls.map((call) => call.slice(1, 4))).toEqual([
      ["first", "45+2", "other"], ["second", "92", "other"],
    ]);
  });

  it.each([
    ["ambiguous", { status: "ambiguous" } as const, "Hay más de una posición posible."],
    ["uncertain", { status: "uncertain", reason: "following_reference_evidence_insufficient" } as const,
      "La referencia OCR posterior no reúne suficientes lecturas consistentes."],
    ["out_of_coverage", { status: "out_of_coverage" } as const, "El minuto queda fuera de la cobertura OCR verificada."],
  ])("does not seek on %s and explains why next to the event", async (_name, location, reason) => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(location);
    await siverio();
    await userEvent.setup().click(play63());
    expect(await screen.findByText(`No se puede reproducir: ${reason}`)).toBeVisible();
    expect(play63()).toHaveAttribute("aria-disabled", "true");
    expect(play63()).toHaveAttribute("aria-pressed", "false");
    expect(play69()).not.toHaveAttribute("aria-disabled");
    // Ignored while it cannot be replayed: no new request, no player.
    await userEvent.setup().click(play63());
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("video-player")).toBeNull();
  });

  it("does not seek to an unusable resolved second", async () => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolved(-5));
    await siverio();
    await userEvent.setup().click(play63());
    expect(await screen.findByText("No se puede reproducir: La posición devuelta para este evento no es válida.")).toBeVisible();
    expect(screen.queryByTestId("video-player")).toBeNull();
  });

  it("lets a failed location be retried, without caching the failure", async () => {
    vi.mocked(locateMatchVideoEvent)
      .mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No se pudo consultar el servicio de vídeo."))
      .mockResolvedValueOnce(resolved(2600));
    await siverio();
    const user = userEvent.setup();
    await user.click(play63());
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo localizar el evento en el vídeo. No se pudo consultar el servicio de vídeo.");
    expect(screen.queryByTestId("video-player")).toBeNull();
    const retry = screen.getByRole("button", { name: "Reintentar la reproducción de gol del minuto 63" });
    expect(retry).not.toHaveAttribute("aria-disabled");
    await user.click(retry);
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2555");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(2);
  });

  it("shows activity while locating, ignores repeated presses on that event, and keeps others selectable", async () => {
    const pending = deferred();
    vi.mocked(locateMatchVideoEvent).mockReturnValueOnce(pending.promise).mockResolvedValueOnce(resolved(2960));
    await siverio();
    const user = userEvent.setup();
    await user.click(play63());
    const busy = screen.getByRole("button", { name: "Localizando gol del minuto 63 en el vídeo" });
    expect(busy).toHaveAttribute("aria-busy", "true");
    expect(busy).toHaveAttribute("aria-disabled", "true");
    await user.click(busy);
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(1);
    await user.click(play69());
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2915");
    pending.resolve(resolved(2600));
    await waitFor(() => expect(play63()).not.toHaveAttribute("aria-disabled"));
  });

  it("lets the latest Play win over an answer that arrives later", async () => {
    const first = deferred();
    const second = deferred();
    vi.mocked(locateMatchVideoEvent).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    await siverio();
    const user = userEvent.setup();
    await user.click(play63());
    await user.click(play69());
    second.resolve(resolved(2960));
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2915");
    first.resolve(resolved(2600));
    await waitFor(() => expect(play63()).toHaveAccessibleName("Reproducir gol del minuto 63"));
    expect(player()).toHaveAttribute("data-seek-to", "2915");
    expect(player()).toHaveAttribute("data-sequence", "1");
    expect(play69()).toHaveAttribute("aria-pressed", "true");
    expect(play63()).toHaveAttribute("aria-pressed", "false");
  });

  it("keeps keyboard focus on Play while locating and after, ignoring repeated presses meanwhile", async () => {
    const pending = deferred();
    vi.mocked(locateMatchVideoEvent).mockReturnValueOnce(pending.promise);
    await siverio();
    const user = userEvent.setup();
    play63().focus();
    await user.keyboard("{Enter}");
    const busy = screen.getByRole("button", { name: "Localizando gol del minuto 63 en el vídeo" });
    // jsdom does not move focus off disabled elements as Chrome does: this checks the button
    // stays focusable (never `disabled`); the real focus behavior is checked in the browser.
    expect(busy).not.toBeDisabled();
    expect(busy).toHaveFocus();
    await user.keyboard("{Enter}{Enter}");
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(1);
    pending.resolve(resolved(2600));
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2555");
    expect(play63()).toHaveFocus();
    expect(play63()).not.toHaveAttribute("aria-disabled");
    expect(play63()).toHaveAttribute("aria-pressed", "true");
    expect(player()).toHaveAttribute("data-sequence", "1");
    // Available again: another press replays it.
    await user.keyboard("{Enter}");
    await waitFor(() => expect(player()).toHaveAttribute("data-sequence", "2"));
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(1);
  });

  it("keeps an event that cannot be replayed focusable, with its reason", async () => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValue({ status: "out_of_coverage" });
    await siverio();
    const user = userEvent.setup();
    play63().focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("No se puede reproducir: El minuto queda fuera de la cobertura OCR verificada.")).toBeVisible();
    expect(play63()).toHaveFocus();
    expect(play63()).toHaveAttribute("aria-disabled", "true");
  });

  it("plays with the keyboard", async () => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolved(2600));
    await siverio();
    play63().focus();
    await userEvent.setup().keyboard("{Enter}");
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2555");
  });

  it.each(["uncertain", "not_calibrated"] as const)("tries to locate with a %s video and plays only a resolved location", async (status) => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValueOnce({ status: "uncertain" }).mockResolvedValueOnce(resolved(2960));
    await siverio({ ...VIDEO, synchronizationStatus: status });
    const user = userEvent.setup();
    await user.click(play63());
    expect(await screen.findByText(/^No se puede reproducir:/)).toBeVisible();
    expect(screen.queryByTestId("video-player")).toBeNull();
    await user.click(play69());
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "2915");
  });
});

describe("Event playback across matches in the player profile", () => {
  const VIDEO = { available: true, youtubeVideoId: "h08Q4YlEiAc", synchronizationStatus: "calibrated" } as const;
  const missing = { value: null, reason: "not_available" } as const;
  const noStats = Object.fromEntries(OBSERVED_MATCH_STAT_KEYS.map((key) => [key, missing])) as ObservedPlayerMatch["stats"];
  const europa: ObservedPlayerMatch = {
    match: { id: MATCH, kickoff: "2026-11-15T16:15:00Z", status: "finished", competition: "Primera Federación", season: "26/27",
      round: 12, home: { id: "sofascore-team-1", name: "CE Europa" }, away: { id: "team-real-jaen", name: "Real Jaén" },
      score: { home: 0, away: 3 } },
    side: "away", role: "starter", position: "forward", participation: "confirmed",
    stats: { ...noStats, minutes: { value: 73 }, goals: { value: 2 }, assists: { value: 0 } },
  };
  const rayo: ObservedPlayerMatch = {
    ...europa, match: { ...europa.match, id: "sofascore-match-16599928", round: 11, kickoff: "2026-11-08T16:15:00Z",
      home: { id: "team-real-jaen", name: "Real Jaén" }, away: { id: "sofascore-team-2", name: "Rayo Majadahonda" } },
  };
  const siverioProfile: PlayerProfile = {
    player: { id: SIVERIO, name: "Marco Siverio", position: "forward", birthDate: null, heightCm: null, preferredFoot: null, country: null },
    scope: "", source: { synthetic: false, label: "Datos reales" },
  };

  it("drops a pending location, its player and its selection when another match is opened", async () => {
    let resolveLocation!: (value: VideoEventLocation) => void;
    vi.mocked(locateMatchVideoEvent).mockReturnValue(new Promise((done) => { resolveLocation = done; }));
    vi.mocked(getMatchVideo).mockResolvedValue(VIDEO);
    vi.mocked(getAllEnrichedComments).mockImplementation(async (eventId) => eventId === "16599363"
      ? page([siverioGoal]) : page([goal(32, 90, [participant("player", "scorer", 942663, "Marco Siverio Toro")])]));
    vi.mocked(getPlayerProfile).mockResolvedValue(siverioProfile);
    vi.mocked(getPlayerObservedMatches).mockResolvedValue({ playerId: SIVERIO, matches: [europa, rayo], scope: "",
      ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" } });
    render(<PlayerProfileView playerId={SIVERIO} returnHref="/teams" />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Reproducir gol del minuto 63" }));
    const signal = vi.mocked(locateMatchVideoEvent).mock.calls[0][5]!.signal!;
    await user.click(screen.getByRole("button", { name: /^Seleccionar J11/ }));
    expect(signal.aborted).toBe(true);
    expect(await screen.findByRole("button", { name: "Reproducir gol del minuto 32" })).toHaveAttribute("aria-pressed", "false");
    resolveLocation({ status: "resolved", videoSecond: 2600, eventVideoSecond: 2600, eventClockSecond: 3720,
      eventClockSecondEstimate: 3760, leadSeconds: 0, estimated: true, extrapolatedSeconds: 0, method: "minute_tail",
      confidence: { ocr: "high", period: "high", location: "high" }, warnings: [] });
    await new Promise((done) => setTimeout(done, 0));
    expect(screen.queryByTestId("video-player")).toBeNull();
    expect(screen.queryByText("Según la narración, asistencia de Antonio Caballero")).toBeNull();
    expect(youtube.mounts).toBe(0);
  });

  it("does not keep the previous match's player", async () => {
    vi.mocked(locateMatchVideoEvent).mockResolvedValue({ status: "resolved", videoSecond: 2600, eventVideoSecond: 2600,
      eventClockSecond: 3720, eventClockSecondEstimate: 3760, leadSeconds: 0, estimated: true, extrapolatedSeconds: 0,
      method: "minute_tail", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [] });
    vi.mocked(getMatchVideo).mockResolvedValue(VIDEO);
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([siverioGoal]));
    vi.mocked(getPlayerProfile).mockResolvedValue(siverioProfile);
    vi.mocked(getPlayerObservedMatches).mockResolvedValue({ playerId: SIVERIO, matches: [europa, rayo], scope: "",
      ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" } });
    render(<PlayerProfileView playerId={SIVERIO} returnHref="/teams" />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Reproducir gol del minuto 63" }));
    expect(await screen.findByTestId("video-player")).toBeVisible();
    await user.click(screen.getByRole("button", { name: /^Seleccionar J11/ }));
    await screen.findByRole("button", { name: "Reproducir gol del minuto 63" });
    expect(screen.queryByTestId("video-player")).toBeNull();
    // The new match locates again: its cache is its own.
    await user.click(screen.getByRole("button", { name: "Reproducir gol del minuto 63" }));
    expect(await screen.findByTestId("video-player")).toBeVisible();
    expect(locateMatchVideoEvent).toHaveBeenLastCalledWith("sofascore-match-16599928", "second", "63", "other", undefined, expect.anything());
  });
});
