import type { UpdateJob, UpdatePendingReason, UpdatePhase, UpdateResource } from "@/contract";

const timeFormat = new Intl.DateTimeFormat("es-ES", {
  day: "2-digit",
  month: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Europe/Madrid",
});

/** UTC ISO-8601 from the API as local time of Jaén. */
export function updateTime(iso: string): string {
  return timeFormat.format(new Date(iso));
}

export const PHASE_LABELS: Record<Exclude<UpdatePhase, "finished">, string> = {
  queued: "En cola",
  acquiring: "Consultando datos",
  verifying: "Comprobando los datos recibidos",
  publishing: "Publicando",
};

export const STEPS: Exclude<UpdatePhase, "queued" | "finished">[] = ["acquiring", "verifying", "publishing"];

export const RESOURCE_LABELS: Record<UpdateResource, string> = {
  event: "datos del partido",
  lineups: "alineaciones",
  statistics: "estadísticas",
  incidents: "incidencias",
};

/** Why a resource is pending, in Spanish. None of them means "no data exist" and none becomes a zero. */
export const REASON_LABELS: Record<UpdatePendingReason, string> = {
  empty: "respuesta vacía",
  not_found: "no encontrado en SofaScore (404)",
  parse: "respuesta no interpretable",
  error: "error temporal de consulta",
  budget: "aplazado por el límite de consultas",
  not_requested: "no consultado en esta actualización",
  restricted: "fuera del alcance de esta actualización",
};

/** A completed update that published while some resources stayed pending. */
export const hasPending = (job: UpdateJob) => job.status === "completed" && (
  (job.result?.coverage?.pending.length ?? 0) > 0 ||
  (job.result?.coverage?.comments.pendingMatchIds.length ?? 0) > 0 ||
  (job.result?.coverage?.comments.emptyMatchIds.length ?? 0) > 0 ||
  (job.result?.coverage?.comments.failed.length ?? 0) > 0
);

/** One line for the analyst about where a job is or how it ended. Never claims more than was published. */
export function jobLine(job: UpdateJob): string {
  if (job.status === "queued" || job.status === "running") {
    const phase = job.phase === "finished" ? "publishing" : job.phase;
    return job.cancelRequested ? "Cancelando…" : `${PHASE_LABELS[phase]}…`;
  }
  if (job.status === "completed" && job.result) {
    if (hasPending(job)) return `Actualización completada con pendientes (${updateTime(job.result.publishedAt)})`;
    return `Publicado el ${updateTime(job.result.publishedAt)}${job.result.reusedBundle ? ", sin volver a consultar" : ""}`;
  }
  if (job.status === "cancelled") {
    return job.published
      ? "Cancelada después de publicar lotes. Los datos ya publicados siguen disponibles."
      : "Cancelada. Se mantienen los datos anteriores.";
  }
  const reason = job.error?.message ?? "No se pudo actualizar.";
  return job.published
    ? `${reason} Se conserva la última publicación válida; los lotes ya publicados siguen disponibles.`
    : `${reason} Se mantienen los datos anteriores.`;
}
