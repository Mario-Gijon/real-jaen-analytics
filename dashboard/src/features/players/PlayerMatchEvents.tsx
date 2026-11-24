"use client";

import { useEffect, useRef, useState } from "react";
import { LoaderCircle, Play } from "lucide-react";
import { getAllEnrichedComments, getMatchVideo, locateMatchVideoEvent } from "@/client/api";
import type { MatchVideoReference, Stat, VideoEventLocation } from "@/contract";
import {
  locationParameters, locationUnavailableReason, PLAYBACK_LEAD_SECONDS, sourceEventId,
} from "@/features/matches/event-video-location";
import { AssistIcon } from "@/features/matches/AssistIcon";
import { GoalIcon } from "@/features/matches/GoalIcon";
import { YouTubePlayer } from "@/features/matches/YouTubePlayer";
import { failureReason, useApi } from "@/hooks/use-api";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { playerMatchEvents, type PlayerMatchEvent } from "./player-match-events";
import shared from "./PlayerDetailView.module.css";
import sections from "../teams/TeamPerformance.module.css";
import stats from "./PlayerMatches.module.css";
import css from "./PlayerMatchEvents.module.css";

type Api<T> = { data: T | null; error: unknown; loading: boolean; retry: () => void };
type AvailableVideo = Extract<MatchVideoReference, { available: true }>;
/** A location as the row shows it: the API's answer, or a request in progress or failed (retryable). */
type LocationState = VideoEventLocation | { status: "loading" } | { status: "error"; message: string };

/**
 * «Eventos» (for now, goals and assists) of one player in one match, inside the
 * selected match's detail of the player's profile. The match's events and its
 * video reference are requested when this detail mounts, so only the open match
 * loads, never the whole history. The parent remounts it per player and match,
 * so nothing from a previous selection survives. Attribution: see `playerMatchEvents`.
 */
export function PlayerMatchEvents({ matchId, playerId, assists }: {
  matchId: string; playerId: string;
  /** The player's official assists in this match, to confirm the assists found in the events. */
  assists: Stat;
}) {
  const eventId = sourceEventId(matchId);
  const comments = useApi(eventId ? `player-match-events|${eventId}` : null,
    (signal) => getAllEnrichedComments(eventId!, { signal }));
  // In parallel with the events: only says whether a video exists; no player, no location.
  const video = useApi(eventId ? `player-match-video|${eventId}` : null,
    (signal) => getMatchVideo(matchId, { signal }));
  return <section className={css.root} aria-labelledby={`player-match-events-${matchId}`}>
    {/* Same static title as «Partidos» and «Detalle del partido» (gold bar, uppercase). */}
    <h3 className={`${sections.zoneTitle} ${css.heading}`} id={`player-match-events-${matchId}`}>Eventos</h3>
    <div className={css.content}>
      <Content matchId={matchId} eventId={eventId} comments={comments} video={video} playerId={playerId} assists={assists} />
    </div>
  </section>;
}

function Content({ matchId, eventId, comments, video, playerId, assists }: {
  matchId: string; eventId: string | null; playerId: string; assists: Stat;
  comments: Api<Awaited<ReturnType<typeof getAllEnrichedComments>>>;
  video: Api<MatchVideoReference>;
}) {
  if (!eventId) return <p className={shared.message}>Los eventos de este partido no están disponibles.</p>;
  if (comments.error) return <div role="alert" className={shared.message}>
    <p>No se pudieron cargar los eventos. {failureReason(comments.error)}</p>
    <Button size="sm" onClick={comments.retry}>Reintentar</Button>
  </div>;
  if (!comments.data) return <LoadingState variant="inline" label="Cargando eventos…" />;
  const { events, omittedAssists, unidentifiedOfficialAssists } = playerMatchEvents(comments.data.items, playerId, assists);
  const assistNotice = omittedAssists > 0 || unidentifiedOfficialAssists > 0;
  return <>
    {events.length
      ? <EventsWithVideo matchId={matchId} events={events} video={video} />
      : <p className={shared.message} role="status">{assistNotice
        ? "No hay goles ni asistencias confirmadas que mostrar en este partido."
        : "Sin goles ni asistencias registrados en este partido."}</p>}
    {assistNotice
      ? <p className={css.notice} role="status">{unidentifiedOfficialAssists > 0
        ? "Las estadísticas oficiales indican asistencias, pero no se han podido identificar todos sus eventos; se omiten las atribuciones no confirmadas."
        : "La narración menciona posibles asistencias, pero no se pueden confirmar con las estadísticas disponibles y no se atribuyen."}</p>
      : null}
  </>;
}

const usableSecond = (location: VideoEventLocation): number | null =>
  location.status === "resolved" && Number.isFinite(location.videoSecond) && location.videoSecond >= 0 ? location.videoSecond : null;

/**
 * The event rows and, once an event is located, one YouTube player under them.
 *
 * - Locations are requested only on Play, per event (one row per source comment) and video, cached
 *   for this match (API answers only; failures can be retried) and shared while
 *   in flight. They are never cancelled by another selection, only on unmount.
 * - The latest Play wins: an older answer neither selects nor seeks.
 * - The player mounts on the first usable location and is reused: each Play is
 *   a new seek request, starting `PLAYBACK_LEAD_SECONDS` before the located second.
 */
function EventsWithVideo({ matchId, events, video }: {
  matchId: string; events: PlayerMatchEvent[]; video: Api<MatchVideoReference>;
}) {
  const reference: AvailableVideo | null = video.data?.available ? video.data : null;
  const scope = reference ? `${matchId}:${reference.youtubeVideoId}` : null;
  const [locations, setLocations] = useState<Record<string, LocationState>>({});
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [seekRequest, setSeekRequest] = useState<{ sequence: number; seconds: number } | null>(null);
  const cache = useRef(new Map<string, VideoEventLocation>());
  const inFlight = useRef(new Map<string, Promise<LocationState>>());
  const latestSelection = useRef(0);
  const seekSequence = useRef(0);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    // Leaving the match or player: pending answers no longer select, seek or update anything.
    return () => controller.abort();
  }, []);

  function locate(locationKey: string, parameters: NonNullable<ReturnType<typeof locationParameters>>): Promise<LocationState> {
    const cached = cache.current.get(locationKey);
    if (cached) return Promise.resolve(cached);
    const pending = inFlight.current.get(locationKey);
    if (pending) return pending;
    const signal = lifetime.current?.signal;
    setLocations((current) => ({ ...current, [locationKey]: { status: "loading" } }));
    const request: Promise<LocationState> = locateMatchVideoEvent(
      matchId, parameters.period, parameters.minute, parameters.eventKind, undefined, { signal },
    ).then((location) => {
      cache.current.set(locationKey, location);
      return location;
    }, (error: unknown): LocationState => ({ status: "error", message: failureReason(error) }))
      .then((outcome) => {
        if (!signal?.aborted) setLocations((current) => ({ ...current, [locationKey]: outcome }));
        return outcome;
      })
      .finally(() => { inFlight.current.delete(locationKey); });
    inFlight.current.set(locationKey, request);
    return request;
  }

  async function play(event: PlayerMatchEvent) {
    const parameters = locationParameters(event.item);
    if (!scope || !parameters) return;
    const selection = ++latestSelection.current;
    const mounted = lifetime.current?.signal;
    const outcome = await locate(`${scope}::${event.key}`, parameters);
    // Only the latest Play, while this match is still mounted, acts on its answer.
    if (mounted?.aborted || selection !== latestSelection.current) return;
    if (outcome.status === "loading" || outcome.status === "error") return;
    const second = usableSecond(outcome);
    if (second === null) return;
    setSelectedKey(event.key);
    setSeekRequest({ sequence: ++seekSequence.current, seconds: Math.max(0, second - PLAYBACK_LEAD_SECONDS) });
  }

  return <>
    {/* In a statistics card. Each row: minute tag, icon, event, related player and, with a video, Play. */}
    <section className={stats.statBlock} aria-label="Eventos del partido">
      <ul className={css.events}>
        {events.map((event) => {
          const detail = related(event);
          const located = scope ? locations[`${scope}::${event.key}`] : undefined;
          const locatable = locationParameters(event.item) !== null;
          const busy = located?.status === "loading";
          const unavailable = !locatable ? "Este evento no tiene minuto y periodo localizables."
            : located && located.status !== "loading" && located.status !== "error"
              ? locationUnavailableReason(located) ?? (usableSecond(located) === null ? "La posición devuelta para este evento no es válida." : null)
              : null;
          // Not `disabled`: a focused button that becomes disabled loses keyboard focus in some browsers.
          // `aria-disabled` keeps it focusable, so the press is ignored here instead.
          const blocked = busy || unavailable !== null;
          const selected = selectedKey === event.key;
          const spoken = event.minute === null ? `${KIND_SPOKEN[event.kind]} sin minuto`
            : `${KIND_SPOKEN[event.kind]} del minuto ${minuteLabel(event).replace("′", "")}`;
          const label = busy ? `Localizando ${spoken} en el vídeo`
            : located?.status === "error" ? `Reintentar la reproducción de ${spoken}` : `Reproducir ${spoken}`;
          return <li key={event.key} className={css.event} data-event-kind={event.kind} data-selected={selected || undefined}>
            <span className={css.minute}>{minuteLabel(event)}</span>
            <span className={css.icon} aria-hidden="true">{event.kind === "assist" ? <AssistIcon /> : <GoalIcon />}</span>
            <span className={css.text}>
              <strong className={css.kind}>{KIND_LABEL[event.kind]}</strong>
              {detail ? <span className={css.detail}>{detail}</span> : null}
            </span>
            {reference ? <button type="button" className={css.play} aria-label={label} aria-pressed={selected}
              aria-busy={busy || undefined} aria-disabled={blocked || undefined} onClick={() => { if (!blocked) void play(event); }}>
              {busy ? <LoaderCircle size={16} aria-hidden="true" className={css.spinner} /> : <Play size={16} aria-hidden="true" />}
            </button> : null}
            {reference && unavailable ? <p className={css.rowMessage} role="status">No se puede reproducir: {unavailable}</p> : null}
            {reference && located?.status === "error" ? <p className={css.rowMessage} role="alert">
              No se pudo localizar el evento en el vídeo. {located.message}
            </p> : null}
          </li>;
        })}
      </ul>
    </section>
    {video.error ? <div role="alert" className={`${shared.message} ${css.videoNote}`}>
      <p>No se pudo consultar el vídeo del partido. {failureReason(video.error)}</p>
      <Button size="sm" onClick={video.retry}>Reintentar</Button>
    </div> : video.data?.available === false
      ? <p className={`${css.notice} ${css.videoNote}`}>Este partido no tiene vídeo asociado.</p> : null}
    {/* One player per match detail, mounted on the first located event (the first seek request);
        the reference never changes while mounted: it is only retried after an error, without a video.
        YouTube's own errors show inside it. */}
    {reference && seekRequest ? <div className={css.player}>
      <YouTubePlayer key={reference.youtubeVideoId} videoId={reference.youtubeVideoId} seekRequest={seekRequest} />
    </div> : null}
  </>;
}

const KIND_LABEL: Record<PlayerMatchEvent["kind"], string> = { goal: "Gol", penalty_goal: "Gol de penalti", assist: "Asistencia confirmada" };
const KIND_SPOKEN: Record<PlayerMatchEvent["kind"], string> = { goal: "gol", penalty_goal: "gol de penalti", assist: "asistencia confirmada" };

/** Match-clock reading: stoppage time as 45+N (first half) and 90+N (second half). */
function minuteLabel({ minute, period }: PlayerMatchEvent): string {
  if (minute === null) return "Sin minuto";
  if (period === "first" && minute > 45) return `45+${minute - 45}′`;
  if (period === "second" && minute > 90) return `90+${minute - 90}′`;
  return `${minute}′`;
}

/** The other participant, only when the event names one: never invented. */
function related(event: PlayerMatchEvent): string | null {
  const name = event.related?.name ?? null;
  if (!name || !event.related || event.kind === "penalty_goal") return null;
  return event.related.attribution === "narrative"
    ? `Según la narración, asistencia de ${name}`
    : `Gol de ${name}`;
}
