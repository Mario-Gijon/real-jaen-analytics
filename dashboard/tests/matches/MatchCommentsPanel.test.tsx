import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { deleteMatchVideoReference, getAllEnrichedComments, getCurrentMatchVideoJob, getMatchVideo, getMatchVideoJob, locateMatchVideoEvent, startMatchVideoJob } from "@/client/api";
import type { EnrichedComment, EnrichedCommentsPage, MatchVideoReference, PlayerRef, VideoJob } from "@/contract";
import { MatchCommentsPanel } from "@/features/matches/MatchCommentsPanel";

const youtubePlayerMock = vi.hoisted(() => ({
  playbackCallback: null as null | ((snapshot: {
    currentTime: number; duration: number; ready: boolean; playing: boolean; error: boolean;
    provisionalTime?: number | null; scrubbing?: boolean;
  }) => void),
}));

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getAllEnrichedComments: vi.fn(),
  getCurrentMatchVideoJob: vi.fn(),
  getMatchVideo: vi.fn(),
  getMatchVideoJob: vi.fn(),
  locateMatchVideoEvent: vi.fn(),
  startMatchVideoJob: vi.fn(),
  deleteMatchVideoReference: vi.fn(),
}));

vi.mock("@/features/matches/YouTubePlayer", () => ({
  YouTubePlayer: ({ seekRequest, onPlaybackUpdate }: {
    seekRequest: { sequence: number; seconds: number } | null;
    onPlaybackUpdate?: typeof youtubePlayerMock.playbackCallback;
  }) => {
    youtubePlayerMock.playbackCallback = onPlaybackUpdate ?? null;
    return <div data-testid="video-player" data-seek-to={seekRequest?.seconds ?? ""} />;
  },
}));

const resolvedLocation = (videoSecond: number) => ({ status: "resolved" as const, videoSecond,
  eventVideoSecond: videoSecond, eventClockSecond: 3420, eventClockSecondEstimate: 3460,
  leadSeconds: 0, estimated: true, method: "minute_tail" as const,
  confidence: { ocr: "high", period: "high", location: "high" }, warnings: ["Segundo 40 seleccionado"] });

const participant = (id: string, name: string, role: string) => ({
  sourceRole: "player", role, normalizerRole: role, sourceId: Number(id.split("-").at(-1)), name,
  player: { id, name, position: null },
});

function event(overrides: Partial<EnrichedComment> & Pick<EnrichedComment, "id" | "position" | "text" | "category">): EnrichedComment {
  return {
    captureId: 31, originalType: "scoreChange", minute: 24, periodName: "1ST", isHome: true,
    score: { home: 1, away: 0 }, attributes: {}, participants: [], ...overrides,
  };
}

function page(items: EnrichedComment[]): EnrichedCommentsPage {
  return { enrichmentVersion: "rules-v1", total: items.length, limit: items.length || 1, offset: 0, items };
}

beforeEach(() => {
  vi.resetAllMocks();
  youtubePlayerMock.playbackCallback = null;
  vi.mocked(getMatchVideo).mockResolvedValue({ available: false });
  vi.mocked(getCurrentMatchVideoJob).mockResolvedValue(null);
  vi.mocked(locateMatchVideoEvent).mockResolvedValue({ status: "uncertain" });
});

describe("MatchCommentsPanel unified events", () => {
  it("shows every event in one chronological list without pages or technical source codes", async () => {
    const items = Array.from({ length: 121 }, (_, index) => event({
      id: `event-${index}`, position: index, text: `Narración ${index}`, category: "match_flow",
      originalType: index === 0 ? "matchEnded" : "matchStarted", minute: index,
      periodName: index % 2 ? "2ND" : "1ST", isHome: null, score: null,
    }));
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    render(<MatchCommentsPanel matchId="sofascore-match-16600011" homeName="Real Jaén" awayName="Algeciras CF" />);

    const list = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    const rows = Array.from(list.children) as HTMLLIElement[];
    expect(rows).toHaveLength(121);
    expect(rows[0]).toHaveTextContent("Narración 120");
    expect(rows.at(-1)).toHaveTextContent("Final del partido");
    expect(screen.getByText("120′")).toBeVisible();
    expect(screen.queryByText("Primera parte", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("Segunda parte", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /más antiguos|más recientes/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/mostrando \d+ de \d+/i)).not.toBeInTheDocument();
    expect(screen.queryByText("rules-v1")).not.toBeInTheDocument();
    expect(screen.queryByText("matchEnded")).not.toBeInTheDocument();
    expect(screen.getByText("Final del partido")).toBeVisible();
    expect(screen.getByLabelText("Tipo de evento")).toBeVisible();
    expect(screen.getByLabelText("Jugador")).toBeVisible();
    expect(screen.getByLabelText("Equipo")).toBeVisible();
  });

  it("keeps separate periods internally when two events share the same displayed minute", async () => {
    const items = [
      event({ id: "first-half", position: 1, minute: 45, periodName: "1ST", text: "Gol de Primera.", category: "goal" }),
      event({ id: "second-half", position: 2, minute: 45, periodName: "2ND", text: "Gol de Segunda.", category: "goal" }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    render(<MatchCommentsPanel matchId="sofascore-match-16600011" />);
    const list = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    const rows = Array.from(list.children) as HTMLLIElement[];
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Gol de Segunda.");
    expect(rows[1]).toHaveTextContent("Gol de Primera.");
    expect(screen.getAllByText("45′")).toHaveLength(2);
    expect(screen.queryByText("Primera parte", { exact: true })).not.toBeInTheDocument();
    expect(screen.queryByText("Segunda parte", { exact: true })).not.toBeInTheDocument();
  });

  it("shows the associated player and seeks only after a resolved Video Service response", async () => {
    const item = event({ id: "second-half-event", position: 1, minute: 58, periodName: "2ND",
      text: "Remate de Javi Moyano.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolvedLocation(6643));

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    expect(await screen.findByTestId("video-player")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Eventos en orden cronológico" })).toHaveClass(/timelineVideo/);
    expect(screen.queryByRole("list", { name: "Atributos detectados" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /añadir vídeo/i })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: /Saltar al minuto 58 de la segunda parte/i }));

    expect(locateMatchVideoEvent).toHaveBeenCalledWith("sofascore-match-16599928", "second", "58", "other");
    await waitFor(() => expect(screen.getByTestId("video-player")).toHaveAttribute("data-seek-to", "6643"));
    expect(screen.getByText("Remate de Javi Moyano.").closest("li")).toHaveClass(/selected/);
    expect(screen.queryByText(/posición calculada mediante OCR|localización estimada|instante aproximado/i)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /reproducción desde el reloj 57:40/i })).toBeInTheDocument();
  });

  it("preloads positions once and follows the active resolved event without per-tick requests", async () => {
    const items = [
      event({ id: "same-minute-first", position: 3, minute: 7, periodName: "1ST", text: "Primer evento del minuto.", category: "foul" }),
      event({ id: "earlier", position: 2, minute: 7, periodName: "1ST", text: "Evento anterior.", category: "shot" }),
      event({ id: "later", position: 1, minute: 8, periodName: "1ST", text: "Evento siguiente.", category: "foul" }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockImplementation(async (_matchId, _period, minute) =>
      resolvedLocation(minute === "7" ? 100 : 200));

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    expect(await screen.findByTestId("video-player")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByTitle("Reproducción desde 06:40 del reloj futbolístico")).toHaveLength(2));
    await waitFor(() => expect(screen.getByTitle("Reproducción desde 07:40 del reloj futbolístico")).toBeInTheDocument());
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(3);

    const earlierCard = screen.getByText("Evento anterior.").closest("li");
    const stableMinuteCard = screen.getByText("Primer evento del minuto.").closest("li");
    const laterCard = screen.getByText("Evento siguiente.").closest("li");
    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 150, duration: 500, ready: true, playing: true, error: false }));
    expect(stableMinuteCard).toHaveAttribute("aria-current", "true");
    expect(stableMinuteCard).toHaveClass(/selected/);
    expect(earlierCard).not.toHaveAttribute("aria-current", "true");
    expect(earlierCard).not.toHaveClass(/selected/);
    expect(laterCard).not.toHaveAttribute("aria-current", "true");
    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 205, duration: 500, ready: true, playing: true, error: false }));
    expect(laterCard).toHaveAttribute("aria-current", "true");
    expect(laterCard).toHaveClass(/selected/);
    expect(stableMinuteCard).not.toHaveClass(/selected/);
    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 230, duration: 500, ready: true, playing: true, error: false }));
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(3);
  });

  it("scrolls only when the active event changes and resumes after manual carousel exploration", async () => {
    const items = [
      event({ id: "before", position: 2, minute: 7, periodName: "1ST", text: "Evento del minuto 7.", category: "shot" }),
      event({ id: "after", position: 1, minute: 8, periodName: "1ST", text: "Evento del minuto 8.", category: "shot" }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockImplementation(async (_matchId, _period, minute) =>
      resolvedLocation(minute === "7" ? 100 : 200));

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    await waitFor(() => expect(screen.getByTitle("Reproducción desde 07:40 del reloj futbolístico")).toBeInTheDocument());

    const list = screen.getByRole("list", { name: "Eventos en orden cronológico" });
    const beforeCard = screen.getByText("Evento del minuto 7.").closest("li")!;
    const afterCard = screen.getByText("Evento del minuto 8.").closest("li")!;
    const rect = (left: number, right: number) => ({
      x: left, y: 0, left, right, top: 0, bottom: 100, width: right - left, height: 100,
      toJSON: () => ({}),
    }) as DOMRect;
    vi.spyOn(list, "getBoundingClientRect").mockReturnValue(rect(0, 300));
    const beforeBounds = vi.spyOn(beforeCard, "getBoundingClientRect").mockReturnValue(rect(10, 110));
    vi.spyOn(afterCard, "getBoundingClientRect").mockReturnValue(rect(320, 420));
    const scrollBy = vi.fn();
    Object.defineProperty(list, "scrollBy", { configurable: true, value: scrollBy });

    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 150, duration: 500, ready: true, playing: true, error: false }));
    expect(scrollBy).not.toHaveBeenCalled();

    vi.useFakeTimers();
    try {
      fireEvent.wheel(list);
      act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 205, duration: 500, ready: true, playing: true, error: false }));
      expect(afterCard).toHaveAttribute("aria-current", "true");
      expect(afterCard).toHaveClass(/selected/);
      expect(scrollBy).not.toHaveBeenCalled();

      act(() => vi.advanceTimersByTime(2800));
      expect(scrollBy).toHaveBeenCalledTimes(1);
      expect(scrollBy).toHaveBeenLastCalledWith({ left: 120, behavior: "smooth" });

      act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 230, duration: 500, ready: true, playing: true, error: false }));
      expect(scrollBy).toHaveBeenCalledTimes(1);

      beforeBounds.mockReturnValue(rect(-150, -50));
      act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 150, duration: 500, ready: true, playing: true, error: false }));
      expect(beforeCard).toHaveAttribute("aria-current", "true");
      expect(beforeCard).toHaveClass(/selected/);
      expect(afterCard).not.toHaveClass(/selected/);
      expect(scrollBy).toHaveBeenLastCalledWith({ left: -150, behavior: "smooth" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("uses provisional scrub time immediately and overrides the carousel pause without stale-time flicker", async () => {
    const items = [
      event({ id: "scrub-before", position: 2, minute: 7, periodName: "1ST", text: "Evento del minuto 7.", category: "shot" }),
      event({ id: "scrub-after", position: 1, minute: 8, periodName: "1ST", text: "Evento del minuto 8.", category: "shot" }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockImplementation(async (_matchId, _period, minute) =>
      resolvedLocation(minute === "7" ? 100 : 200));

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    await waitFor(() => expect(screen.getByTitle("Reproducción desde 07:40 del reloj futbolístico")).toBeInTheDocument());

    const list = screen.getByRole("list", { name: "Eventos en orden cronológico" });
    const beforeCard = screen.getByText("Evento del minuto 7.").closest("li")!;
    const afterCard = screen.getByText("Evento del minuto 8.").closest("li")!;
    const rect = (left: number, right: number) => ({
      x: left, y: 0, left, right, top: 0, bottom: 100, width: right - left, height: 100,
      toJSON: () => ({}),
    }) as DOMRect;
    vi.spyOn(list, "getBoundingClientRect").mockReturnValue(rect(0, 300));
    vi.spyOn(beforeCard, "getBoundingClientRect").mockReturnValue(rect(10, 110));
    vi.spyOn(afterCard, "getBoundingClientRect").mockReturnValue(rect(320, 420));
    const scrollBy = vi.fn();
    Object.defineProperty(list, "scrollBy", { configurable: true, value: scrollBy });

    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 100, duration: 500, ready: true,
      playing: false, error: false }));
    fireEvent.wheel(list);
    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 100, provisionalTime: 205, scrubbing: true,
      duration: 500, ready: true, playing: false, error: false }));
    expect(afterCard).toHaveAttribute("aria-current", "true");
    expect(afterCard).toHaveClass(/selected/);
    expect(beforeCard).not.toHaveClass(/selected/);
    expect(scrollBy).toHaveBeenCalledTimes(1);
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(2);

    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 100, provisionalTime: 205, scrubbing: false,
      duration: 500, ready: true, playing: false, error: false }));
    expect(afterCard).toHaveAttribute("aria-current", "true");
    act(() => youtubePlayerMock.playbackCallback?.({ currentTime: 205, provisionalTime: null, scrubbing: false,
      duration: 500, ready: true, playing: false, error: false }));
    expect(afterCard).toHaveAttribute("aria-current", "true");
    expect(scrollBy).toHaveBeenCalledTimes(1);
    expect(locateMatchVideoEvent).toHaveBeenCalledTimes(2);
  });

  it("lets long video comments expand without losing player links", async () => {
    const player = participant("sofascore-player-597500", "Mario Martos", "scorer");
    const item = event({ id: "long-comment", position: 1, text: `Mario Martos ${"jugada narrada con detalle ".repeat(12)}`,
      category: "shot", participants: [player] });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolvedLocation(100));
    render(<MatchCommentsPanel matchId="sofascore-match-16599928"
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16599928" }} />);

    const expand = await screen.findByRole("button", { name: "Ver comentario completo" });
    const text = expand.previousElementSibling;
    expect(text).toHaveClass("textClamped");
    fireEvent.click(expand);
    expect(screen.getByRole("button", { name: "Mostrar menos" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Mario Martos" })).toBeInTheDocument();
  });

  it("converts first-half stoppage minutes to the V5.1 45+N convention", async () => {
    const item = event({ id: "first-added-time", position: 1, minute: 47, periodName: "1ST",
      text: "Remate durante la reposición.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockResolvedValue(resolvedLocation(4800));

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    fireEvent.click(await screen.findByRole("button", { name: /Saltar al minuto 47 de la primera parte/i }));
    expect(locateMatchVideoEvent).toHaveBeenCalledWith("sofascore-match-16599928", "first", "45+2", "other");
    expect(screen.getByText("47′")).toBeVisible();
  });

  it.each([
    [7, "1ST", "06:40"], [8, "1ST", "07:40"], [32, "1ST", "31:40"],
    [60, "2ND", "59:40"], [94, "2ND", "93:40"],
  ])("shows minute %s as playback clock %s", async (minute, periodName, clock) => {
    const item = event({ id: `minute-${minute}`, position: minute, minute, periodName,
      text: "Evento ordinario.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    expect(await screen.findByRole("button", { name: new RegExp(`reproducción desde el reloj ${clock}`, "i") }))
      .toBeInTheDocument();
  });

  it("routes first-period start events to the explicit boundary estimate", async () => {
    const item = event({ id: "start-first", position: 1, originalType: "matchStarted", minute: 1,
      periodName: "1ST", category: "match_flow", text: "Empieza primera parte." });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "mo5lqtNeS2w", synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockResolvedValue({ ...resolvedLocation(2032.032), method: "boundary_extrapolation" });
    render(<MatchCommentsPanel matchId="sofascore-match-16599973" />);
    fireEvent.click(await screen.findByRole("button", { name: /Localizar el inicio de la primera parte/i }));
    expect(locateMatchVideoEvent).toHaveBeenCalledWith("sofascore-match-16599973", "first", "1", "match_start");
    await waitFor(() => expect(screen.getByTestId("video-player")).toHaveAttribute("data-seek-to", "2032.032"));
    expect(screen.queryByText(/inicio de parte estimado desde referencias OCR/i)).not.toBeInTheDocument();
  });

  it("does not seek after an ambiguous location and explains why the event cannot be replayed", async () => {
    const item = event({ id: "ambiguous-event", position: 1, minute: 58, periodName: "2ND",
      text: "Acción con dos segmentos posibles.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockResolvedValue({ status: "ambiguous" });

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    const action = await screen.findByRole("button", { name: /Saltar al minuto 58/i });
    fireEvent.click(action);
    expect(await screen.findByRole("button", { name: /Vídeo no localizable: Hay más de una posición posible/i })).toBeDisabled();
    expect(screen.queryByText("Hay más de una posición posible.")).not.toBeInTheDocument();
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "");
  });

  it.each([
    ["uncertain", /no se ha identificado con seguridad/i],
    ["out_of_coverage", /cobertura OCR verificada/i],
  ] as const)("does not seek for a %s location", async (status, explanation) => {
    const item = event({ id: `not-resolved-${status}`, position: 1, minute: 58, periodName: "2ND",
      text: "Evento sin posición confirmada.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });
    vi.mocked(locateMatchVideoEvent).mockResolvedValue({ status });

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    fireEvent.click(await screen.findByRole("button", { name: /Saltar al minuto 58/i }));
    expect(await screen.findByRole("button", { name: explanation })).toBeDisabled();
    expect(await screen.findByTestId("video-player")).toHaveAttribute("data-seek-to", "");
  });

  it("shows a disabled icon for comments without a minute or a recognized period", async () => {
    const items = [
      event({ id: "no-minute", position: 1, minute: null, periodName: "2ND", text: "Final de la parte.", category: "match_flow" }),
      event({ id: "unknown-period", position: 2, minute: 50, periodName: null, text: "Periodo desconocido.", category: "shot" }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" });

    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
    expect(await screen.findByRole("button", { name: /no tiene minuto/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /no se reconoce el periodo/i })).toBeDisabled();
    expect(locateMatchVideoEvent).not.toHaveBeenCalled();
  });

  it("restores the vertical event list and compact pending button when no video is associated", async () => {
    const scorer = participant("sofascore-player-597500", "Delantero", "scorer");
    const item = event({ id: "classic-goal", position: 1, minute: 32, category: "goal",
      text: "¡Gol de Delantero!", participants: [scorer], attributes: { goal_status: "valid" } });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValue({ available: false });
    render(<MatchCommentsPanel matchId="sofascore-match-16599928"
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16599928" }} />);
    const list = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    const row = list.querySelector("li")!;
    expect(row).toHaveTextContent("¡Gol de Delantero!");
    expect(list).not.toHaveClass(/timelineVideo/);
    expect(row).toHaveTextContent("32′");
    expect(within(row!).getByText("Goles")).toBeVisible();
    expect(within(row!).getByRole("link", { name: "Delantero" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Eventos anteriores" })).not.toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "Atributos detectados" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /añadir vídeo/i })).toBeEnabled();
    expect(screen.queryByText("Incorporación de vídeos pendiente")).not.toBeInTheDocument();
    expect(screen.queryByTestId("video-player")).not.toBeInTheDocument();
  });

  it("opens the YouTube form, validates the URL and sends only the URL for the current match", async () => {
    const job: VideoJob = {
      jobId: "11111111-1111-4111-8111-111111111111",
      match: { provider: "sofascore", eventId: "16599928" },
      youtubeVideoId: "79Rngk2hcC0",
      status: "pending",
      message: "Trabajo pendiente.",
      errorCode: null,
      createdAt: "2026-11-11T12:00:00Z",
      updatedAt: "2026-11-11T12:00:00Z",
    };
    vi.mocked(startMatchVideoJob).mockResolvedValue(job);
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([]));
    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);

    fireEvent.click(await screen.findByRole("button", { name: "Añadir vídeo" }));
    const dialog = screen.getByRole("dialog", { name: "Añadir vídeo de YouTube" });
    const field = within(dialog).getByLabelText("URL de YouTube");
    fireEvent.change(field, { target: { value: "https://youtube.com.evil.invalid/watch?v=79Rngk2hcC0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Añadir y sincronizar" }));
    expect(await within(dialog).findByRole("alert")).toHaveTextContent("enlace HTTPS válido de YouTube");
    expect(startMatchVideoJob).not.toHaveBeenCalled();

    fireEvent.change(field, { target: { value: "https://www.youtube.com/watch?v=79Rngk2hcC0" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Añadir y sincronizar" }));
    await waitFor(() => expect(startMatchVideoJob).toHaveBeenCalledWith(
      "sofascore-match-16599928", "https://www.youtube.com/watch?v=79Rngk2hcC0",
    ));
    expect(await screen.findByText("Pendiente.")).toBeInTheDocument();
  });

  it("polls the submitted job by ID and refreshes the video reference on completion", async () => {
    const pending: VideoJob = {
      jobId: "11111111-1111-4111-8111-111111111111",
      match: { provider: "sofascore", eventId: "16599928" },
      youtubeVideoId: "79Rngk2hcC0",
      status: "pending",
      message: "Trabajo pendiente.",
      errorCode: null,
      createdAt: "2026-11-11T12:00:00Z",
      updatedAt: "2026-11-11T12:00:00Z",
    };
    const completed = { ...pending, status: "completed" as const, message: "Procesamiento completado." };
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([]));
    vi.mocked(getCurrentMatchVideoJob).mockResolvedValue(null);
    vi.mocked(startMatchVideoJob).mockResolvedValue(pending);
    vi.mocked(getMatchVideoJob).mockResolvedValue(completed);
    vi.mocked(getMatchVideo).mockResolvedValueOnce({ available: false }).mockResolvedValue({ available: true,
      youtubeVideoId: "79Rngk2hcC0", synchronizationStatus: "calibrated" });

    let poll: (() => void) | null = null;
    const interval = vi.spyOn(window, "setInterval").mockImplementation(((handler: TimerHandler) => {
      poll = handler as () => void;
      return 17;
    }) as typeof window.setInterval);
    const clearInterval = vi.spyOn(window, "clearInterval").mockImplementation(() => {});
    try {
      render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);
      const addButton = await screen.findByRole("button", { name: "Añadir vídeo" });
      await waitFor(() => expect(addButton).toBeEnabled());
      fireEvent.click(addButton);
      const dialog = screen.getByRole("dialog", { name: "Añadir vídeo de YouTube" });
      fireEvent.change(within(dialog).getByLabelText("URL de YouTube"), {
        target: { value: "https://www.youtube.com/watch?v=79Rngk2hcC0" },
      });
      fireEvent.click(within(dialog).getByRole("button", { name: "Añadir y sincronizar" }));
      expect(await screen.findByText("Pendiente.")).toBeInTheDocument();
      expect(poll).toBeTypeOf("function");

      await act(async () => {
        poll?.();
        await Promise.resolve();
      });

      await waitFor(() => expect(getMatchVideoJob).toHaveBeenCalledWith(
        "sofascore-match-16599928", "11111111-1111-4111-8111-111111111111",
      ));
      expect(await screen.findByRole("button", { name: "Eliminar vídeo de referencia" })).toBeVisible();
    } finally {
      interval.mockRestore();
      clearInterval.mockRestore();
    }
  });

  it("requires confirmation for persistent removal and restores the classic event list after success", async () => {
    const item = event({ id: "one", position: 1, text: "Evento narrado.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    vi.mocked(getMatchVideo).mockResolvedValueOnce({ available: true, youtubeVideoId: "79Rngk2hcC0",
      synchronizationStatus: "calibrated" }).mockResolvedValue({ available: false });
    vi.mocked(deleteMatchVideoReference).mockResolvedValue({ available: false });
    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);

    fireEvent.click(await screen.findByRole("button", { name: "Eliminar vídeo de referencia" }));
    const dialog = screen.getByRole("dialog", { name: "Eliminar vídeo de referencia" });
    expect(within(dialog).getByText(/El vídeo original de YouTube y el informe OCR histórico se conservarán/)).toBeVisible();
    expect(deleteMatchVideoReference).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Eliminar referencia" }));

    await waitFor(() => expect(deleteMatchVideoReference).toHaveBeenCalledWith("sofascore-match-16599928"));
    expect(await screen.findByRole("button", { name: "Añadir vídeo" })).toBeEnabled();
    expect(screen.queryByTestId("video-player")).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Eventos en orden cronológico" })).not.toHaveClass(/timelineVideo/);
  });

  it("keeps the classic layout during lookup and reports service errors without implying no video", async () => {
    let rejectVideo!: (reason: unknown) => void;
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([event({ id: "loading-event", position: 1,
      text: "Evento durante consulta.", category: "shot" })]));
    vi.mocked(getMatchVideo).mockImplementation(() => new Promise<MatchVideoReference>((_resolve, reject) => {
      rejectVideo = reject;
    }));
    render(<MatchCommentsPanel matchId="sofascore-match-16599928" />);

    const list = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    expect(await screen.findByRole("status")).toHaveTextContent("Comprobando vídeo asociado");
    expect(list).not.toHaveClass(/timelineVideo/);
    expect(screen.queryByRole("button", { name: /añadir vídeo/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("video-player")).not.toBeInTheDocument();
    await waitFor(() => expect(getMatchVideo).toHaveBeenCalled());

    rejectVideo(new Error("servicio no disponible"));
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudo consultar el vídeo asociado");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeVisible();
    expect(list).not.toHaveClass(/timelineVideo/);
    expect(screen.queryByRole("button", { name: /añadir vídeo/i })).not.toBeInTheDocument();
  });

  it("combines event, structured player, and event-side team filters and links matched names in prose", async () => {
    const scorer = participant("sofascore-player-597500", "Delantero", "scorer");
    const assistant = participant("sofascore-player-597501", "Asistente", "assistant");
    const items = [
      event({ id: "goal-home", position: 4, text: "¡Gol de Delantero! Asistencia de Asistente.", category: "goal",
        attributes: { goal_status: "valid", surface: "right_foot", goal_kind: "ordinary", contexts: ["after_corner"] },
        participants: [scorer, assistant] }),
      event({ id: "shot-away", position: 3, text: "Remate de Delantero.", category: "shot", isHome: false,
        participants: [scorer], score: { home: 1, away: 0 } }),
      event({ id: "goal-away", position: 2, text: "Gol de Delantero para el visitante.", category: "goal", isHome: false,
        participants: [scorer], score: { home: 1, away: 1 } }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    render(<MatchCommentsPanel matchId="sofascore-match-16600011" homeName="Real Jaén" awayName="Algeciras CF"
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16600011" }} />);

    fireEvent.change(await screen.findByLabelText("Tipo de evento"), { target: { value: "goal" } });
    fireEvent.change(screen.getByLabelText("Jugador"), { target: { value: "sofascore-player-597500" } });
    fireEvent.change(screen.getByLabelText("Equipo"), { target: { value: "home" } });

    const list = screen.getByRole("list", { name: "Eventos en orden cronológico" });
    const rows = Array.from(list.children) as HTMLLIElement[];
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("¡Gol de Delantero! Asistencia de Asistente.");
    expect(within(rows[0]).getByRole("link", { name: "Delantero" })).toHaveAttribute(
      "href", "/players/sofascore-player-597500?view=profile&from=observedMatch&origin=sofascore-match-16600011",
    );
    expect(within(rows[0]).getByRole("link", { name: "Asistente" })).toBeVisible();
    expect(within(rows[0]).getByText("Goles")).toBeVisible();
    expect(within(rows[0]).queryByRole("list", { name: "Atributos detectados" })).not.toBeInTheDocument();
    expect(rows[0]).toHaveTextContent("Marcador 1–0");
    expect(screen.queryByText("scoreChange")).not.toBeInTheDocument();
    expect(screen.queryByText("scorer")).not.toBeInTheDocument();
    expect(screen.queryByText("assistant")).not.toBeInTheDocument();
  });

  it("leaves ambiguous names without IDs as plain text and hides the attribute chips", async () => {
    const item = event({ id: "ambiguous", position: 1, category: "foul", text: "Falta de Juan Pérez sobre Juan.",
      attributes: { infraction_type: "foul", unknown_internal_field: "internal_code" },
      participants: [
        participant("sofascore-player-1", "Juan", "foul_committer"),
        participant("sofascore-player-2", "Juan", "foul_recipient"),
        { sourceRole: "player", role: null, normalizerRole: null, sourceId: null, name: "Juan Pérez", player: null },
      ] });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600011" />);
    const row = await screen.findByText("Falta de Juan Pérez sobre Juan.");
    expect(row.querySelectorAll("a")).toHaveLength(0);
    expect(row).toHaveTextContent("Falta de Juan Pérez sobre Juan.");
    expect(screen.queryByRole("list", { name: "Atributos detectados" })).not.toBeInTheDocument();
    expect(screen.queryByText("unknown_internal_field")).not.toBeInTheDocument();
    expect(screen.queryByText("internal_code")).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Juan (ID 1)" })).toBeVisible();
  });

  it("links SofaScore's shortened Cheikh Thiam mention and filters all his structured events", async () => {
    const cheikh = participant("sofascore-player-2137657", "Cheikh Tidiane Thiam", "foul_committer");
    const roster: PlayerRef[] = [{ id: "sofascore-player-2137657", name: "Cheikh Tidiane Thiam", position: null }];
    const items = [
      event({ id: "handball-88", position: 3, minute: 88, originalType: "freeKickLost", category: "foul",
        text: "Mano de Cheikh Thiam (Villarreal II).", participants: [cheikh] }),
      event({ id: "shot-78", position: 2, minute: 78, originalType: "shotOffTarget", category: "shot",
        text: "Disparo que sale desviado de Cheikh Thiam (Villarreal II).", participants: [cheikh] }),
      event({ id: "shot-50", position: 1, minute: 50, originalType: "shotBlocked", category: "shot",
        text: "Remate rechazado de Cheikh Thiam (Villarreal II).", participants: [cheikh] }),
    ];
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page(items));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={roster}
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16600019" }} />);

    const links = await screen.findAllByRole("link", { name: "Cheikh Thiam" });
    expect(links).toHaveLength(3);
    expect(links[0]).toHaveAttribute("href", "/players/sofascore-player-2137657?view=profile&from=observedMatch&origin=sofascore-match-16600019");
    fireEvent.change(screen.getByLabelText("Jugador"), { target: { value: "sofascore-player-2137657" } });
    expect(screen.getByRole("list", { name: "Eventos en orden cronológico" }).children).toHaveLength(3);
    expect(screen.getByRole("option", { name: "Cheikh Tidiane Thiam" })).toBeVisible();
  });

  it("resolves the explicit Edu alias to Eduardo Espiau and includes its event in the player filter", async () => {
    const item = event({ id: "edu-goal", position: 1, category: "goal", text: "¡Gol de Edu Espiau!",
      participants: [{ sourceRole: "player", role: "scorer", normalizerRole: "player", sourceId: null,
        name: "Edu Espiau", player: null }] });
    const eduardo: PlayerRef = { id: "sofascore-player-184816", name: "Eduardo Espiau", position: null };
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[eduardo]}
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16600019" }} />);

    const link = await screen.findByRole("link", { name: "Edu Espiau" });
    expect(link).toHaveAttribute("href",
      "/players/sofascore-player-184816?view=profile&from=observedMatch&origin=sofascore-match-16600019");
    expect(screen.getByRole("option", { name: "Eduardo Espiau" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Jugador"), { target: { value: eduardo.id } });
    const rows = screen.getByRole("list", { name: "Eventos en orden cronológico" }).children;
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("¡Gol de Edu Espiau!");
  });

  it("does not equate a different given name such as Fernán with Fernando", async () => {
    const item = event({ id: "fernan-foul", position: 1, category: "foul", text: "Falta de Fernán Pérez." });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[
      { id: "sofascore-player-32001", name: "Fernando Pérez", position: null },
    ]} />);

    const text = await screen.findByText("Falta de Fernán Pérez.");
    expect(text.querySelector("a")).toBeNull();
    expect(screen.queryByRole("option", { name: "Fernando Pérez" })).not.toBeInTheDocument();
  });

  it("leaves an explicit alias unlinked when multiple roster players share it", async () => {
    const item = event({ id: "ambiguous-edu", position: 1, category: "goal", text: "Gol de Edu Espiau." });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[
      { id: "sofascore-player-41001", name: "Eduardo Espiau", position: null },
      { id: "sofascore-player-41002", name: "Eduardo María Espiau", position: null },
    ]} />);

    const text = await screen.findByText("Gol de Edu Espiau.");
    expect(text.querySelector("a")).toBeNull();
    expect(screen.queryByRole("option", { name: "Eduardo Espiau" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Eduardo María Espiau" })).not.toBeInTheDocument();
  });

  it("does not apply a roster alias when it conflicts with another structured event identity", async () => {
    const item = event({ id: "conflicting-edu", position: 1, category: "goal", text: "Gol de Edu Espiau.",
      participants: [participant("sofascore-player-41099", "Eduardo Espiau", "assistant")] });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[
      { id: "sofascore-player-41001", name: "Eduardo Espiau", position: null },
    ]} />);

    const text = await screen.findByText("Gol de Edu Espiau.");
    expect(text.querySelector("a")).toBeNull();
    expect(screen.getByRole("option", { name: "Eduardo Espiau" })).toHaveValue("sofascore-player-41099");
  });

  it("resolves a missing participant ID only from one matching roster player", async () => {
    const item = event({ id: "lineup-resolved", position: 1, text: "Mano de Cheikh Thiam.", category: "foul",
      participants: [{ sourceRole: "player", role: "foul_committer", normalizerRole: "player", sourceId: null,
        name: "Cheikh Thiam", player: null }] });
    const player: PlayerRef = { id: "sofascore-player-2137657", name: "Cheikh Tidiane Thiam", position: null };
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    const view = render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[player]}
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16600019" }} />);
    expect(await screen.findByRole("link", { name: "Cheikh Thiam" })).toHaveAttribute("href",
      "/players/sofascore-player-2137657?view=profile&from=observedMatch&origin=sofascore-match-16600019");
    expect(screen.getByRole("option", { name: "Cheikh Tidiane Thiam" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Jugador"), { target: { value: player.id } });
    expect(screen.getByRole("list", { name: "Eventos en orden cronológico" }).children).toHaveLength(1);
    view.unmount();

    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([{ ...item, participants: [] }]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[player,
      { id: "sofascore-player-999", name: "Cheikh Thiam", position: null }]}
      profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16600019" }} />);
    expect(await screen.findByText("Mano de Cheikh Thiam.")).toBeVisible();
    expect(screen.getByText("Mano de Cheikh Thiam.").querySelector("a")).toBeNull();
    expect(screen.queryByRole("option", { name: "Cheikh Tidiane Thiam" })).not.toBeInTheDocument();
  });

  it("does not match a roster name inside a longer word", async () => {
    const item = event({ id: "not-a-name", position: 1, text: "Remate desviado de Óscar Ureñazo.", category: "shot" });
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page([item]));
    render(<MatchCommentsPanel matchId="sofascore-match-16600019" rosterPlayers={[
      { id: "sofascore-player-1134431", name: "Oscar Ureña", position: null },
    ]} profileOrigin={{ kind: "observedMatch", id: "sofascore-match-16600019" }} />);
    const text = await screen.findByText("Remate desviado de Óscar Ureñazo.");
    expect(text.querySelector("a")).toBeNull();
    expect(screen.queryByRole("option", { name: "Oscar Ureña" })).not.toBeInTheDocument();
  });

  it("does not request a real event for local or synthetic match IDs", () => {
    render(<MatchCommentsPanel matchId="match-demo-1" />);
    expect(screen.getByText("La cronología de eventos no está disponible para este partido.")).toBeVisible();
    expect(getAllEnrichedComments).not.toHaveBeenCalled();
  });
});
