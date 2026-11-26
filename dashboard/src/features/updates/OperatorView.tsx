"use client";

import { AlertTriangle, CheckCircle2, ChevronDown } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/PageHeader";
import { LoadingState } from "@/ui/LoadingState";
import type { UpdateCoverage, UpdatePhase } from "@/contract";
import { isActive, useUpdates } from "./use-updates";
import { CoverageSummary } from "./CoverageSummary";
import { STEPS, jobLine, updateTime } from "./labels";
import css from "./Updates.module.css";

const PHASE_SHORT_LABELS: Record<Exclude<UpdatePhase, "finished">, string> = {
  queued: "En cola",
  acquiring: "Consulta",
  verifying: "Verificación",
  publishing: "Publicación",
};

function reviewIssueCount(coverage: UpdateCoverage | null | undefined): number {
  if (!coverage) return 0;
  const pendingResources = coverage.pending.flatMap((match) => match.resources)
    .filter(({ reason }) => !["budget", "restricted", "not_requested"].includes(reason)).length;
  const commentErrors = coverage.comments.failed
    .filter(({ reason }) => !["not_found", "empty", "budget"].includes(reason)).length;
  return pendingResources + commentErrors + coverage.newMatches.length;
}

/** Compact update summary; budgets and detailed coverage stay in the closed disclosure. */
export function OperatorView() {
  const { status, statusFresh, job, busy, problem, publishedNow, start, cancel, reload, registerStatusReader } = useUpdates();
  useEffect(() => registerStatusReader(), [registerStatusReader]);

  if (status === null) {
    return <>
      <PageHeader title="Actualizaciones" />
      {problem ? <p className={css.problem} role="alert">{problem}</p>
        : <section aria-label="Estado de actualización" aria-busy="true"><LoadingState variant="section" label="Cargando actualizaciones…" /></section>}
    </>;
  }

  const scope = status.scope;
  if (!scope) {
    return <>
      <PageHeader title="Actualizaciones" />
      <p className={css.muted}>El servicio de actualización no está disponible en esta instalación.</p>
    </>;
  }

  const running = isActive(job);
  const coverage = job?.status === "completed" && job.result?.coverage
    ? job.result.coverage
    : status.lastCoverage;
  const issues = reviewIssueCount(coverage);
  const lastUpdatedAt = status.lastPublished?.at
    ?? (job?.status === "completed" ? job.result?.publishedAt : null)
    ?? null;
  const phaseIndex = job?.phase === "queued" ? -1 : STEPS.indexOf(job?.phase as typeof STEPS[number]);

  return <>
    <PageHeader title="Actualizaciones" />
    <section className={css.summary} aria-label="Resumen de actualizaciones">
      <div className={css.summaryHead}>
        <div className={css.lastUpdate}>
          <span className={css.eyebrow}>Última actualización</span>
          {lastUpdatedAt
            ? <time dateTime={lastUpdatedAt}>{updateTime(lastUpdatedAt)}</time>
            : <span className={css.muted}>Sin publicaciones</span>}
        </div>
        {job?.status === "completed" ? (
          <p className={css.success} role="status"><CheckCircle2 aria-hidden="true" size={19} />Datos actualizados</p>
        ) : null}
      </div>

      {running && job ? (
        <div className={css.progressBlock}>
          <p className={css.progressTitle} aria-live="polite">
            {job.cancelRequested ? "Cancelando actualización" : "Actualización en curso"}
          </p>
          {job.phase === "queued" ? <p className={css.muted}>En cola</p> : null}
          <ol className={css.steps} aria-label="Fases de la actualización">
            {STEPS.map((step, index) => (
              <li
                aria-current={job.phase === step ? "step" : undefined}
                data-state={index < phaseIndex ? "done" : job.phase === step ? "current" : "upcoming"}
                key={step}
              >
                <span className={css.stepMarker}>{index < phaseIndex ? "✓" : index + 1}</span>
                {PHASE_SHORT_LABELS[step]}
              </li>
            ))}
          </ol>
        </div>
      ) : null}

      {!status.available ? <p className={css.notice} role="status">El servicio de actualización no está disponible ahora.</p> : null}
      {!statusFresh ? <p className={css.notice} role="status">Estado temporalmente desactualizado; se conserva la última confirmación.</p> : null}
      {job?.status === "failed" || job?.status === "cancelled" ? (
        <p className={job.status === "failed" ? css.problem : css.muted} role={job.status === "failed" ? "alert" : "status"}>
          {jobLine(job)}
        </p>
      ) : null}
      {job?.status === "completed" && issues > 0 ? (
        <p className={css.issueNotice} role="alert">
          <AlertTriangle aria-hidden="true" size={17} />
          {issues} {issues === 1 ? "incidencia requiere" : "incidencias requieren"} revisión.
        </p>
      ) : null}
      {problem ? <p className={css.problem} role="alert">{problem}</p> : null}

      {!running && coverage ? (
        <div className={css.metrics} aria-label="Métricas principales">
          <article className={css.metric}>
            <span>Datos nuevos</span><strong>{coverage.newResources}</strong>
          </article>
          <article className={css.metric}>
            <span>Conservados</span><strong>{coverage.keptResources}</strong>
          </article>
          <article className={css.metric} data-alert={issues > 0 || undefined}>
            <span>Incidencias</span><strong>{issues}</strong>
          </article>
        </div>
      ) : null}

      <div className={css.actions}>
        {running && job ? (
          job.cancellable ? (
            <Button disabled={busy || job.cancelRequested} onClick={cancel}>
              {job.cancelRequested ? "Cancelando…" : "Cancelar actualización"}
            </Button>
          ) : <span className={css.muted}>La fase de publicación ya no se puede cancelar.</span>
        ) : (
          <Button disabled={busy || !status.available || !statusFresh} onClick={start} variant="primary">
            Actualizar datos
          </Button>
        )}
        {publishedNow ? <Button onClick={reload}>Ver datos publicados</Button> : null}
      </div>

      <details className={css.details}>
        <summary>
          <span>Detalles de la actualización</span>
          <ChevronDown aria-hidden="true" size={17} />
        </summary>
        <div className={css.detailsBody}>
          <section className={css.scopeDetails} aria-label="Alcance de la actualización">
            <h2>{scope.label}</h2>
            <p>{scope.description}</p>
            {scope.kind === "pilot" && scope.matchIds.length === 1 ? (
              <p><Link href={`/matches/${scope.matchIds[0]}`}>Ver partido del piloto</Link></p>
            ) : null}
          </section>
          {job && (job.status !== "completed" || !coverage) ? (
            <p className={css.muted}>Solicitud recibida el {updateTime(job.requestedAt)}.</p>
          ) : null}
          {coverage ? (
            <CoverageSummary
              coverage={coverage}
              requestedAt={job?.status === "completed" ? job.requestedAt : undefined}
              showResourceTotals={false}
            />
          ) : <p className={css.muted}>No hay detalles de cobertura todavía.</p>}
        </div>
      </details>
    </section>
  </>;
}
