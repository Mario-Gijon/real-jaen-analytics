import type {
  UpdateCoverage,
  UpdateErrorCode,
  UpdateJob,
  UpdateJobStatus,
  UpdatePendingReason,
  UpdatePhase,
  UpdateResource,
  UpdateScope,
  UpdateStart,
  UpdateStatus,
} from "@/contract";
import { clearTeamNameCache, publicMatchId } from "../local-api/local-api-source";
import { controlSecret } from "../auth/session";

/**
 * Server-side client of the backend's update control (`/internal/v1/updates`, docs/contrato-actualizaciones.md).
 * Only this module sends the internal control secret, and only to `LOCAL_DATA_API_URL`. It returns contract types
 * and never forwards the backend's bodies or errors as they are.
 */

/** The backend did not answer, or answered something unusable. */
export class ControlUnavailableError extends Error {}
/** The backend refused the operation in the current state (409). */
export class ControlConflictError extends Error {
  constructor(readonly reason: string) {
    super(reason);
  }
}

type BackendJob = {
  id: string;
  status: UpdateJobStatus;
  phase: UpdatePhase;
  cancellable: boolean;
  cancel_requested: boolean;
  published: boolean;
  requested_at: string;
  finished_at: string | null;
  poll_after_seconds: number;
  error: { code: UpdateErrorCode; message: string; retryable: boolean } | null;
  result: { published_at: string; reused_bundle: boolean; coverage?: BackendCoverage | null } | null;
};
type BackendCoverage = {
  new: number;
  kept: number;
  pending: { event_id: number; resources: { resource: UpdateResource; reason: UpdatePendingReason; kept: boolean }[] }[];
  deferred: number[];
  restricted?: number[];
  detail_rounds?: number[] | null;
  new_matches: { id: number; replaces: number[] }[];
  names?: Record<string, string>;
  comments?: {
    new: number[];
    empty?: number[];
    kept: number[];
    pending: number[];
    failed: { id: number; reason: string }[];
  };
};
type BackendStatus = {
  scope: { kind: "pilot" | "group"; label: string; description: string; action: string; round: number | null; match_ids: number[] };
  active_job: BackendJob | null;
  last_job: BackendJob | null;
  last_published: { at: string; coverage?: BackendCoverage | null } | null;
};

const STATUSES: UpdateJobStatus[] = ["queued", "running", "completed", "failed", "cancelled", "interrupted"];
const PHASES: UpdatePhase[] = ["queued", "acquiring", "verifying", "publishing", "finished"];

function job(value: BackendJob): UpdateJob {
  if (!value || typeof value.id !== "string" || !STATUSES.includes(value.status) || !PHASES.includes(value.phase)) {
    throw new ControlUnavailableError("Respuesta de control no válida");
  }
  const result = value.result && {
    publishedAt: value.result.published_at,
    reusedBundle: value.result.reused_bundle,
    coverage: value.result.coverage ? coverage(value.result.coverage) : null,
  };
  if (result) forgetCachesAfter(result.publishedAt);
  return {
    id: value.id,
    status: value.status,
    phase: value.phase,
    cancellable: value.cancellable === true,
    cancelRequested: value.cancel_requested === true,
    published: value.published === true,
    requestedAt: value.requested_at,
    finishedAt: value.finished_at,
    pollAfterSeconds: Math.max(1, Math.min(30, Number(value.poll_after_seconds) || 3)),
    error: value.error && { code: value.error.code, message: value.error.message, retryable: value.error.retryable === true },
    result,
  };
}

/** A publication changes the data: forget what the adapter keeps in memory (team names) once per publication. */
let lastSeenPublication = "";
function forgetCachesAfter(publishedAt: string) {
  if (publishedAt > lastSeenPublication) {
    lastSeenPublication = publishedAt;
    clearTeamNameCache();
  }
}

function coverage(value: BackendCoverage): UpdateCoverage {
  const emptyComments = new Set(value.comments?.empty ?? []);
  return {
    newResources: value.new,
    keptResources: value.kept,
    pending: value.pending.map((p) => ({
      matchId: publicMatchId(p.event_id),
      resources: p.resources.map((r) => ({ resource: r.resource, reason: r.reason, kept: r.kept === true })),
    })),
    deferredMatchIds: value.deferred.map(publicMatchId),
    restrictedMatchIds: (value.restricted ?? []).map(publicMatchId),
    detailRounds: value.detail_rounds ?? null,
    newMatches: value.new_matches.map((m) => ({ matchId: publicMatchId(m.id), replaces: m.replaces.map(publicMatchId) })),
    matchNames: Object.fromEntries(Object.entries(value.names ?? {}).map(([id, name]) => [publicMatchId(Number(id)), name])),
    comments: {
      newMatchIds: (value.comments?.new ?? []).filter((id) => !emptyComments.has(id)).map(publicMatchId),
      emptyMatchIds: [...emptyComments].map(publicMatchId),
      keptMatchIds: (value.comments?.kept ?? []).map(publicMatchId),
      pendingMatchIds: (value.comments?.pending ?? []).map(publicMatchId),
      failed: (value.comments?.failed ?? []).map((item) => ({
        matchId: publicMatchId(item.id), reason: item.reason,
      })),
    },
  };
}

function scope(value: BackendStatus["scope"]): UpdateScope {
  if (value.kind !== "pilot" && value.kind !== "group") throw new ControlUnavailableError("Alcance desconocido");
  return {
    kind: value.kind,
    label: value.label,
    description: value.description,
    actionLabel: value.action,
    round: value.round,
    matchIds: value.match_ids.map(publicMatchId),
  };
}

async function call(method: "GET" | "POST", path: string, headers: Record<string, string> = {}): Promise<{ status: number; body: unknown }> {
  const base = process.env.LOCAL_DATA_API_URL;
  const secret = controlSecret();
  if (!base || !secret) throw new ControlUnavailableError("Control de actualizaciones no configurado");
  let response: Response;
  try {
    response = await fetch(`${base.replace(/\/$/, "")}/internal/v1/updates${path}`, {
      method,
      headers: { Accept: "application/json", Authorization: `Bearer ${secret.toString("ascii")}`, ...headers },
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(10_000),
    });
  } catch {
    throw new ControlUnavailableError("El backend no responde");
  }
  const body: unknown = await response.json().catch(() => null);
  if (response.status === 404) return { status: 404, body: null };
  if (response.status === 409) throw new ControlConflictError(String((body as { error?: string } | null)?.error ?? "conflict"));
  if (!response.ok) throw new ControlUnavailableError(`Control respondió ${response.status}`);
  return { status: response.status, body };
}

export async function controlStatus(): Promise<Omit<UpdateStatus, "available">> {
  const { body } = await call("GET", "/current");
  const value = body as BackendStatus;
  if (!value?.scope) throw new ControlUnavailableError("Respuesta de control no válida");
  return {
    scope: scope(value.scope),
    lastPublished: value.last_published ? { at: value.last_published.at } : null,
    activeJob: value.active_job ? job(value.active_job) : null,
    lastJob: value.last_job ? job(value.last_job) : null,
    lastCoverage: value.last_published?.coverage ? coverage(value.last_published.coverage) : null,
  };
}

export async function startUpdate(idempotencyKey: string): Promise<UpdateStart> {
  const { body } = await call("POST", "", { "Idempotency-Key": idempotencyKey });
  const value = body as { job: BackendJob; reused: boolean };
  return { job: job(value.job), reused: value.reused === true };
}

export async function updateJob(id: string): Promise<UpdateJob | null> {
  const { status, body } = await call("GET", `/${encodeURIComponent(id)}`);
  return status === 404 ? null : job(body as BackendJob);
}

export async function cancelUpdate(id: string): Promise<UpdateJob | null> {
  const { status, body } = await call("POST", `/${encodeURIComponent(id)}/cancel`);
  return status === 404 ? null : job(body as BackendJob);
}
