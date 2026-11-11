"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { cancelUpdate, getUpdate, getUpdateStatus, startUpdate } from "@/client/api";
import type { UpdateJob, UpdateStatus } from "@/contract";
import { failureReason } from "@/hooks/use-api";

const KEY_STORAGE = "rj-update-idempotency-key";
/** Told to every control on the page (top bar and /operador) when a job changes. */
const CHANGED = "rj-updates-changed";
/** `job`: the job the action just obtained, shown at once by every control without waiting for the status. */
const announce = (job?: UpdateJob) => window.dispatchEvent(new CustomEvent<UpdateJob | undefined>(CHANGED, { detail: job }));

/** The key of the pending start: kept until a job is obtained, so a retried or repeated click reuses it. */
function pendingKey(): string {
  try {
    const stored = sessionStorage.getItem(KEY_STORAGE);
    if (stored) return stored;
    const created = crypto.randomUUID();
    sessionStorage.setItem(KEY_STORAGE, created);
    return created;
  } catch {
    return crypto.randomUUID();
  }
}

function forgetKey() {
  try {
    sessionStorage.removeItem(KEY_STORAGE);
  } catch {
    // Without storage the next start simply uses a new key.
  }
}

export const isActive = (job: UpdateJob | null) => job !== null && (job.status === "queued" || job.status === "running");

/**
 * Update status, the job being followed, and its actions (any session). Polls only while a job is active, at the
 * interval the server asks for; a start or a cancel already in flight ignores further clicks.
 */
export function useUpdates() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [job, setJob] = useState<UpdateJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [publishedNow, setPublishedNow] = useState(false);
  const inFlight = useRef(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const next = await getUpdateStatus({ signal });
      setStatus(next);
      setJob(next.activeJob ?? next.lastJob ?? null);
    } catch (error) {
      if (!signal?.aborted) setProblem(failureReason(error));
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let latest = 0;
    const refresh = (event?: Event) => {
      const announced = (event as CustomEvent<UpdateJob | undefined> | undefined)?.detail;
      if (announced) setJob(announced);
      const request = ++latest;  // an older status answer never overwrites a newer one
      return getUpdateStatus({ signal: controller.signal })
        .then((next) => {
          if (request !== latest) return;
          setStatus(next);
          setJob(next.activeJob ?? next.lastJob ?? null);
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) setProblem(failureReason(error));
        });
    };
    void refresh();
    window.addEventListener(CHANGED, refresh);
    return () => {
      window.removeEventListener(CHANGED, refresh);
      controller.abort();
    };
  }, []);

  const jobId = job?.id;
  const active = isActive(job);
  const pollAfter = job?.pollAfterSeconds ?? 3;
  useEffect(() => {
    if (!active || !jobId) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      getUpdate(jobId, { signal: controller.signal })
        .then((next) => {
          setJob(next);
          if (!isActive(next)) {
            if (next.published) setPublishedNow(true);
            void load();
          }
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) setProblem(failureReason(error));
        });
    }, pollAfter * 1000);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [active, jobId, pollAfter, job, load]);

  const run = useCallback(async (action: () => Promise<void>) => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await action();
    } catch (error) {
      setProblem(failureReason(error));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }, []);

  return {
    status,
    job,
    busy,
    problem,
    publishedNow,
    start: () =>
      run(async () => {
        const started = await startUpdate(pendingKey());
        forgetKey();
        setPublishedNow(false);
        setJob(started.job);
        announce(started.job);
      }),
    cancel: () =>
      run(async () => {
        if (!job) return;
        const cancelled = await cancelUpdate(job.id);
        setJob(cancelled);
        announce(cancelled);
      }),
    reload: () => window.location.reload(),
  };
}
