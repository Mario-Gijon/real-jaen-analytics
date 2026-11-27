"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { cancelUpdate, getUpdate, getUpdateStatus, startUpdate } from "@/client/api";
import type { UpdateJob, UpdateStatus } from "@/contract";
import { failureReason } from "@/hooks/use-api";

const KEY_STORAGE = "rj-update-idempotency-key";
const STATUS_INTERVAL_MS = 15_000;
const MAX_JOB_RETRY_MS = 30_000;

/** The key of the pending start: kept until a job is obtained, so a retried click reuses it. */
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

type UpdatesValue = {
  authorized: boolean;
  setAuthorized: (value: boolean) => void;
  status: UpdateStatus | null;
  statusFresh: boolean;
  job: UpdateJob | null;
  busy: boolean;
  problem: string | null;
  publishedNow: boolean;
  start: () => void;
  cancel: () => void;
  reload: () => void;
};

const UpdatesContext = createContext<UpdatesValue | null>(null);

/**
 * One bounded polling loop serves every interested update view. The top bar opts in only after authorization;
 * the operator screen can read status independently. A failed read keeps the last confirmed values and marks them
 * stale; job actions retain the existing BFF authorization and idempotency contract.
 */
export function UpdatesProvider({ children }: { children: ReactNode }) {
  const [authorized, setAuthorized] = useState(false);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [statusFresh, setStatusFresh] = useState(false);
  const [job, setJob] = useState<UpdateJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [publishedNow, setPublishedNow] = useState(false);

  const statusReadEnabledRef = useRef(false);
  const mountedRef = useRef(false);
  const actionInFlight = useRef(false);
  const statusInFlight = useRef(false);
  const statusRefreshPending = useRef(false);
  const statusController = useRef<AbortController | null>(null);
  const statusRequestVersion = useRef(0);
  const jobRequestVersion = useRef(0);
  const statusFreshRef = useRef(false);
  const refreshStatusRef = useRef<() => Promise<void>>(async () => {});
  const statusReadEnabled = authorized;

  const setAuthorizedState = useCallback((value: boolean) => {
    if (!value) {
      statusReadEnabledRef.current = false;
      statusController.current?.abort();
      statusRefreshPending.current = false;
    }
    setAuthorized(value);
  }, []);

  const refreshStatus = useCallback(async () => {
    if (!statusReadEnabledRef.current || !mountedRef.current) return;
    if (statusInFlight.current) {
      statusRefreshPending.current = true;
      return;
    }

    statusInFlight.current = true;
    const controller = new AbortController();
    statusController.current = controller;
    const requestVersion = ++statusRequestVersion.current;
    const jobVersionAtStart = jobRequestVersion.current;
    try {
      const next = await getUpdateStatus({ signal: controller.signal });
      if (controller.signal.aborted || requestVersion !== statusRequestVersion.current) return;
      setStatus(next);
      setStatusFresh(true);
      statusFreshRef.current = true;
      setProblem(null);
      // A status response cannot rewind a job returned by a later poll or action.
      if (jobRequestVersion.current === jobVersionAtStart) {
        jobRequestVersion.current += 1;
        setJob(next.activeJob ?? next.lastJob ?? null);
      }
    } catch (error) {
      if (!controller.signal.aborted && requestVersion === statusRequestVersion.current) {
        setStatusFresh(false);
        statusFreshRef.current = false;
        setProblem(failureReason(error));
      }
    } finally {
      if (statusController.current === controller) statusController.current = null;
      statusInFlight.current = false;
      if (statusRefreshPending.current && mountedRef.current && statusReadEnabledRef.current) {
        statusRefreshPending.current = false;
        queueMicrotask(() => void refreshStatusRef.current());
      }
    }
  }, []);

  useEffect(() => {
    refreshStatusRef.current = refreshStatus;
  }, [refreshStatus]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      statusController.current?.abort();
    };
  }, []);

  useEffect(() => {
    statusReadEnabledRef.current = statusReadEnabled;
  }, [statusReadEnabled]);

  useEffect(() => {
    if (!statusReadEnabled) return;
    const initial = window.setTimeout(() => void refreshStatus(), 0);
    const timer = window.setInterval(() => void refreshStatus(), STATUS_INTERVAL_MS);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
      statusController.current?.abort();
    };
  }, [statusReadEnabled, refreshStatus]);

  const jobId = job?.id;
  const active = isActive(job);
  const pollAfter = job?.pollAfterSeconds ?? 3;
  useEffect(() => {
    if (!statusReadEnabled || !active || !jobId) return;
    let stopped = false;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    let failures = 0;

    const schedule = (delayMs: number) => {
      timer = window.setTimeout(() => void poll(), delayMs);
    };
    const poll = async () => {
      controller = new AbortController();
      const requestVersion = ++jobRequestVersion.current;
      try {
        const next = await getUpdate(jobId, { signal: controller.signal });
        if (stopped || controller.signal.aborted) return;
        if (requestVersion === jobRequestVersion.current) {
          failures = 0;
          setProblem(null);
          setJob(next);
          if (!isActive(next)) {
            if (next?.published) {
              setPublishedNow(true);
              setStatus((current) => current && next.result
                ? { ...current, lastPublished: { at: next.result.publishedAt } }
                : current);
            }
            void refreshStatus();
          }
        }
      } catch (error) {
        if (!stopped && !controller.signal.aborted && requestVersion === jobRequestVersion.current) {
          failures += 1;
          setProblem(failureReason(error));
        }
      }
      if (stopped) return;
      const retryMs = failures === 0
        ? pollAfter * 1000
        : Math.min(MAX_JOB_RETRY_MS, Math.max(2_000, pollAfter * 1000) * (2 ** Math.min(failures - 1, 4)));
      schedule(retryMs);
    };

    schedule(pollAfter * 1000);
    return () => {
      stopped = true;
      if (timer !== undefined) window.clearTimeout(timer);
      controller?.abort();
      // Invalidate a late response from this poll before the next job/effect owns the slot.
      jobRequestVersion.current += 1;
    };
  }, [statusReadEnabled, active, jobId, pollAfter, refreshStatus]);

  const run = useCallback(async (action: () => Promise<void>, options?: { requireFreshStatus?: boolean }) => {
    if (actionInFlight.current) return;
    if (options?.requireFreshStatus && !statusFreshRef.current) return;
    actionInFlight.current = true;
    setBusy(true);
    setProblem(null);
    try {
      await action();
    } catch (error) {
      setProblem(failureReason(error));
    } finally {
      actionInFlight.current = false;
      setBusy(false);
      void refreshStatus();
    }
  }, [refreshStatus]);

  const start = useCallback(() => {
    void run(async () => {
      // A failed status read must never make the previous idle state sufficient to start work.
      if (!statusFreshRef.current) return;
      statusRequestVersion.current += 1;
      jobRequestVersion.current += 1;
      const started = await startUpdate(pendingKey());
      forgetKey();
      setPublishedNow(false);
      setJob(started.job);
    }, { requireFreshStatus: true });
  }, [run]);

  const cancel = useCallback(() => {
    void run(async () => {
      if (!job) return;
      statusRequestVersion.current += 1;
      jobRequestVersion.current += 1;
      const cancelled = await cancelUpdate(job.id);
      setJob(cancelled);
    });
  }, [job, run]);

  const value: UpdatesValue = {
    authorized,
    setAuthorized: setAuthorizedState,
    status,
    statusFresh,
    job,
    busy,
    problem,
    publishedNow,
    start,
    cancel,
    reload: () => window.location.reload(),
  };

  return <UpdatesContext.Provider value={value}>{children}</UpdatesContext.Provider>;
}

export function useUpdates(): UpdatesValue {
  const value = useContext(UpdatesContext);
  if (!value) throw new Error("useUpdates debe usarse dentro de UpdatesProvider");
  return value;
}
