import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { GET as getVideo } from "@/app/api/v1/matches/[matchId]/video/route";
import { GET as locateVideo } from "@/app/api/v1/matches/[matchId]/video/locate/route";
import { POST as startVideoJob, GET as getCurrentJob } from "@/app/api/v1/matches/[matchId]/video/jobs/route";
import { GET as getVideoJob } from "@/app/api/v1/matches/[matchId]/video/jobs/[jobId]/route";
import { DELETE as deleteVideo } from "@/app/api/v1/matches/[matchId]/video/route";
import { POST as signIn } from "@/app/api/v1/session/route";
import { UpstreamUnavailableError } from "@/server/local-api/local-api-source";
import { InvalidLocalApiResponseError } from "@/server/local-api/validation";

const { getDataSourceMock } = vi.hoisted(() => ({ getDataSourceMock: vi.fn() }));
vi.mock("@/server/data-source", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/server/data-source")>();
  return { ...original, getDataSource: getDataSourceMock };
});

const catalog = { items: [
  { video_id: "real-jaen-rayo-majadahonda", origin: "youtube", source_url: "https://youtu.be/79Rngk2hcC0",
    synchronization_status: "calibrated", match_ref: "16599928" },
  { video_id: "atletico-madrileno-real-jaen", origin: null, source_url: null,
    synchronization_status: "calibrated", match_ref: "16599973" },
] };

const commentsDocument = {
  matchId: "sofascore-match-16599928",
  availability: "available" as const,
  capturedAt: "2026-11-10T12:00:00Z",
  responseSha256: "a".repeat(64),
  lastAttempt: { availability: "available" as const, occurredAt: "2026-11-10T12:00:00Z",
    httpStatus: 200, errorCode: null },
  comments: [
    { id: "start-first", position: 2, text: "Inicio primera parte.", type: "matchStarted", minute: 1,
      periodName: "1ST", isHome: null, score: null, incidentId: null, participants: {} },
    { id: "end-first", position: 1, text: "Final primera parte.", type: "endFirstHalf", minute: null,
      periodName: null, isHome: null, score: null, incidentId: null, participants: {} },
    { id: "start-second", position: 0, text: "Inicio segunda parte.", type: "matchStarted", minute: 46,
      periodName: "2ND", isHome: null, score: null, incidentId: null, participants: {} },
  ],
};

const context = { params: Promise.resolve({ matchId: "sofascore-match-16599928" }) };
const ORIGIN = "http://localhost";
const PASSWORD = "video-service-test-password";
const SESSION_SECRET = "video-service-test-secret-0123456789abcdef";
let folder: string;
const request = (path: string, options: { method?: string; cookie?: string; origin?: string; body?: unknown } = {}) =>
  new Request(`${ORIGIN}/api/v1${path}`, {
    method: options.method ?? "GET",
    headers: { host: "localhost", ...(options.origin ? { origin: options.origin } : {}),
      ...(options.cookie ? { cookie: options.cookie } : {}),
      ...(options.body === undefined ? {} : { "content-type": "application/json" }) },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
const jsonResponse = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json" },
});

beforeEach(() => {
  folder = mkdtempSync(join(tmpdir(), "rj-video-bff-"));
  writeFileSync(join(folder, "password"), PASSWORD);
  writeFileSync(join(folder, "session"), SESSION_SECRET);
  vi.stubEnv("DATA_MODE", "real");
  vi.stubEnv("RJ_ACCESS_PASSWORD_FILE", join(folder, "password"));
  vi.stubEnv("RJ_SESSION_SECRET_FILE", join(folder, "session"));
  vi.stubEnv("VIDEO_SERVICE_URL", "http://video-service:8010");
  vi.stubGlobal("fetch", vi.fn());
  getDataSourceMock.mockClear();
  const matchComments = vi.fn().mockResolvedValue(commentsDocument);
  getDataSourceMock.mockReturnValue({ matchComments });
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  rmSync(folder, { recursive: true, force: true });
});

async function sessionCookie(): Promise<string> {
  const response = await signIn(request("/session", { method: "POST", body: { password: PASSWORD }, origin: ORIGIN }));
  expect(response.status).toBe(204);
  return response.headers.get("set-cookie")!.split(";")[0];
}

describe("Video Service BFF routes", () => {
  it("matches the exact SofaScore event ID and returns only a playable YouTube reference", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(catalog));
    const response = await getVideo(request("/matches/sofascore-match-16599928/video"), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ available: true, youtubeVideoId: "79Rngk2hcC0", synchronizationStatus: "calibrated" });
    expect(fetch).toHaveBeenCalledWith("http://video-service:8010/api/v1/videos", expect.objectContaining({ cache: "no-store" }));
  });

  it("does not infer a video association for a different match ID", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(catalog));
    const response = await getVideo(request("/matches/sofascore-match-16599972/video"), {
      params: Promise.resolve({ matchId: "sofascore-match-16599972" }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ available: false });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not expose a calibrated record without a playable source URL", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(catalog));
    const response = await getVideo(request("/matches/sofascore-match-16599973/video"), {
      params: Promise.resolve({ matchId: "sofascore-match-16599973" }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ available: false });
  });

  it("passes the period and minute with no extra playback lead", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(catalog))
      .mockResolvedValueOnce(jsonResponse({ status: "resolved", result: {
        video_second: 6660, playback_video_second: 6660, event_video_second: 6660,
        event_clock_second_start: 5640, event_clock_second_estimate: 5680, lead_seconds: 0,
        estimated: true, method: "minute_tail", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [],
      } }));
    const response = await locateVideo(request("/matches/sofascore-match-16599928/video/locate?period=second&minute=95"), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "resolved", videoSecond: 6660, eventVideoSecond: 6660,
      eventClockSecond: 5640, eventClockSecondEstimate: 5680, leadSeconds: 0, estimated: true,
      method: "minute_tail", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [] });
    const secondCall = vi.mocked(fetch).mock.calls[1];
    expect(String(secondCall?.[0])).toContain("/api/v1/videos/real-jaen-rayo-majadahonda/locate?");
    const query = new URL(String(secondCall?.[0])).searchParams;
    expect(Object.fromEntries(query)).toEqual({ period: "second", minute: "95", lead_seconds: "0", event_kind: "other" });
  });

  it("preserves plus notation when forwarding first-half stoppage time", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(catalog))
      .mockResolvedValueOnce(jsonResponse({ status: "resolved", result: {
        video_second: 4800, playback_video_second: 4800, event_video_second: 4800,
        event_clock_second_start: 2760, event_clock_second_estimate: 2800, lead_seconds: 0,
        estimated: true, method: "minute_tail", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [],
      } }));
    const response = await locateVideo(request("/matches/sofascore-match-16599928/video/locate?period=first&minute=45%2B2"), context);
    expect(response.status).toBe(200);
    expect((await response.json() as { videoSecond: number }).videoSecond).toBe(4800);
    const secondCall = vi.mocked(fetch).mock.calls[1];
    expect(new URL(String(secondCall?.[0])).searchParams.get("minute")).toBe("45+2");
  });

  it("forwards a first-half start without fabricating a minute timestamp", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(catalog))
      .mockResolvedValueOnce(jsonResponse({ status: "resolved", result: {
        video_second: 2032.032, playback_video_second: 2032.032, event_video_second: 2032.032,
        event_clock_second_start: 0, event_clock_second_estimate: 0, lead_seconds: 0,
        estimated: true, method: "boundary_extrapolation", confidence: { ocr: "high", period: "high", location: "high" }, warnings: [],
      } }));
    const response = await locateVideo(request("/matches/sofascore-match-16599928/video/locate?period=first&event_kind=match_start"), context);
    expect(response.status).toBe(200);
    const query = new URL(String(vi.mocked(fetch).mock.calls[1]?.[0])).searchParams;
    expect(Object.fromEntries(query)).toEqual({ period: "first", lead_seconds: "0", event_kind: "match_start" });
  });

  it("does not expose a single position for ambiguous service responses", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(jsonResponse(catalog))
      .mockResolvedValueOnce(jsonResponse({ status: "ambiguous", candidates: [{ video_second: 6000 }, { video_second: 6100 }] }));
    const response = await locateVideo(request("/matches/sofascore-match-16599928/video/locate?period=second&minute=58"), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ambiguous" });
  });

  it("validates query parameters before contacting Video Service", async () => {
    const response = await locateVideo(request("/matches/sofascore-match-16599928/video/locate?period=extra&minute=58"), context);
    expect(response.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reports an unavailable upstream without exposing its internal URL", async () => {
    vi.mocked(fetch).mockRejectedValue(new Error("internal network detail"));
    const response = await getVideo(request("/matches/sofascore-match-16599928/video"), context);
    expect(response.status).toBe(503);
    const body = await response.json();
    expect(body).toEqual({ error: { code: "UPSTREAM_UNAVAILABLE", message: "No se pudo consultar el servicio de vídeo." } });
    expect(JSON.stringify(body)).not.toContain("video-service:8010");
  });

  it("resolves the route match and forwards its neutral sports-events document to Video Service", async () => {
    const cookie = await sessionCookie();
    vi.mocked(fetch).mockResolvedValue(jsonResponse({
      job_id: "11111111-1111-4111-8111-111111111111", status: "pending", message: "Trabajo pendiente.",
      error_code: null, created_at: "2026-11-11T12:00:00Z", updated_at: "2026-11-11T12:00:00Z",
      youtube_video_id: "79Rngk2hcC0", match_ref: "16599928",
    }, 202));
    const response = await startVideoJob(request("/matches/sofascore-match-16599928/video/jobs", {
      method: "POST", cookie, origin: ORIGIN, body: { source_url: "https://www.youtube.com/watch?v=79Rngk2hcC0" },
    }), context);
    expect(response.status).toBe(202);
    expect(await response.json()).toMatchObject({ match: { eventId: "16599928" }, youtubeVideoId: "79Rngk2hcC0" });
    const dataSource = getDataSourceMock.mock.results.at(-1)?.value;
    expect(dataSource.matchComments).toHaveBeenCalledWith("sofascore-match-16599928");
    const [, options] = vi.mocked(fetch).mock.calls[0]!;
    expect(String(options?.body)).toContain('"match_ref":"16599928"');
    const requestBody = JSON.parse(String(options?.body));
    expect(requestBody.match_ref).toBe("16599928");
    expect(requestBody.youtube_url).toBe("https://www.youtube.com/watch?v=79Rngk2hcC0");
    expect(requestBody.sports_events.contract).toBe("video-sync.sports-events");
    expect(requestBody.sports_events.version).toBe(1);
    expect(requestBody.sports_events.events.map((event: { original_type: string; normalized_type: string; period: string | null; source_minute: number | null; game_clock_seconds: number | null }) => [
      event.original_type, event.normalized_type, event.period, event.source_minute, event.game_clock_seconds,
    ])).toEqual([
      ["matchStarted", "match_start", "first", 1, null],
      ["endFirstHalf", "period_end", "first", null, null],
      ["matchStarted", "period_start", "second", 46, null],
    ]);
    expect(requestBody.sports_events.events.map((event: { source_order: number }) => event.source_order)).toEqual([0, 1, 2]);
  });

  it("requires a session and rejects extra match identity fields before contacting Video Service", async () => {
    const anonymous = await startVideoJob(request("/matches/sofascore-match-16599928/video/jobs", {
      method: "POST", origin: ORIGIN, body: { source_url: "https://youtu.be/79Rngk2hcC0" },
    }), context);
    expect(anonymous.status).toBe(401);
    const cookie = await sessionCookie();
    const refused = await startVideoJob(request("/matches/sofascore-match-16599928/video/jobs", {
      method: "POST", cookie, origin: ORIGIN,
      body: { source_url: "https://youtu.be/79Rngk2hcC0", sofascore_event_id: "16599973" },
    }), context);
    expect(refused.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("allows an existing match without acquired comments and sends no narrative evidence", async () => {
    const matchComments = vi.fn().mockResolvedValue({
      ...commentsDocument, availability: "not_collected", comments: [],
    });
    getDataSourceMock.mockReturnValue({ matchComments });
    const cookie = await sessionCookie();
    vi.mocked(fetch).mockResolvedValue(jsonResponse({
      job_id: "11111111-1111-4111-8111-111111111111", status: "pending", message: "Trabajo pendiente.",
      error_code: null, created_at: "2026-11-11T12:00:00Z", updated_at: "2026-11-11T12:00:00Z",
      youtube_video_id: "79Rngk2hcC0", match_ref: "16599928",
    }, 202));
    const response = await startVideoJob(request("/matches/sofascore-match-16599928/video/jobs", {
      method: "POST", cookie, origin: ORIGIN, body: { source_url: "https://youtu.be/79Rngk2hcC0" },
    }), context);
    expect(response.status).toBe(202);
    expect(matchComments).toHaveBeenCalledWith("sofascore-match-16599928");
    const body = JSON.parse(String(vi.mocked(fetch).mock.calls[0]?.[1]?.body));
    expect(body.sports_events).toBeNull();
  });

  it("distinguishes an unknown match, a temporary source failure and an invalid source response", async () => {
    const cookie = await sessionCookie();
    const requestForCurrentMatch = () => request("/matches/sofascore-match-16599928/video/jobs", {
      method: "POST", cookie, origin: ORIGIN, body: { source_url: "https://youtu.be/79Rngk2hcC0" },
    });

    getDataSourceMock.mockReturnValue({ matchComments: vi.fn().mockResolvedValue(null) });
    const missing = await startVideoJob(requestForCurrentMatch(), context);
    expect(missing.status).toBe(404);
    expect((await missing.json()).error.message).toContain("no existe");
    expect(fetch).not.toHaveBeenCalled();

    getDataSourceMock.mockReturnValue({ matchComments: vi.fn().mockRejectedValue(new UpstreamUnavailableError("timeout")) });
    const unavailable = await startVideoJob(requestForCurrentMatch(), context);
    expect(unavailable.status).toBe(503);
    expect((await unavailable.json()).error.message).toContain("temporalmente");
    expect(fetch).not.toHaveBeenCalled();

    getDataSourceMock.mockReturnValue({ matchComments: vi.fn().mockRejectedValue(new InvalidLocalApiResponseError("comments")) });
    const invalid = await startVideoJob(requestForCurrentMatch(), context);
    expect(invalid.status).toBe(502);
    expect((await invalid.json()).error.message).toContain("respuesta no válida");
    expect(fetch).not.toHaveBeenCalled();
  });

  it("rejects YouTube URLs at the BFF boundary before requesting sports data", async () => {
    const cookie = await sessionCookie();
    const response = await startVideoJob(request("/matches/sofascore-match-16599928/video/jobs", {
      method: "POST", cookie, origin: ORIGIN, body: { source_url: "https://youtube.com.evil.invalid/watch?v=79Rngk2hcC0" },
    }), context);
    expect(response.status).toBe(400);
    expect(getDataSourceMock).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("gets job status only when the returned event identity matches the current route", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse({
      job_id: "11111111-1111-4111-8111-111111111111", status: "downloading", message: "Descargando.",
      error_code: null, created_at: "2026-11-11T12:00:00Z", updated_at: "2026-11-11T12:00:01Z",
      youtube_video_id: "79Rngk2hcC0", match_ref: "16599928",
    }));
    const response = await getVideoJob(request("/matches/sofascore-match-16599928/video/jobs/11111111-1111-4111-8111-111111111111"), {
      params: Promise.resolve({ matchId: "sofascore-match-16599928", jobId: "11111111-1111-4111-8111-111111111111" }),
    });
    expect(response.status).toBe(200);
    expect((await response.json()).match.eventId).toBe("16599928");
    vi.mocked(fetch).mockResolvedValue(jsonResponse({
      job_id: "11111111-1111-4111-8111-111111111111", status: "downloading", message: "Descargando.",
      error_code: null, created_at: "2026-11-11T12:00:00Z", updated_at: "2026-11-11T12:00:01Z",
      youtube_video_id: "79Rngk2hcC0", match_ref: "16599973",
    }));
    const mismatch = await getVideoJob(request("/matches/sofascore-match-16599928/video/jobs/11111111-1111-4111-8111-111111111111"), {
      params: Promise.resolve({ matchId: "sofascore-match-16599928", jobId: "11111111-1111-4111-8111-111111111111" }),
    });
    expect(mismatch.status).toBe(503);
    vi.mocked(fetch).mockResolvedValue(jsonResponse(null));
    const current = await getCurrentJob(request("/matches/sofascore-match-16599928/video/jobs"), context);
    expect(current.status).toBe(200);
    expect(await current.json()).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("requires confirmation authorization before persistent video deactivation", async () => {
    const anonymous = await deleteVideo(request("/matches/sofascore-match-16599928/video", { method: "DELETE", origin: ORIGIN }), context);
    expect(anonymous.status).toBe(401);
    const cookie = await sessionCookie();
    vi.mocked(fetch).mockResolvedValue(jsonResponse({ available: false, match_ref: "16599928" }));
    const response = await deleteVideo(request("/matches/sofascore-match-16599928/video", {
      method: "DELETE", cookie, origin: ORIGIN,
    }), context);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ available: false });
    expect(fetch).toHaveBeenCalledWith("http://video-service:8010/api/v1/matches/16599928/video", expect.objectContaining({ method: "DELETE" }));
  });
});
