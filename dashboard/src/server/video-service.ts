import type { MatchVideoReference, VideoEventLocation, VideoJob, VideoJobStatus } from "@/contract";
import type { VideoSportsEventsV1 } from "./video-sports-events";

const REQUEST_TIMEOUT_MS = 5000;
const STATE_CHANGE_TIMEOUT_MS = 12000;
const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const SYNC_STATES = new Set(["calibrated", "uncertain", "not_calibrated"] as const);

export class VideoServiceUnavailableError extends Error {
  constructor() {
    super("El servicio de vídeo no responde o ha enviado una respuesta no válida.");
    this.name = "VideoServiceUnavailableError";
  }
}

export class MatchVideoNotFoundError extends Error {
  constructor() {
    super("El partido no tiene un vídeo de referencia disponible.");
    this.name = "MatchVideoNotFoundError";
  }
}

export class VideoServiceRequestError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "VideoServiceRequestError";
  }
}

type CatalogVideo = {
  video_id: string;
  origin: string | null;
  source_url: string | null;
  synchronization_status: "calibrated" | "uncertain" | "not_calibrated";
  match_ref: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function baseUrl(): string {
  const configured = process.env.VIDEO_SERVICE_URL?.trim();
  if (!configured) throw new VideoServiceUnavailableError();
  try {
    const parsed = new URL(configured);
    if (!(["http:", "https:"].includes(parsed.protocol)) || parsed.username || parsed.password) {
      throw new Error("invalid service URL");
    }
    return parsed.toString().replace(/\/+$/, "");
  } catch {
    throw new VideoServiceUnavailableError();
  }
}

async function getJson(path: string): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new VideoServiceUnavailableError();
  }
  if (!response.ok) throw new VideoServiceUnavailableError();
  try {
    return await response.json();
  } catch {
    throw new VideoServiceUnavailableError();
  }
}

async function sendJson(path: string, method: "POST" | "DELETE", body?: unknown): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(`${baseUrl()}${path}`, {
      method,
      headers: { Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(STATE_CHANGE_TIMEOUT_MS),
    });
  } catch {
    throw new VideoServiceUnavailableError();
  }
  if (!response.ok) {
    let message = "No se pudo completar la operación de vídeo.";
    try {
      const payload: unknown = await response.json();
      if (isRecord(payload) && typeof payload.detail === "string" && payload.detail.length <= 240
          && !/[\\/](?:home|app|tmp|state)[\\/]|Traceback/i.test(payload.detail)) message = payload.detail;
    } catch { /* use the safe generic message */ }
    throw new VideoServiceRequestError(response.status, message);
  }
  try {
    return await response.json();
  } catch {
    throw new VideoServiceUnavailableError();
  }
}

function youtubeVideoId(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    let id: string | null = null;
    if (["youtu.be", "www.youtu.be"].includes(host)) id = url.pathname.split("/").filter(Boolean)[0] ?? null;
    else if (["youtube.com", "www.youtube.com"].includes(host) && url.pathname === "/watch") id = url.searchParams.get("v");
    else if (["youtube.com", "www.youtube.com", "www.youtube-nocookie.com"].includes(host)) {
      const match = /^\/embed\/([^/]+)$/.exec(url.pathname);
      id = match?.[1] ?? null;
    }
    return id && VIDEO_ID.test(id) ? id : null;
  } catch {
    return null;
  }
}

async function catalogVideo(sourceEventId: string): Promise<CatalogVideo | null> {
  const body = await getJson("/api/v1/videos");
  if (!isRecord(body) || !Array.isArray(body.items)) throw new VideoServiceUnavailableError();
  const matches = body.items.filter((item): item is Record<string, unknown> => {
    if (!isRecord(item)) return false;
    if (item.match_ref === sourceEventId) return true;
    return isRecord(item.match) && item.match.provider === "sofascore"
      && (typeof item.match.event_id === "string" || typeof item.match.event_id === "number")
      && String(item.match.event_id) === sourceEventId;
  });
  if (matches.length > 1) throw new VideoServiceUnavailableError();
  const match = matches[0];
  if (!match) return null;
  if (typeof match.video_id !== "string" || !/^[a-z0-9][a-z0-9_-]{0,79}$/.test(match.video_id)
      || !SYNC_STATES.has(match.synchronization_status as CatalogVideo["synchronization_status"])) {
    throw new VideoServiceUnavailableError();
  }
  return {
    video_id: match.video_id,
    origin: typeof match.origin === "string" ? match.origin : null,
    source_url: typeof match.source_url === "string" ? match.source_url : null,
    synchronization_status: match.synchronization_status as CatalogVideo["synchronization_status"],
    match_ref: sourceEventId,
  };
}

const JOB_STATUSES = new Set<VideoJobStatus>([
  "pending", "downloading", "calibrating", "saving", "completed", "failed",
]);

function publicJob(value: unknown, expectedMatchId: string): VideoJob {
  if (!isRecord(value) || typeof value.job_id !== "string" || typeof value.status !== "string"
      || !JOB_STATUSES.has(value.status as VideoJobStatus) || typeof value.message !== "string"
      || typeof value.youtube_video_id !== "string" || !VIDEO_ID.test(value.youtube_video_id)
      || typeof value.created_at !== "string" || typeof value.updated_at !== "string"
      || (value.match_ref !== expectedMatchId
        && (!isRecord(value.match) || value.match.provider !== "sofascore"
          || String(value.match.event_id) !== expectedMatchId))
      || !(value.error_code === null || typeof value.error_code === "string")) {
    throw new VideoServiceUnavailableError();
  }
  return {
    jobId: value.job_id,
    match: { provider: "sofascore", eventId: expectedMatchId },
    youtubeVideoId: value.youtube_video_id,
    status: value.status as VideoJobStatus,
    message: value.message,
    errorCode: value.error_code,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  };
}

export async function startMatchVideoJob(
  sourceEventId: string,
  sourceUrl: string,
  sportsEvents: VideoSportsEventsV1 | null,
): Promise<VideoJob> {
  const value = await sendJson("/api/v1/video-jobs", "POST", {
    match_ref: sourceEventId,
    youtube_url: sourceUrl,
    sports_events: sportsEvents,
  });
  return publicJob(value, sourceEventId);
}

export async function getMatchVideoJob(sourceEventId: string, jobId: string): Promise<VideoJob> {
  const value = await getJson(`/api/v1/video-jobs/${encodeURIComponent(jobId)}`);
  return publicJob(value, sourceEventId);
}

export async function getCurrentMatchVideoJob(sourceEventId: string): Promise<VideoJob | null> {
  const value = await getJson(`/api/v1/matches/${encodeURIComponent(sourceEventId)}/video-job`);
  return value === null ? null : publicJob(value, sourceEventId);
}

export async function deleteMatchVideoReference(sourceEventId: string): Promise<void> {
  const value = await sendJson(`/api/v1/matches/${encodeURIComponent(sourceEventId)}/video`, "DELETE");
  if (!isRecord(value) || value.available !== false
      || (value.match_ref !== sourceEventId && (!isRecord(value.match)
        || value.match.provider !== "sofascore" || String(value.match.event_id) !== sourceEventId))) {
    throw new VideoServiceUnavailableError();
  }
}

function publicReference(record: CatalogVideo | null): MatchVideoReference {
  if (!record || record.origin !== "youtube") return { available: false };
  const id = youtubeVideoId(record.source_url);
  if (!id) return { available: false };
  return { available: true, youtubeVideoId: id, synchronizationStatus: record.synchronization_status };
}

export async function getMatchVideoReference(sourceEventId: string): Promise<MatchVideoReference> {
  return publicReference(await catalogVideo(sourceEventId));
}

export async function locateMatchVideoEvent(
  sourceEventId: string,
  period: "first" | "second",
  minute: string | null,
  eventKind: "other" | "match_start" | "period_start" = "other",
  eventSecond?: number,
): Promise<VideoEventLocation> {
  const record = await catalogVideo(sourceEventId);
  if (!record || !publicReference(record).available) throw new MatchVideoNotFoundError();
  const query = new URLSearchParams({ period, lead_seconds: "0", event_kind: eventKind });
  if (minute !== null) query.set("minute", minute);
  if (eventSecond !== undefined) query.set("event_second", String(eventSecond));
  const body = await getJson(`/api/v1/videos/${encodeURIComponent(record.video_id)}/locate?${query}`);
  if (!isRecord(body) || typeof body.status !== "string") throw new VideoServiceUnavailableError();
  if (body.status === "resolved") {
    const result = body.result;
    const methods = ["event_second_interpolation", "minute_tail", "boundary_extrapolation"];
    if (!isRecord(result) || typeof result.video_second !== "number" || !Number.isFinite(result.video_second)
        || result.video_second < 0) throw new VideoServiceUnavailableError();
    if (typeof result.playback_video_second !== "number" || !Number.isFinite(result.playback_video_second)
        || typeof result.event_video_second !== "number" || !Number.isFinite(result.event_video_second)
        || typeof result.event_clock_second_start !== "number" || !Number.isFinite(result.event_clock_second_start)
        || typeof result.event_clock_second_estimate !== "number" || !Number.isFinite(result.event_clock_second_estimate)
        || typeof result.lead_seconds !== "number" || typeof result.estimated !== "boolean"
        || typeof result.method !== "string" || !methods.includes(result.method)
        || !isRecord(result.confidence) || !Array.isArray(result.warnings)) {
      throw new VideoServiceUnavailableError();
    }
    return {
      status: "resolved", videoSecond: result.playback_video_second,
      eventVideoSecond: result.event_video_second, eventClockSecond: result.event_clock_second_start,
      eventClockSecondEstimate: result.event_clock_second_estimate, leadSeconds: result.lead_seconds,
      estimated: result.estimated,
      method: result.method as "event_second_interpolation" | "minute_tail" | "boundary_extrapolation",
      confidence: {
        ocr: typeof result.confidence.ocr === "string" ? result.confidence.ocr : null,
        period: typeof result.confidence.period === "string" ? result.confidence.period : null,
        location: typeof result.confidence.location === "string" ? result.confidence.location : null,
      },
      warnings: result.warnings.filter((warning): warning is string => typeof warning === "string"),
    };
  }
  if (["ambiguous", "uncertain", "out_of_coverage"].includes(body.status)) {
    return { status: body.status as "ambiguous" | "uncertain" | "out_of_coverage" };
  }
  throw new VideoServiceUnavailableError();
}
