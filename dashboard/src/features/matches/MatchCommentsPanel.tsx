"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, Plus, Trash2, Video, X } from "lucide-react";
import { deleteMatchVideoReference, getAllEnrichedComments, getCurrentMatchVideoJob, getMatchVideo, getMatchVideoJob, locateMatchVideoEvent, startMatchVideoJob } from "@/client/api";
import type { EnrichedComment, PlayerRef, VideoEventLocation, VideoJob } from "@/contract";
import { PlayerLink } from "@/features/players/PlayerLink";
import type { ProfileOrigin } from "@/lib/navigation";
import { Button } from "@/ui/Button";
import { LoadingState } from "@/ui/LoadingState";
import { failureReason, useApi } from "@/hooks/use-api";
import { YouTubePlayer } from "./YouTubePlayer";
import type { YouTubePlaybackSnapshot } from "./YouTubePlayer";
import css from "./MatchCommentsPanel.module.css";

const CATEGORIES = [
  ["goal", "Goles"], ["goal_review", "Goles anulados"], ["shot", "Remates"], ["foul", "Faltas"],
  ["set_piece", "Córneres"], ["substitution", "Sustituciones"], ["discipline", "Tarjetas"],
  ["offside", "Fueras de juego"], ["penalty", "Penaltis"], ["var", "VAR"], ["match_flow", "Desarrollo del partido"],
] as const;
const CATEGORY_LABELS: Readonly<Record<string, string>> = Object.fromEntries(CATEGORIES);
const MATCH_FLOW_LABELS: Readonly<Record<string, string>> = {
  matchStarted: "Inicio del partido",
  startDelay: "Inicio retrasado",
  endDelay: "Fin del retraso",
  addedTime: "Tiempo añadido",
  endFirstHalf: "Final de la primera parte",
  endSecondHalf: "Final de la segunda parte",
  matchEnded: "Final del partido",
};
const matchIdPattern = /^sofascore-match-([1-9]\d*)$/;
const publicPlayerIdPattern = /^sofascore-player-[1-9]\d*$/;
const youtubeVideoIdPattern = /^[A-Za-z0-9_-]{11}$/;
const activeVideoJobStatuses = new Set<VideoJob["status"]>(["pending", "downloading", "calibrating", "saving"]);
const KNOWN_GIVEN_NAME_ALIASES = new Map([["eduardo", "edu"]]);
const EMPTY_COMMENTS: EnrichedComment[] = [];

type FilterTeam = "all" | "home" | "away";
type PlayerChoice = { id: string; name: string; label: string };
type Mention = { start: number; end: number; id: string; name: string; player: PlayerRef };
type ResolvedParticipant = { name: string | null; player: PlayerRef | null; optionName: string | null };
type EventRow = { item: EnrichedComment; participants: ResolvedParticipant[]; mentions: Mention[] };
type LocationState = VideoEventLocation | { status: "loading" } | { status: "error"; message: string };

function sourceEventId(matchId: string): string | null {
  return matchIdPattern.exec(matchId)?.[1] ?? null;
}

function isYouTubeVideoUrl(value: string): boolean {
  try {
    const url = new URL(value.trim());
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return false;
    let id: string | null = null;
    if (["youtu.be", "www.youtu.be"].includes(host)) {
      const parts = url.pathname.split("/").filter(Boolean);
      if (parts.length === 1) id = parts[0];
    } else if (["youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"].includes(host)) {
      if (url.pathname === "/watch") {
        const values = url.searchParams.getAll("v");
        if (values.length === 1) id = values[0];
      } else {
        const parts = url.pathname.split("/").filter(Boolean);
        if (parts.length === 2 && ["embed", "live", "shorts"].includes(parts[0])) id = parts[1];
      }
    }
    return id !== null && youtubeVideoIdPattern.test(id);
  } catch {
    return false;
  }
}

function videoJobLabel(job: VideoJob): string {
  const labels: Record<VideoJob["status"], string> = {
    pending: "Pendiente",
    downloading: "Descargando vídeo",
    calibrating: "Analizando el reloj y sincronizando con OCR",
    saving: "Guardando la sincronización",
    completed: "Procesamiento completado",
    failed: "No se pudo completar el procesamiento",
  };
  return labels[job.status];
}

function nameTokens(value: string): string[] {
  return value.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("es-ES").match(/[\p{L}\p{N}]+/gu) ?? [];
}

function nameKey(value: string): string {
  return nameTokens(value).join(" ");
}

function sameRosterPlayer(name: string, rosterName: string): boolean {
  const source = nameTokens(name);
  const roster = nameTokens(rosterName);
  if (source.length < 2 || source[0] !== roster[0] || source.at(-1) !== roster.at(-1) || source.length > roster.length) return false;
  return isTokenSubsequence(source, roster);
}

function isTokenSubsequence(source: string[], target: string[]): boolean {
  let cursor = 0;
  return source.every((token) => {
    const index = target.indexOf(token, cursor);
    if (index < 0) return false;
    cursor = index + 1;
    return true;
  });
}

function resolveParticipant(participant: EnrichedComment["participants"][number], roster: PlayerRef[]): ResolvedParticipant {
  const name = participant.name?.trim() || null;
  const direct = participant.player && publicPlayerIdPattern.test(participant.player.id) ? participant.player : null;
  if (direct) return { name, player: direct, optionName: name ? direct.name : null };
  if (!name || participant.sourceRole !== "player") return { name, player: null, optionName: null };

  const candidates = new Map<string, PlayerRef>();
  for (const player of roster) {
    if (publicPlayerIdPattern.test(player.id) && sameRosterPlayer(name, player.name)) candidates.set(player.id, player);
  }
  const player = candidates.size === 1 ? [...candidates.values()][0] : null;
  return { name, player, optionName: player ? player.name : null };
}

function eventLabel(item: EnrichedComment): string {
  if (item.category === "match_flow") return MATCH_FLOW_LABELS[item.originalType] ?? "Desarrollo del partido";
  if (!item.category) return "Sin clasificar";
  return CATEGORY_LABELS[item.category] ?? "Evento";
}

function eventKey(item: EnrichedComment): string {
  return `${item.captureId ?? "capture"}-${typeof item.id}-${item.id}`;
}

function locationParameters(item: EnrichedComment) {
  const period = periodFor(item.periodName);
  const eventKind: "other" | "match_start" | "period_start" = item.originalType === "matchStarted" && period === "first" ? "match_start"
    : item.originalType === "matchStarted" && period === "second" ? "period_start" : "other";
  const periodStart = eventKind !== "other";
  if ((!periodStart && (item.minute === null || item.minute < 1)) || !period) return null;
  const minute = item.minute !== null && item.minute >= 1 ? minuteForVideo(item.minute, period) : null;
  return { period, eventKind, minute };
}

function playerChoices(rows: EventRow[]): PlayerChoice[] {
  const namesById = new Map<string, Map<string, { name: string; count: number }>>();
  for (const row of rows) for (const participant of row.participants) {
    const player = participant.player;
    const name = participant.optionName?.trim();
    if (!player || !publicPlayerIdPattern.test(player.id) || !name) continue;
    const names = namesById.get(player.id) ?? new Map<string, { name: string; count: number }>();
    const key = name.toLocaleLowerCase("es-ES");
    const current = names.get(key);
    names.set(key, { name: current?.name ?? name, count: (current?.count ?? 0) + 1 });
    namesById.set(player.id, names);
  }
  for (const row of rows) for (const mention of row.mentions) {
    const names = namesById.get(mention.player.id) ?? new Map<string, { name: string; count: number }>();
    const key = mention.player.name.toLocaleLowerCase("es-ES");
    const current = names.get(key);
    names.set(key, { name: current?.name ?? mention.player.name, count: (current?.count ?? 0) + 1 });
    namesById.set(mention.player.id, names);
  }
  const options = [...namesById.entries()].flatMap(([id, names]) => {
    const mostCommon = [...names.values()].sort((left, right) => right.count - left.count)[0];
    return mostCommon ? [{ id, name: mostCommon.name, label: mostCommon.name }] : [];
  }).sort((left, right) => left.name.localeCompare(right.name, "es-ES") || left.id.localeCompare(right.id));

  const countByName = new Map<string, number>();
  for (const option of options) {
    const key = option.name.toLocaleLowerCase("es-ES");
    countByName.set(key, (countByName.get(key) ?? 0) + 1);
  }
  return options.map((option) => ({
    ...option,
    // The public ID is shown only to disambiguate two different players with the same name.
    label: (countByName.get(option.name.toLocaleLowerCase("es-ES")) ?? 0) > 1
      ? `${option.name} (ID ${option.id.slice("sofascore-player-".length)})` : option.name,
  }));
}

function isWordCharacter(value: string | undefined): boolean {
  return value !== undefined && /[\p{L}\p{M}\p{N}_]/u.test(value);
}

function mentionAliases(name: string): string[] {
  const words = nameTokens(name);
  if (words.length < 2) return [];
  const aliases = new Set([words.join(" "), `${words[0]} ${words.at(-1)}`]);
  for (let index = 1; index < words.length; index += 1) aliases.add(`${words[0]} ${words[index]}`);
  if (words.length > 2) aliases.add(words.slice(-2).join(" "));
  return [...aliases];
}

function knownPlayerAliases(name: string): string[] {
  const words = nameTokens(name);
  if (words.length < 2) return [];
  const alias = KNOWN_GIVEN_NAME_ALIASES.get(words[0]);
  return alias ? [`${alias} ${words.at(-1)}`] : [];
}

function rosterMatches(name: string, playerId: string, roster: PlayerRef[]): boolean {
  const matches = new Map<string, PlayerRef>();
  for (const player of roster) {
    if (publicPlayerIdPattern.test(player.id) && isTokenSubsequence(nameTokens(name), nameTokens(player.name))) matches.set(player.id, player);
  }
  return matches.size === 1 && matches.has(playerId);
}

type SearchableText = { value: string; starts: number[]; ends: number[] };

function searchableText(text: string): SearchableText {
  let value = "";
  const starts: number[] = [];
  const ends: number[] = [];
  for (let offset = 0; offset < text.length;) {
    const char = String.fromCodePoint(text.codePointAt(offset)!);
    const end = offset + char.length;
    const folded = char.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase("es-ES");
    const letters = [...folded].filter((part) => /[\p{L}\p{N}]/u.test(part));
    if (letters.length) {
      for (const part of letters) {
        value += part;
        for (let unit = 0; unit < part.length; unit += 1) { starts.push(offset); ends.push(end); }
      }
    } else if (value && !value.endsWith(" ")) {
      value += " ";
      starts.push(offset);
      ends.push(end);
    }
    offset = end;
  }
  if (value.endsWith(" ")) { value = value.slice(0, -1); starts.pop(); ends.pop(); }
  return { value, starts, ends };
}

function addCandidate(index: Map<string, Map<string, PlayerRef>>, alias: string, player: PlayerRef) {
  const key = nameKey(alias);
  if (!key || !publicPlayerIdPattern.test(player.id)) return;
  const matches = index.get(key) ?? new Map<string, PlayerRef>();
  matches.set(player.id, player);
  index.set(key, matches);
}

function playerMentions(text: string, participants: ResolvedParticipant[], roster: PlayerRef[]): Mention[] {
  const structured = new Map<string, Map<string, PlayerRef>>();
  const structuredDirect = new Map<string, Map<string, PlayerRef>>();
  const rosterNames = new Map<string, Map<string, PlayerRef>>();
  const blockedNames = new Set<string>();
  const knownAliasNames = new Set<string>();

  for (const player of roster) {
    for (const alias of mentionAliases(player.name)) addCandidate(rosterNames, alias, player);
    for (const alias of knownPlayerAliases(player.name)) {
      addCandidate(rosterNames, alias, player);
      knownAliasNames.add(nameKey(alias));
    }
  }
  for (const participant of participants) {
    const player = participant.player;
    if (!player || !publicPlayerIdPattern.test(player.id)) {
      if (participant.name) blockedNames.add(nameKey(participant.name));
      continue;
    }
    if (participant.name) {
      addCandidate(structured, participant.name, player);
      addCandidate(structuredDirect, participant.name, player);
    }
    addCandidate(structured, player.name, player);
    addCandidate(structuredDirect, player.name, player);
    for (const alias of [...knownPlayerAliases(participant.name ?? ""), ...knownPlayerAliases(player.name)]) {
      addCandidate(structured, alias, player);
    }
    for (const alias of mentionAliases(player.name)) {
      if (nameKey(alias) === nameKey(player.name) || rosterMatches(alias, player.id, roster)) addCandidate(structured, alias, player);
    }
  }

  const normalized = searchableText(text);
  const matches: Mention[] = [];
  const aliases = new Set([...structured.keys(), ...rosterNames.keys()]);
  for (const alias of aliases) {
    const structuredIds = structured.get(alias);
    const directIds = structuredDirect.get(alias);
    const rosterCandidates = rosterNames.get(alias);
    let candidates: Map<string, PlayerRef> | undefined;
    if (knownAliasNames.has(alias)) {
      // A directly supplied SofaScore identity wins. Otherwise, only infer the
      // explicit alias from one roster ID, with no conflicting structured alias.
      if (directIds) {
        if (directIds.size !== 1) continue;
        candidates = directIds;
      } else if (structuredIds) {
        if (!rosterCandidates || rosterCandidates.size !== 1 || structuredIds.size !== 1
          || !structuredIds.has([...rosterCandidates.keys()][0])) continue;
        candidates = structuredIds;
      } else {
        if (!rosterCandidates || rosterCandidates.size !== 1) continue;
        candidates = rosterCandidates;
      }
    } else if (blockedNames.has(alias)) continue;
    else candidates = structuredIds ?? rosterCandidates;
    if (!candidates || candidates.size !== 1) continue;
    const player = [...candidates.values()][0];
    const expression = new RegExp(alias.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gu");
    for (const match of normalized.value.matchAll(expression)) {
      const start = match.index;
      const normalizedEnd = start + match[0].length;
      if (isWordCharacter(Array.from(normalized.value.slice(0, start)).at(-1))
        || isWordCharacter(Array.from(normalized.value.slice(normalizedEnd))[0])) continue;
      const originalStart = normalized.starts[start];
      const originalEnd = normalized.ends[normalizedEnd - 1];
      if (originalStart !== undefined && originalEnd !== undefined) {
        matches.push({ start: originalStart, end: originalEnd, id: player.id, name: text.slice(originalStart, originalEnd), player });
      }
    }
  }

  matches.sort((left, right) => left.start - right.start || right.end - left.end);
  const selected: Mention[] = [];
  let group: Mention[] = [];
  let groupEnd = -1;
  const flush = () => {
    if (!group.length) return;
    const longest = Math.max(...group.map((match) => match.end - match.start));
    const best = group.filter((match) => match.end - match.start === longest);
    if (new Set(best.map((match) => match.id)).size === 1) selected.push(best[0]);
    group = [];
    groupEnd = -1;
  };
  for (const match of matches) {
    if (group.length && match.start >= groupEnd) flush();
    group.push(match);
    groupEnd = Math.max(groupEnd, match.end);
  }
  flush();
  return selected.sort((left, right) => left.start - right.start);
}

function resolvedRows(items: EnrichedComment[], roster: PlayerRef[]): EventRow[] {
  return items.map((item) => {
    const participants = item.participants.map((participant) => resolveParticipant(participant, roster));
    return { item, participants, mentions: playerMentions(item.text, participants, roster) };
  });
}

function TextWithPlayers({ item, mentions, seasonId, profileOrigin }: {
  item: EnrichedComment; mentions: Mention[]; seasonId?: string; profileOrigin?: ProfileOrigin;
}) {
  if (!mentions.length) return <>{item.text}</>;
  const parts: ReactNode[] = [];
  let cursor = 0;
  for (const [index, mention] of mentions.entries()) {
    if (mention.start > cursor) parts.push(item.text.slice(cursor, mention.start));
    parts.push(<PlayerLink key={`${mention.id}-${mention.start}-${index}`} player={{ ...mention.player, name: mention.name }}
      seasonId={seasonId} profileOrigin={profileOrigin} className={css.playerMention} />);
    cursor = mention.end;
  }
  if (cursor < item.text.length) parts.push(item.text.slice(cursor));
  return <>{parts.map((part, index) => typeof part === "string" ? <Fragment key={`text-${index}`}>{part}</Fragment> : part)}</>;
}

export function MatchCommentsPanel({ matchId, seasonId, homeName, awayName, profileOrigin, rosterPlayers = [] }: {
  matchId: string; seasonId?: string; homeName?: string | null; awayName?: string | null; profileOrigin?: ProfileOrigin; rosterPlayers?: PlayerRef[];
}) {
  const eventId = sourceEventId(matchId);
  const result = useApi(eventId ? `match-events|${eventId}` : null,
    (signal) => getAllEnrichedComments(eventId!, { signal }));
  const videoResult = useApi(eventId ? `match-video|${eventId}` : null,
    (signal) => getMatchVideo(matchId, { signal }));
  const videoRetry = useRef(videoResult.retry);
  const [category, setCategory] = useState("");
  const [playerId, setPlayerId] = useState("");
  const [team, setTeam] = useState<FilterTeam>("all");
  const [locationByKey, setLocationByKey] = useState<Record<string, LocationState>>({});
  const [selectedEventKey, setSelectedEventKey] = useState<string | null>(null);
  const [expandedEventKeys, setExpandedEventKeys] = useState<Set<string>>(() => new Set());
  const [seekRequest, setSeekRequest] = useState<{ sequence: number; seconds: number; scope: string } | null>(null);
  const [scrollRange, setScrollRange] = useState({ left: 0, max: 0 });
  const [playback, setPlayback] = useState<YouTubePlaybackSnapshot>({
    currentTime: 0, duration: 0, ready: false, playing: false, error: false,
  });
  const [manualCarouselPaused, setManualCarouselPaused] = useState(false);
  const [videoJob, setVideoJob] = useState<VideoJob | null>(null);
  const [videoJobLoaded, setVideoJobLoaded] = useState(false);
  const [videoJobError, setVideoJobError] = useState<string | null>(null);
  const [addVideoOpen, setAddVideoOpen] = useState(false);
  const [deleteVideoOpen, setDeleteVideoOpen] = useState(false);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [operationError, setOperationError] = useState<string | null>(null);
  const [submittingVideo, setSubmittingVideo] = useState(false);
  const [deletingVideo, setDeletingVideo] = useState(false);
  const [videoOperationBusy, setVideoOperationBusy] = useState(false);
  const eventsRef = useRef<HTMLOListElement>(null);
  const eventCardRefs = useRef(new Map<string, HTMLLIElement>());
  const lastAutoFollowedKey = useRef<string | null>(null);
  const locationCache = useRef(new Map<string, LocationState>());
  const locationRequests = useRef(new Map<string, Promise<LocationState>>());
  const latestLocateRequest = useRef(0);
  const manualCarouselTimer = useRef<number | null>(null);
  const jobPollInFlight = useRef(false);
  const items = result.data?.items ?? EMPTY_COMMENTS;
  const rows = useMemo(() => resolvedRows(items, rosterPlayers), [items, rosterPlayers]);
  const players = useMemo(() => playerChoices(rows), [rows]);
  const filtered = useMemo(() => rows.filter(({ item, participants, mentions }) => {
    const categoryMatches = category === "__unclassified" ? item.category === null : !category || item.category === category;
    const playerMatches = !playerId || participants.some((participant) => participant.player?.id === playerId)
      || mentions.some((mention) => mention.id === playerId);
    const teamMatches = team === "all" || (team === "home" ? item.isHome === true : item.isHome === false);
    return categoryMatches && playerMatches && teamMatches;
  }), [rows, category, playerId, team]);

  const chronological = useMemo(() => [...filtered].sort((left, right) => right.item.position - left.item.position), [filtered]);
  const videoReference = videoResult.data?.available ? videoResult.data : null;
  const hasVideo = videoReference !== null;
  const videoScope = `${matchId}:${videoReference?.youtubeVideoId ?? ""}`;
  const timelineVideoSecond = playback.provisionalTime ?? playback.currentTime;
  const activeJobId = videoJob?.jobId;
  const activeJobStatus = videoJob?.status;

  const playbackUpdate = useCallback((next: YouTubePlaybackSnapshot) => setPlayback(next), []);
  const rememberLocation = useCallback((key: string, location: LocationState) => {
    if (location.status !== "loading") locationCache.current.set(key, location);
    setLocationByKey((current) => ({ ...current, [key]: location }));
  }, []);
  const requestLocation = useCallback((item: EnrichedComment, key: string): Promise<LocationState> => {
    const parameters = locationParameters(item);
    if (!parameters) return Promise.resolve({ status: "error", message: "El evento no tiene minuto y periodo localizables." });
    const cached = locationCache.current.get(key);
    if (cached && cached.status !== "error" && cached.status !== "loading") return Promise.resolve(cached);
    const inFlight = locationRequests.current.get(key);
    if (inFlight) return inFlight;

    const request = locateMatchVideoEvent(matchId, parameters.period, parameters.minute, parameters.eventKind)
      .then((location) => {
        rememberLocation(key, location);
        return location;
      })
      .catch((error: unknown) => {
        const failed: LocationState = { status: "error", message: failureReason(error) };
        rememberLocation(key, failed);
        return failed;
      })
      .finally(() => { locationRequests.current.delete(key); });
    locationRequests.current.set(key, request);
    return request;
  }, [matchId, rememberLocation]);

  useEffect(() => { videoRetry.current = videoResult.retry; });

  useEffect(() => {
    if (!videoReference || videoReference.synchronizationStatus !== "calibrated") return;
    let active = true;
    let cursor = 0;
    const candidates = chronological.filter(({ item }) => locationParameters(item) !== null);
    const worker = async () => {
      while (active && cursor < candidates.length) {
        const row = candidates[cursor++];
        await requestLocation(row.item, `${videoScope}::${eventKey(row.item)}`);
      }
    };
    void Promise.all(Array.from({ length: Math.min(3, candidates.length) }, () => worker()));
    return () => { active = false; };
  }, [chronological, requestLocation, videoReference, videoScope]);

  const activePlaybackEventKey = useMemo(() => {
    if (!hasVideo || !playback.ready) return null;
    const pointsBySecond = new Map<number, string>();
    for (const { item } of chronological) {
      const key = eventKey(item);
      const location = locationByKey[`${videoScope}::${key}`];
      if (location?.status !== "resolved") continue;
      const second = Math.round(location.videoSecond);
      if (!pointsBySecond.has(second)) pointsBySecond.set(second, key);
    }
    const points = [...pointsBySecond.entries()].sort(([left], [right]) => left - right);
    let activeKey: string | null = null;
    for (const [second, key] of points) {
      if (second > timelineVideoSecond + 0.5) break;
      activeKey = key;
    }
    return activeKey;
  }, [chronological, hasVideo, locationByKey, playback.ready, timelineVideoSecond, videoScope]);

  useEffect(() => {
    let active = true;
    setVideoJob(null);
    setVideoJobError(null);
    setVideoJobLoaded(false);
    void getCurrentMatchVideoJob(matchId).then((job) => {
      if (!active) return;
      setVideoJob(job);
      setVideoJobError(null);
      setVideoJobLoaded(true);
      if (job?.status === "completed") videoRetry.current();
    }).catch((error: unknown) => {
      if (active) {
        setVideoJobError(failureReason(error));
        setVideoJobLoaded(true);
      }
    });
    return () => { active = false; };
  }, [eventId, matchId]);

  useEffect(() => {
    if (!activeJobStatus || !activeVideoJobStatuses.has(activeJobStatus)) return;
    if (!activeJobId) return;
    let active = true;
    const poll = window.setInterval(() => {
      if (jobPollInFlight.current) return;
      jobPollInFlight.current = true;
      void getMatchVideoJob(matchId, activeJobId).then((job) => {
        if (!active) return;
        setVideoJob(job);
        setVideoJobError(null);
        setVideoJobLoaded(true);
        if (job?.status === "completed") videoRetry.current();
      }).catch((error: unknown) => {
        if (active) setVideoJobError(failureReason(error));
      }).finally(() => { jobPollInFlight.current = false; });
    }, 2000);
    return () => { active = false; window.clearInterval(poll); };
  }, [activeJobId, activeJobStatus, matchId]);

  useEffect(() => {
    if (!addVideoOpen && !deleteVideoOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submittingVideo && !deletingVideo) {
        setAddVideoOpen(false);
        setDeleteVideoOpen(false);
        setOperationError(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [addVideoOpen, deleteVideoOpen, submittingVideo, deletingVideo]);

  useEffect(() => {
    const list = eventsRef.current;
    if (!list) return;
    const updateRange = () => setScrollRange({ left: list.scrollLeft, max: Math.max(0, list.scrollWidth - list.clientWidth) });
    list.addEventListener("scroll", updateRange, { passive: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(updateRange);
    observer?.observe(list);
    updateRange();
    return () => {
      list.removeEventListener("scroll", updateRange);
      observer?.disconnect();
    };
  }, [chronological.length, hasVideo]);

  useEffect(() => {
    const scrubbingTimeline = playback.scrubbing === true;
    if (!hasVideo || !activePlaybackEventKey || (!scrubbingTimeline && manualCarouselPaused)) return;
    const activeChanged = lastAutoFollowedKey.current !== activePlaybackEventKey;
    if (!scrubbingTimeline && !playback.playing && !activeChanged) return;
    const list = eventsRef.current;
    const card = eventCardRefs.current.get(activePlaybackEventKey);
    if (!list || !card) return;
    const listBounds = list.getBoundingClientRect();
    const cardBounds = card.getBoundingClientRect();
    if (cardBounds.left < listBounds.left) {
      list.scrollBy({ left: cardBounds.left - listBounds.left, behavior: "smooth" });
    } else if (cardBounds.right > listBounds.right) {
      list.scrollBy({ left: cardBounds.right - listBounds.right, behavior: "smooth" });
    }
    lastAutoFollowedKey.current = activePlaybackEventKey;
  }, [activePlaybackEventKey, hasVideo, manualCarouselPaused, playback.playing, playback.scrubbing]);

  useEffect(() => () => {
    if (manualCarouselTimer.current !== null) window.clearTimeout(manualCarouselTimer.current);
  }, []);

  function noteManualCarouselUse() {
    setManualCarouselPaused(true);
    if (manualCarouselTimer.current !== null) window.clearTimeout(manualCarouselTimer.current);
    manualCarouselTimer.current = window.setTimeout(() => {
      setManualCarouselPaused(false);
    }, 2800);
  }

  function scrollCarousel(direction: -1 | 1) {
    noteManualCarouselUse();
    const list = eventsRef.current;
    if (!list) return;
    list.scrollBy({ left: direction * Math.max(280, Math.round(list.clientWidth * 0.82)), behavior: "smooth" });
  }

  async function locateEvent(item: EnrichedComment, key: string) {
    if (!locationParameters(item)) return;
    const scopedKey = `${videoScope}::${key}`;
    const requestNumber = latestLocateRequest.current + 1;
    latestLocateRequest.current = requestNumber;
    setSelectedEventKey(scopedKey);
    const cached = locationCache.current.get(scopedKey);
    if (cached && cached.status !== "error" && cached.status !== "loading") {
      if (cached.status === "resolved") setSeekRequest({ sequence: requestNumber, seconds: cached.videoSecond, scope: videoScope });
      return;
    }
    setLocationByKey((current) => ({ ...current, [scopedKey]: { status: "loading" } }));
    const location = await requestLocation(item, scopedKey);
    if (latestLocateRequest.current !== requestNumber) return;
    if (location.status === "resolved") setSeekRequest({ sequence: requestNumber, seconds: location.videoSecond, scope: videoScope });
  }

  async function addVideo(event: import("react").FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedUrl = youtubeUrl.trim();
    if (!isYouTubeVideoUrl(normalizedUrl)) {
      setOperationError("Pega un enlace HTTPS válido de YouTube.");
      return;
    }
    setSubmittingVideo(true);
    setVideoOperationBusy(true);
    setOperationError(null);
    try {
      const job = await startMatchVideoJob(matchId, normalizedUrl);
      setVideoJob(job);
      setVideoJobError(null);
      setYoutubeUrl("");
      setAddVideoOpen(false);
      if (job.status === "completed") videoRetry.current();
    } catch (error) {
      setOperationError(failureReason(error));
    } finally {
      setSubmittingVideo(false);
      setVideoOperationBusy(false);
    }
  }

  async function removeVideo() {
    setDeletingVideo(true);
    setVideoOperationBusy(true);
    setOperationError(null);
    try {
      await deleteMatchVideoReference(matchId);
      setDeleteVideoOpen(false);
      setVideoJob(null);
      setVideoJobError(null);
      videoResult.retry();
    } catch (error) {
      setOperationError(failureReason(error));
    } finally {
      setDeletingVideo(false);
      setVideoOperationBusy(false);
    }
  }

  if (!eventId) return <p className={css.empty} role="status">La cronología de eventos no está disponible para este partido.</p>;
  return <section className={css.panel} aria-label="Eventos del partido" data-video-layout={hasVideo ? "wide" : undefined}>
    <div className={css.videoSection} aria-label="Vídeo del partido">
      {videoResult.loading ? <p className={css.videoStatus} role="status" aria-busy="true">Comprobando vídeo asociado…</p>
        : videoResult.error ? <div className={`${css.videoStatus} ${css.message}`} role="alert">
        <p>No se pudo consultar el vídeo asociado. {failureReason(videoResult.error)}</p>
        <Button size="sm" onClick={videoResult.retry}>Reintentar</Button>
      </div> : videoReference ? <>
        <div className={css.videoToolbar}>
          <Button size="sm" onClick={() => { setOperationError(null); setDeleteVideoOpen(true); }}
            disabled={videoOperationBusy} icon={<Trash2 size={15} aria-hidden="true" />}>
            Eliminar vídeo de referencia
          </Button>
        </div>
        <YouTubePlayer key={videoReference.youtubeVideoId} videoId={videoReference.youtubeVideoId}
          seekRequest={seekRequest?.scope === videoScope ? seekRequest : null} onPlaybackUpdate={playbackUpdate} />
      </> : videoResult.data?.available === false ? <>
        <div className={css.videoToolbar}>
          {videoJob && activeVideoJobStatuses.has(videoJob.status)
            ? <Button size="sm" disabled icon={<Plus size={16} aria-hidden="true" />}>Añadir vídeo</Button>
            : <Button size="sm" disabled={videoOperationBusy || !videoJobLoaded} icon={<Plus size={16} aria-hidden="true" />} onClick={() => {
              setOperationError(null);
              setAddVideoOpen(true);
            }}>Añadir vídeo</Button>}
        </div>
        {videoJob && <div className={css.jobStatus} role={videoJob.status === "failed" ? "alert" : "status"} aria-live="polite">
          <strong>{videoJobLabel(videoJob)}.</strong> <span>{videoJob.message}</span>
          {videoJob.status === "failed" && <Button size="sm" onClick={() => {
            setOperationError(null);
            setAddVideoOpen(true);
          }}>Reintentar</Button>}
        </div>}
        {videoJobError && <p className={css.videoJobError} role="alert">No se pudo consultar el estado de la incorporación. {videoJobError}</p>}
      </> : null}
    </div>

    {result.error ? <div className={css.message} role="alert">
      <p>No se pudieron cargar los eventos. {failureReason(result.error)}</p>
      <Button size="sm" onClick={result.retry}>Reintentar</Button>
    </div> : !result.data ? <div aria-busy="true"><LoadingState variant="section" label="Cargando eventos…" /></div> : <>
      <div className={css.filters} aria-label="Filtros de eventos">
        <label>Tipo de evento
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">Todos</option>
            {CATEGORIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            <option value="__unclassified">Sin clasificar</option>
          </select>
        </label>
        <label>Jugador
          <select value={playerId} onChange={(event) => setPlayerId(event.target.value)}>
            <option value="">Todos los jugadores</option>
            {players.map((player) => <option key={player.id} value={player.id}>{player.label}</option>)}
          </select>
        </label>
        <label>Equipo
          <select value={team} onChange={(event) => setTeam(event.target.value as FilterTeam)}>
            <option value="all">Ambos equipos</option>
            <option value="home">{homeName || "Equipo local"}</option>
            <option value="away">{awayName || "Equipo visitante"}</option>
          </select>
        </label>
      </div>
      {chronological.length === 0 ? <p className={css.empty} role="status">
        {items.length ? "No hay eventos que coincidan con estos filtros." : "No hay eventos disponibles para este partido."}
      </p> : <>
        {hasVideo ? <div className={css.carouselControls} aria-label="Navegación de eventos">
          <button type="button" className={css.scrollButton} aria-label="Eventos anteriores"
            disabled={scrollRange.left <= 0} onClick={() => scrollCarousel(-1)}>
            <ChevronLeft size={18} aria-hidden="true" />
          </button>
          <button type="button" className={css.scrollButton} aria-label="Eventos siguientes"
            disabled={scrollRange.left >= scrollRange.max} onClick={() => scrollCarousel(1)}>
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        </div> : null}
        <ol ref={eventsRef} className={`${css.timeline} ${hasVideo ? css.timelineVideo : ""}`}
          aria-label="Eventos en orden cronológico" tabIndex={hasVideo ? 0 : undefined}
          onPointerDown={hasVideo ? noteManualCarouselUse : undefined}
          onPointerMove={hasVideo ? noteManualCarouselUse : undefined}
          onTouchStart={hasVideo ? noteManualCarouselUse : undefined}
          onTouchMove={hasVideo ? noteManualCarouselUse : undefined}
          onWheel={hasVideo ? noteManualCarouselUse : undefined}
          onKeyDown={hasVideo ? (event) => {
            if (["ArrowLeft", "ArrowRight", "Home", "End", "PageDown", "PageUp"].includes(event.key)) noteManualCarouselUse();
          } : undefined}>
      {chronological.map(({ item, mentions }) => {
        const key = eventKey(item);
        const scopedKey = `${videoScope}::${key}`;
        const period = periodFor(item.periodName);
        const eventKind = item.originalType === "matchStarted" && period === "first" ? "match_start"
          : item.originalType === "matchStarted" && period === "second" ? "period_start" : "other";
        const periodStart = eventKind !== "other";
        const location = locationByKey[scopedKey];
        const nonlocalizable = (!periodStart && (item.minute === null || item.minute < 1)) || period === null
          || location?.status === "ambiguous" || location?.status === "uncertain" || location?.status === "out_of_coverage";
        const reason = location?.status === "ambiguous" ? "Hay más de una posición posible."
          : location?.status === "uncertain" ? "No se ha identificado con seguridad la parte del partido."
            : location?.status === "out_of_coverage" ? "El minuto queda fuera de la cobertura OCR verificada."
              : periodStart ? null
                : item.minute === null ? "Este comentario no tiene minuto."
                : item.minute < 1 ? "Este comentario no tiene un minuto futbolístico válido."
                : !period ? "No se reconoce el periodo de este comentario." : null;
        const actionName = nonlocalizable ? `Vídeo no localizable: ${reason ?? "sin posición única"}`
          : location?.status === "loading" ? "Buscando la posición aproximada en el vídeo"
            : location?.status === "error" ? `Reintentar búsqueda de vídeo para el minuto ${item.minute}`
              : periodStart ? `Localizar el inicio de la ${period === "first" ? "primera" : "segunda"} parte`
                : `Saltar al minuto ${item.minute} de la ${period === "first" ? "primera" : "segunda"} parte; reproducción desde el reloj ${footballClockAtMinuteTail(item.minute!)}`;
        const isPlaybackActive = hasVideo && activePlaybackEventKey === key;
        const isSelected = hasVideo && (playback.ready ? isPlaybackActive : selectedEventKey === scopedKey);
        const expanded = expandedEventKeys.has(key);
        const canExpand = hasVideo && item.text.length > 130;
        return <li key={key} ref={(element) => {
          if (element) eventCardRefs.current.set(key, element);
          else eventCardRefs.current.delete(key);
        }} className={`${css.item} ${isSelected ? css.selected : ""} ${isPlaybackActive ? css.activeEvent : ""}`}
          aria-current={isPlaybackActive ? "true" : undefined}>
          <div className={css.when}>
            <strong>{item.minute === null ? "Sin minuto" : `${item.minute}′`}</strong>
          </div>
          <div className={css.content}>
            <span className={css.kind}>{eventLabel(item)}</span>
            <p className={`${css.text} ${canExpand && !expanded ? css.textClamped : ""}`}><TextWithPlayers item={item} mentions={mentions}
              seasonId={seasonId} profileOrigin={profileOrigin} /></p>
            {canExpand ? <button type="button" className={css.expandText} aria-expanded={expanded}
              onClick={() => setExpandedEventKeys((current) => {
                const next = new Set(current);
                if (next.has(key)) next.delete(key); else next.add(key);
                return next;
              })}>{expanded ? "Mostrar menos" : "Ver comentario completo"}</button> : null}
            {item.score && (item.score.home !== null || item.score.away !== null) ? <p className={css.score}
              aria-label={`Marcador: ${item.score.home ?? "no disponible"} a ${item.score.away ?? "no disponible"}`}>
              Marcador <strong>{item.score.home ?? "—"}–{item.score.away ?? "—"}</strong>
            </p> : null}
          </div>
          {hasVideo ? <div className={css.videoAction}>
            <button type="button" className={css.videoButton} aria-label={actionName}
              title={reason ?? (location?.status === "error" ? location.message
                : location?.status === "resolved" && !periodStart
                  ? `Reproducción desde ${footballClockAtMinuteTail(item.minute!)} del reloj futbolístico`
                  : "Consultar estado de localización del evento")}
              disabled={nonlocalizable || location?.status === "loading"}
              onClick={() => void locateEvent(item, key)}>
              <Video size={17} aria-hidden="true" />
            </button>
          </div> : null}
        </li>;
      })}
        </ol>
      </>}
    </>}
    {addVideoOpen && <div className={css.dialogBackdrop}>
      <section className={css.dialog} role="dialog" aria-modal="true" aria-labelledby="add-video-title">
        <div className={css.dialogHeading}>
          <h2 id="add-video-title">Añadir vídeo de YouTube</h2>
          <button type="button" className={css.dialogClose} aria-label="Cerrar" disabled={submittingVideo}
            onClick={() => { setAddVideoOpen(false); setOperationError(null); }}><X size={20} aria-hidden="true" /></button>
        </div>
        <form onSubmit={(event) => void addVideo(event)}>
          <label className={css.dialogField} htmlFor="youtube-video-url">URL de YouTube
            <input id="youtube-video-url" name="youtube-url" type="url" autoComplete="url" autoFocus required
              maxLength={2048} placeholder="https://www.youtube.com/watch?v=…" value={youtubeUrl}
              disabled={submittingVideo} onChange={(event) => setYoutubeUrl(event.target.value)} />
          </label>
          <p className={css.dialogHelp}>Se descargará temporalmente la versión actual para generar una nueva sincronización. El archivo temporal se eliminará al terminar.</p>
          {operationError && <p className={css.dialogError} role="alert">{operationError}</p>}
          <div className={css.dialogActions}>
            <Button size="sm" variant="ghost" disabled={submittingVideo} onClick={() => {
              setAddVideoOpen(false);
              setOperationError(null);
            }}>Cancelar</Button>
            <Button size="sm" variant="primary" type="submit" disabled={submittingVideo || videoOperationBusy
              || !youtubeUrl.trim()}>{submittingVideo ? "Iniciando…" : "Añadir y sincronizar"}</Button>
          </div>
        </form>
      </section>
    </div>}
    {deleteVideoOpen && <div className={css.dialogBackdrop}>
      <section className={css.dialog} role="dialog" aria-modal="true" aria-labelledby="delete-video-title">
        <div className={css.dialogHeading}>
          <h2 id="delete-video-title">Eliminar vídeo de referencia</h2>
          <button type="button" className={css.dialogClose} aria-label="Cerrar" disabled={deletingVideo}
            onClick={() => { setDeleteVideoOpen(false); setOperationError(null); }}><X size={20} aria-hidden="true" /></button>
        </div>
        <p>Se desvinculará el vídeo de este partido y se desactivará su calibración vigente. El vídeo original de YouTube y el informe OCR histórico se conservarán. Volverán los comentarios verticales y podrás añadir este u otro vídeo más adelante.</p>
        {operationError && <p className={css.dialogError} role="alert">{operationError}</p>}
        <div className={css.dialogActions}>
          <Button size="sm" variant="ghost" disabled={deletingVideo} onClick={() => {
            setDeleteVideoOpen(false);
            setOperationError(null);
          }}>Cancelar</Button>
          <Button size="sm" variant="primary" disabled={deletingVideo || videoOperationBusy} onClick={() => void removeVideo()}>
            {deletingVideo ? "Eliminando…" : "Eliminar referencia"}
          </Button>
        </div>
      </section>
    </div>}
  </section>;
}

function periodFor(periodName: string | null): "first" | "second" | null {
  if (periodName === "1ST") return "first";
  if (periodName === "2ND") return "second";
  return null;
}

function minuteForVideo(minute: number, period: "first" | "second"): string {
  // SofaScore stores first-half stoppage time as 46′, 47′, etc.; V5.1 expects 45+N.
  return period === "first" && minute > 45 ? `45+${minute - 45}` : String(minute);
}

function footballClockAtMinuteTail(minute: number): string {
  const clockSecond = Math.max(0, (minute - 1) * 60 + 40);
  const minutes = Math.floor(clockSecond / 60);
  const seconds = clockSecond % 60;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}
