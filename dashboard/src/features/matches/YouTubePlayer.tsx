"use client";

import { useEffect, useRef, useState } from "react";
import { FastForward, Rewind } from "lucide-react";
import css from "./MatchCommentsPanel.module.css";

type PlayerErrorEvent = { data: number };
type PlayerStateEvent = { data: number };
type PlayerInstance = {
  destroy(): void;
  playVideo(): void;
  seekTo(seconds: number, allowSeekAhead: boolean): void;
  getCurrentTime(): number;
  getDuration(): number;
  getPlayerState(): number;
};
type PlayerConfig = {
  videoId: string;
  host?: string;
  playerVars: { enablejsapi: 1; origin: string; playsinline: 1; rel: 0 };
  events: {
    onReady: () => void;
    onError: (event: PlayerErrorEvent) => void;
    onStateChange: (event: PlayerStateEvent) => void;
  };
};

export type YouTubePlaybackSnapshot = {
  currentTime: number;
  duration: number;
  ready: boolean;
  playing: boolean;
  error: boolean;
  provisionalTime?: number | null;
  scrubbing?: boolean;
};

declare global {
  interface Window {
    YT?: { Player: new (element: HTMLElement, config: PlayerConfig) => PlayerInstance };
    onYouTubeIframeAPIReady?: () => void;
  }
}

let apiPromise: Promise<void> | null = null;

function loadYouTubeApi(): Promise<void> {
  if (window.YT?.Player) return Promise.resolve();
  if (apiPromise) return apiPromise;
  apiPromise = new Promise<void>((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady;
    window.onYouTubeIframeAPIReady = () => {
      previous?.();
      resolve();
    };
    let script = document.querySelector<HTMLScriptElement>('script[src="https://www.youtube.com/iframe_api"]');
    if (!script) {
      script = document.createElement("script");
      script.src = "https://www.youtube.com/iframe_api";
      script.async = true;
      document.head.append(script);
    }
    script.addEventListener("error", () => reject(new Error("No se pudo cargar la API de YouTube.")), { once: true });
  }).catch((error: unknown) => {
    apiPromise = null;
    throw error;
  });
  return apiPromise;
}

function safePlayerNumber(read: () => number): number {
  try {
    const value = read();
    return Number.isFinite(value) && value >= 0 ? value : 0;
  } catch {
    return 0;
  }
}

function formatTime(value: number): string {
  const total = Math.max(0, Math.floor(value));
  const seconds = String(total % 60).padStart(2, "0");
  const minutes = Math.floor(total / 60);
  if (minutes >= 60) {
    return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${seconds}`;
  }
  return `${minutes}:${seconds}`;
}

export function YouTubePlayer({ videoId, seekRequest, onPlaybackUpdate }: {
  videoId: string;
  seekRequest: { sequence: number; seconds: number } | null;
  onPlaybackUpdate?: (snapshot: YouTubePlaybackSnapshot) => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const playerRef = useRef<PlayerInstance | null>(null);
  const pollRef = useRef<number | null>(null);
  const callbackRef = useRef(onPlaybackUpdate);
  const scrubbingRef = useRef(false);
  const pendingRangeRef = useRef<number | null>(null);
  const rangePreviewRef = useRef<number | null>(null);
  const pendingSeekRef = useRef<{ seconds: number; requestedAt: number } | null>(null);
  const [snapshot, setSnapshot] = useState<YouTubePlaybackSnapshot>({
    currentTime: 0, duration: 0, ready: false, playing: false, error: false,
  });
  const [errorCode, setErrorCode] = useState<number | null>(null);
  const [apiError, setApiError] = useState(false);
  const [rangePreview, setRangePreview] = useState<number | null>(null);
  const controlsReady = snapshot.ready && snapshot.duration > 0 && !snapshot.error && !apiError;
  const durationRef = useRef(0);

  useEffect(() => { callbackRef.current = onPlaybackUpdate; }, [onPlaybackUpdate]);
  useEffect(() => { durationRef.current = snapshot.duration; }, [snapshot.duration]);

  function updateRangePreview(value: number | null) {
    rangePreviewRef.current = value;
    setRangePreview(value);
  }

  useEffect(() => {
    const host = hostRef.current;
    let mounted = true;
    const stopPoll = () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
      pollRef.current = null;
    };
    const publish = (playing?: boolean, error = errorCode !== null || apiError) => {
      const player = playerRef.current;
      const currentTime = player ? safePlayerNumber(() => player.getCurrentTime()) : 0;
      const pendingSeek = pendingSeekRef.current;
      let provisionalTime: number | null = null;
      if (scrubbingRef.current) {
        provisionalTime = pendingRangeRef.current ?? rangePreviewRef.current ?? currentTime;
      } else if (pendingSeek) {
        const confirmed = Math.abs(currentTime - pendingSeek.seconds) <= 1.25;
        const expired = Date.now() - pendingSeek.requestedAt >= 6000;
        if (confirmed || expired) {
          pendingSeekRef.current = null;
          updateRangePreview(null);
        } else {
          provisionalTime = pendingSeek.seconds;
        }
      }
      const next: YouTubePlaybackSnapshot = {
        currentTime,
        duration: player ? safePlayerNumber(() => player.getDuration()) : 0,
        ready: Boolean(player),
        playing: playing ?? Boolean(player && safePlayerNumber(() => player.getPlayerState()) === 1),
        error,
        provisionalTime,
        scrubbing: scrubbingRef.current,
      };
      if (mounted) {
        setSnapshot(next);
        callbackRef.current?.(next);
      }
      return next;
    };
    const startPoll = () => {
      stopPoll();
      // Poll while paused too: YouTube's public API exposes a seek only after it is applied.
      pollRef.current = window.setInterval(() => { publish(); }, 750);
    };

    void loadYouTubeApi().then(() => {
      if (!mounted || !host || !window.YT?.Player) return;
      const mount = document.createElement("div");
      mount.className = css.youtubePlayer;
      host.replaceChildren(mount);
      const player = new window.YT.Player(mount, {
        videoId,
        host: "https://www.youtube-nocookie.com",
        playerVars: {
          enablejsapi: 1,
          origin: window.location.origin,
          playsinline: 1,
          rel: 0,
        },
        events: {
          onReady: () => {
            if (!mounted) return;
            const playing = playerRef.current?.getPlayerState() === 1;
            publish(playing);
            startPoll();
          },
          onError: (event) => {
            if (!mounted) return;
            stopPoll();
            setErrorCode(event.data);
            publish(false, true);
          },
          onStateChange: (event) => {
            if (!mounted) return;
            const playing = event.data === 1;
            publish(playing);
            startPoll();
          },
        },
      });
      playerRef.current = player;
    }).catch(() => {
      if (!mounted) return;
      setApiError(true);
      const next = { currentTime: 0, duration: 0, ready: false, playing: false, error: true };
      setSnapshot(next);
      callbackRef.current?.(next);
    });

    return () => {
      mounted = false;
      stopPoll();
      playerRef.current?.destroy();
      playerRef.current = null;
      host?.replaceChildren();
    };
    // The player instance is intentionally recreated only when the source video changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId]);

  useEffect(() => {
    if (!snapshot.ready || !seekRequest || !playerRef.current) return;
    const player = playerRef.current;
    const duration = durationRef.current;
    const target = Math.min(duration, Math.max(0, seekRequest.seconds));
    pendingSeekRef.current = { seconds: target, requestedAt: Date.now() };
    updateRangePreview(target);
    player.seekTo(target, true);
    player.playVideo();
    const next: YouTubePlaybackSnapshot = {
      currentTime: safePlayerNumber(() => player.getCurrentTime()), duration, ready: true, playing: true, error: false,
      provisionalTime: target, scrubbing: false,
    };
    setSnapshot(next);
    callbackRef.current?.(next);
  }, [snapshot.ready, seekRequest]);

  function stepPlayback(delta: number) {
    const player = playerRef.current;
    if (!controlsReady || !player) return;
    const current = safePlayerNumber(() => player.getCurrentTime());
    const duration = safePlayerNumber(() => player.getDuration());
    const target = Math.min(duration, Math.max(0, current + delta));
    pendingSeekRef.current = { seconds: target, requestedAt: Date.now() };
    updateRangePreview(target);
    player.seekTo(target, true);
    const next = { ...snapshot, provisionalTime: target, scrubbing: false };
    setSnapshot(next);
    callbackRef.current?.(next);
  }

  function commitRange(value: number) {
    const player = playerRef.current;
    if (!controlsReady || !player) return;
    const target = Math.min(snapshot.duration, Math.max(0, value));
    pendingSeekRef.current = { seconds: target, requestedAt: Date.now() };
    pendingRangeRef.current = null;
    scrubbingRef.current = false;
    updateRangePreview(target);
    player.seekTo(target, true);
    const next = { ...snapshot, provisionalTime: target, scrubbing: false };
    setSnapshot(next);
    callbackRef.current?.(next);
  }

  const displayedTime = rangePreview ?? snapshot.currentTime;
  const disabled = !controlsReady;

  return <>
    <div className={css.playerFrame}>
      <div ref={hostRef} className={css.youtubePlayer} title="Reproductor de YouTube" />
      {apiError ? <p className={css.playerMessage} role="alert">No se pudo cargar el reproductor de YouTube.</p> : null}
      {errorCode !== null ? <p className={css.playerMessage} role="alert">
        YouTube no permite reproducir este vídeo incrustado (código {errorCode}).
      </p> : null}
    </div>
    <div className={css.videoPlaybackControls}>
      <span className={css.playbackTime} aria-live="off">
        {formatTime(displayedTime)} <span aria-hidden="true">/</span> {formatTime(snapshot.duration)}
      </span>
      <input type="range" className={css.playbackTimeline} min={0} max={snapshot.duration || 1} step={0.25}
        value={Math.min(displayedTime, snapshot.duration || 0)} disabled={disabled}
        aria-label="Línea temporal del vídeo"
        aria-valuetext={`${formatTime(displayedTime)} de ${formatTime(snapshot.duration)}`}
        onPointerDown={(event) => {
          scrubbingRef.current = true;
          pendingRangeRef.current = Number(event.currentTarget.value);
          updateRangePreview(pendingRangeRef.current);
          const next = { ...snapshot, provisionalTime: pendingRangeRef.current, scrubbing: true };
          setSnapshot(next);
          callbackRef.current?.(next);
        }}
        onPointerUp={() => {
          if (pendingRangeRef.current !== null) commitRange(pendingRangeRef.current);
        }}
        onPointerCancel={() => {
          if (pendingRangeRef.current !== null) commitRange(pendingRangeRef.current);
        }}
        onKeyDown={(event) => {
          if (["ArrowLeft", "ArrowRight", "Home", "End", "PageDown", "PageUp"].includes(event.key)) {
            scrubbingRef.current = true;
            pendingRangeRef.current = Number(event.currentTarget.value);
            updateRangePreview(pendingRangeRef.current);
            const next = { ...snapshot, provisionalTime: pendingRangeRef.current, scrubbing: true };
            setSnapshot(next);
            callbackRef.current?.(next);
          }
        }}
        onKeyUp={() => {
          if (pendingRangeRef.current !== null) commitRange(pendingRangeRef.current);
        }}
        onBlur={() => {
          if (pendingRangeRef.current !== null) commitRange(pendingRangeRef.current);
        }}
        onChange={(event) => {
          const value = Number(event.currentTarget.value);
          pendingRangeRef.current = value;
          updateRangePreview(value);
          if (scrubbingRef.current) {
            const next = { ...snapshot, provisionalTime: value, scrubbing: true };
            setSnapshot(next);
            callbackRef.current?.(next);
          } else commitRange(value);
        }} />
      <div className={css.playerControls} aria-label="Ajuste manual de reproducción">
        <button type="button" aria-label="Retroceder 15 segundos" onClick={() => stepPlayback(-15)} disabled={disabled}>
          <Rewind size={15} aria-hidden="true" /> −15 s
        </button>
        <button type="button" aria-label="Avanzar 15 segundos" onClick={() => stepPlayback(15)} disabled={disabled}>
          <FastForward size={15} aria-hidden="true" /> +15 s
        </button>
      </div>
    </div>
  </>;
}
