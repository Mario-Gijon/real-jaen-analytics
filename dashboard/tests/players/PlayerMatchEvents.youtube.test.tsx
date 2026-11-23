import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getAllEnrichedComments, getMatchVideo, locateMatchVideoEvent } from "@/client/api";
import type { EnrichedComment, VideoEventLocation } from "@/contract";
import { PlayerMatchEvents } from "@/features/players/PlayerMatchEvents";

// The real, unmodified YouTubePlayer over a stand-in YouTube IFrame API (as in YouTubePlayer's own tests).
vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getAllEnrichedComments: vi.fn(), getMatchVideo: vi.fn(), locateMatchVideoEvent: vi.fn(),
}));

type PlayerConfig = { videoId: string; events: { onReady: () => void; onError: (event: { data: number }) => void } };
const player = {
  destroy: vi.fn(), playVideo: vi.fn(), seekTo: vi.fn(),
  getCurrentTime: vi.fn(() => 0), getDuration: vi.fn(() => 5700), getPlayerState: vi.fn(() => 2),
};
let configs: PlayerConfig[] = [];

const scorer = (minute: number, position: number): EnrichedComment => ({
  id: String(40778700 + minute), captureId: 3, position, text: "¡Gol!", originalType: "scoreChange", minute, periodName: "2ND",
  isHome: false, score: null, category: "goal", attributes: { goal_status: "valid", goal_kind: "ordinary" },
  participants: [{ sourceRole: "player", role: "scorer", normalizerRole: null, sourceId: 942663, name: "Marco Siverio Toro",
    player: { id: "sofascore-player-942663", name: "Marco Siverio Toro", position: null } }],
});
const resolved = (videoSecond: number): VideoEventLocation => ({ status: "resolved", videoSecond, eventVideoSecond: videoSecond,
  eventClockSecond: 3720, eventClockSecondEstimate: 3760, leadSeconds: 0, estimated: true, extrapolatedSeconds: 0,
  method: "minute_tail", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [] });

beforeEach(() => {
  vi.resetAllMocks();
  configs = [];
  player.getCurrentTime.mockReturnValue(0);
  player.getDuration.mockReturnValue(5700);
  player.getPlayerState.mockReturnValue(2);
  Object.defineProperty(window, "YT", { configurable: true, value: { Player: function (_element: HTMLElement, config: PlayerConfig) {
    configs.push(config);
    return player;
  } } });
  vi.mocked(getMatchVideo).mockResolvedValue({ available: true, youtubeVideoId: "h08Q4YlEiAc", synchronizationStatus: "calibrated" });
  vi.mocked(getAllEnrichedComments).mockResolvedValue({ enrichmentVersion: "rules-v1", total: 2, limit: 100, offset: 0,
    items: [scorer(63, 40), scorer(69, 30)] });
  vi.mocked(locateMatchVideoEvent).mockImplementation(async (_match, _period, minute) => resolved(minute === "63" ? 2600 : 2960));
});

async function playFirst() {
  render(<PlayerMatchEvents matchId="sofascore-match-16599363" playerId="sofascore-player-942663" assists={{ value: 0 }} />);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Reproducir gol del minuto 63" }));
  await waitFor(() => expect(configs).toHaveLength(1));
  return user;
}

describe("PlayerMatchEvents with the real YouTubePlayer", () => {
  it("creates one YouTube player and seeks it 45 s before each located event", async () => {
    const user = await playFirst();
    expect(configs[0].videoId).toBe("h08Q4YlEiAc");
    act(() => configs[0].events.onReady());
    expect(player.seekTo).toHaveBeenLastCalledWith(2555, true);
    expect(player.playVideo).toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Reproducir gol del minuto 69" }));
    await waitFor(() => expect(player.seekTo).toHaveBeenLastCalledWith(2915, true));
    expect(configs).toHaveLength(1);
    expect(player.destroy).not.toHaveBeenCalled();
    // Its own ±15 s controls stay usable.
    expect(screen.getByRole("button", { name: "Retroceder 15 segundos" })).toBeEnabled();
  });

  it("shows YouTube's own embed error inside the player, not as a location problem", async () => {
    await playFirst();
    act(() => configs[0].events.onError({ data: 150 }));
    expect(screen.getByRole("alert")).toHaveTextContent("YouTube no permite reproducir este vídeo incrustado (código 150).");
    expect(screen.queryByText(/No se puede reproducir:|No se pudo localizar/)).toBeNull();
  });

  // Known limitation of the shared YouTubePlayer (phase 3A), not of this section: it clamps a
  // seek to the duration it knows, so if YouTube still reported 0 when the player becomes
  // ready, the first requested seek would land on second 0. Real playback has not shown it
  // (validated in the browser), so YouTubePlayer is left unchanged. This test pins the current
  // behavior: it fails if the flow stops reaching the player, or if the clamping changes.
  it("known limitation: with duration 0 on ready, the first seek lands on second 0", async () => {
    player.getDuration.mockReturnValue(0);
    await playFirst();
    expect(locateMatchVideoEvent).toHaveBeenCalledWith("sofascore-match-16599363", "second", "63", "other", undefined, expect.anything());
    expect(player.seekTo).not.toHaveBeenCalled();
    act(() => configs[0].events.onReady());
    expect(player.seekTo).toHaveBeenCalledTimes(1);
    expect(player.seekTo).toHaveBeenLastCalledWith(0, true);
    expect(player.playVideo).toHaveBeenCalledTimes(1);
  });
});
