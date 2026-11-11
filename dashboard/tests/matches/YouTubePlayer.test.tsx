import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { YouTubePlayer } from "@/features/matches/YouTubePlayer";

type PlayerConfig = {
  events: {
    onReady: () => void;
    onError: (event: { data: number }) => void;
    onStateChange: (event: { data: number }) => void;
  };
};

const player = {
  destroy: vi.fn(),
  playVideo: vi.fn(),
  seekTo: vi.fn(),
  getCurrentTime: vi.fn(() => 30),
  getDuration: vi.fn(() => 120),
  getPlayerState: vi.fn(() => 2),
};

let playerConfig: PlayerConfig | null = null;

beforeEach(() => {
  vi.clearAllMocks();
  playerConfig = null;
  player.getCurrentTime.mockReturnValue(30);
  player.getDuration.mockReturnValue(120);
  player.getPlayerState.mockReturnValue(2);
  const MockPlayer = function (_element: HTMLElement, config: PlayerConfig) {
    playerConfig = config;
    return player;
  };
  Object.defineProperty(window, "YT", {
    configurable: true,
    value: { Player: MockPlayer },
  });
});

describe("YouTubePlayer playback controls", () => {
  it("seeks by 15 seconds and preserves a paused player", async () => {
    render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    act(() => playerConfig?.events.onReady());

    const back = screen.getByRole("button", { name: "Retroceder 15 segundos" });
    const forward = screen.getByRole("button", { name: "Avanzar 15 segundos" });
    expect(back).toBeEnabled();
    expect(forward).toBeEnabled();
    fireEvent.click(back);
    expect(player.seekTo).toHaveBeenLastCalledWith(15, true);
    fireEvent.click(forward);
    expect(player.seekTo).toHaveBeenLastCalledWith(45, true);
    expect(player.playVideo).not.toHaveBeenCalled();
  });

  it("clamps manual seeks at both video boundaries", async () => {
    render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    act(() => playerConfig?.events.onReady());

    player.getCurrentTime.mockReturnValue(5);
    fireEvent.click(screen.getByRole("button", { name: "Retroceder 15 segundos" }));
    expect(player.seekTo).toHaveBeenLastCalledWith(0, true);
    player.getCurrentTime.mockReturnValue(118);
    fireEvent.click(screen.getByRole("button", { name: "Avanzar 15 segundos" }));
    expect(player.seekTo).toHaveBeenLastCalledWith(120, true);
  });

  it("seeks the real timeline only when a pointer drag is confirmed", async () => {
    const onPlaybackUpdate = vi.fn();
    render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} onPlaybackUpdate={onPlaybackUpdate} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    player.getPlayerState.mockReturnValue(1);
    act(() => playerConfig?.events.onReady());
    const timeline = screen.getByRole("slider", { name: "Línea temporal del vídeo" });
    expect(timeline).toHaveAttribute("aria-valuetext", "0:30 de 2:00");

    fireEvent.pointerDown(timeline);
    fireEvent.change(timeline, { target: { value: "60" } });
    expect(timeline).toHaveValue("60");
    expect(timeline).toHaveAttribute("aria-valuetext", "1:00 de 2:00");
    expect(player.seekTo).not.toHaveBeenCalled();
    expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({
      provisionalTime: 60, scrubbing: true, playing: true,
    }));
    fireEvent.pointerUp(timeline);
    expect(player.seekTo).toHaveBeenCalledWith(60, true);
    expect(player.playVideo).not.toHaveBeenCalled();
    expect(timeline).toHaveValue("60");
    expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({
      provisionalTime: 60, scrubbing: false, playing: true,
    }));
  });

  it("keeps the scrub target visible over stale API readings until YouTube confirms it", async () => {
    const onPlaybackUpdate = vi.fn();
    render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} onPlaybackUpdate={onPlaybackUpdate} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    vi.useFakeTimers();
    try {
      act(() => playerConfig?.events.onReady());
      const timeline = screen.getByRole("slider", { name: "Línea temporal del vídeo" });
      fireEvent.pointerDown(timeline);
      fireEvent.change(timeline, { target: { value: "75" } });
      fireEvent.pointerUp(timeline);
      expect(player.seekTo).toHaveBeenLastCalledWith(75, true);
      expect(timeline).toHaveValue("75");

      player.getCurrentTime.mockReturnValue(30);
      await act(async () => { await vi.advanceTimersByTimeAsync(750); });
      expect(timeline).toHaveValue("75");
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({
        currentTime: 30, provisionalTime: 75, scrubbing: false,
      }));

      player.getCurrentTime.mockReturnValue(75);
      await act(async () => { await vi.advanceTimersByTimeAsync(750); });
      expect(timeline).toHaveValue("75");
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({
        currentTime: 75, provisionalTime: null, scrubbing: false,
      }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("continues polling a paused player after a seek from YouTube's native controls", async () => {
    const onPlaybackUpdate = vi.fn();
    render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} onPlaybackUpdate={onPlaybackUpdate} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    vi.useFakeTimers();
    try {
      act(() => playerConfig?.events.onReady());
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ playing: false }));

      player.getCurrentTime.mockReturnValue(92);
      await act(async () => { await vi.advanceTimersByTimeAsync(750); });
      expect(screen.getByRole("slider", { name: "Línea temporal del vídeo" })).toHaveValue("92");
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ currentTime: 92, playing: false }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("publishes YouTube time during playback and refreshes it after a seek", async () => {
    const onPlaybackUpdate = vi.fn();
    const view = render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} onPlaybackUpdate={onPlaybackUpdate} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    vi.useFakeTimers();
    try {
      player.getCurrentTime.mockReturnValue(41);
      act(() => playerConfig?.events.onReady());
      player.getPlayerState.mockReturnValue(1);
      act(() => playerConfig?.events.onStateChange({ data: 1 }));
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ currentTime: 41, playing: true }));

      player.getCurrentTime.mockReturnValue(42);
      await act(async () => { await vi.advanceTimersByTimeAsync(750); });
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ currentTime: 42, playing: true }));

      player.getCurrentTime.mockReturnValue(57);
      view.rerender(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={{ sequence: 1, seconds: 57 }}
        onPlaybackUpdate={onPlaybackUpdate} />);
      expect(player.seekTo).toHaveBeenLastCalledWith(57, true);
      expect(player.playVideo).toHaveBeenCalled();
      await act(async () => { await vi.advanceTimersByTimeAsync(750); });
      expect(onPlaybackUpdate).toHaveBeenLastCalledWith(expect.objectContaining({ currentTime: 57, playing: true }));
    } finally {
      vi.useRealTimers();
    }
  });

  it("disables all timeline controls after a YouTube playback error", async () => {
    render(<YouTubePlayer videoId="79Rngk2hcC0" seekRequest={null} />);
    await waitFor(() => expect(playerConfig).not.toBeNull());
    act(() => playerConfig?.events.onReady());
    act(() => playerConfig?.events.onError({ data: 150 }));

    expect(screen.getByRole("button", { name: "Retroceder 15 segundos" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Avanzar 15 segundos" })).toBeDisabled();
    expect(screen.getByRole("slider", { name: "Línea temporal del vídeo" })).toBeDisabled();
    expect(screen.getByRole("alert")).toHaveTextContent("código 150");
  });
});
