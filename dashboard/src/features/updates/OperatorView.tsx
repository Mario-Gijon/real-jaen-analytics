"use client";

import Link from "next/link";
import { Button } from "@/ui/Button";
import { PageHeader } from "@/ui/PageHeader";
import { LoadingState } from "@/ui/LoadingState";
import { isActive, useUpdates } from "./use-updates";
import { CoverageSummary } from "./CoverageSummary";
import { PHASE_LABELS, STEPS, hasPending, jobLine, updateTime } from "./labels";
import css from "./Updates.module.css";

/**
 * The update screen (not in the navigation, reached from the top bar): real scope, last publication, the job's
 * step, cancel while still consulting, and its outcome. The session that consults the data can update them.
 * Whatever happens, the last valid data stay published.
 */
export function OperatorView() {
  const { status, job, busy, problem, publishedNow, start, cancel, reload } = useUpdates();

  if (status === null) {
    return <>
      <PageHeader title="Actualización de datos" />
      {problem ? <p className={css.problem} role="alert">{problem}</p>
        : <section aria-label="Estado de actualización" aria-busy="true"><LoadingState variant="section" label="Cargando estado de actualización…" /></section>}
    </>;
  }
  if (!status.available) {
    return (
      <>
        <PageHeader title="Actualización de datos" />
        <p>La actualización no está disponible en esta instalación.</p>
      </>
    );
  }
  const running = isActive(job);
  const scope = status.scope;

  return (
    <>
      <PageHeader title="Actualización de datos" description={scope ? scope.label : undefined} />
      {scope ? (
        <section className={css.section} aria-labelledby="alcance">
          <h2 id="alcance">Alcance</h2>
          <p>{scope.description}</p>
          {scope.kind === "pilot" && scope.matchIds.length === 1 ? (
            <p><Link href={`/matches/${scope.matchIds[0]}`}>Ver el partido del piloto</Link></p>
          ) : null}
          <p className={css.muted}>
            {status.lastPublished
              ? `Última publicación desde este control: ${updateTime(status.lastPublished.at)}.`
              : "Todavía no hay publicaciones desde este control."}
          </p>
        </section>
      ) : null}

      <section className={css.section} aria-labelledby="estado">
        <h2 id="estado">Estado</h2>
        {job ? (
          <>
            {running ? (
              <ol className={css.steps}>
                {STEPS.map((step) => (
                  <li aria-current={job.phase === step ? "step" : undefined} key={step}>{PHASE_LABELS[step]}</li>
                ))}
              </ol>
            ) : null}
            <p aria-live="polite" className={hasPending(job) ? css.warn : job.status === "completed" ? css.ok : running || job.status === "cancelled" ? undefined : css.problem} role="status">
              {jobLine(job)}
            </p>
            <p className={css.muted}>Solicitada el {updateTime(job.requestedAt)}.</p>
            {job.status === "completed" && job.result?.coverage ? (
              <CoverageSummary coverage={job.result.coverage} requestedAt={job.requestedAt} />
            ) : null}
          </>
        ) : (
          <p className={css.muted}>No hay actualizaciones todavía.</p>
        )}
        {status.lastCoverage && status.lastPublished && !(job?.status === "completed" && job.result?.coverage) ? (
          // A later update did not publish: what the last publication left pending or deferred is still what is served.
          <section aria-labelledby="ultima-publicacion" className={css.coverage}>
            <h3 id="ultima-publicacion">Última publicación ({updateTime(status.lastPublished.at)})</h3>
            <CoverageSummary coverage={status.lastCoverage} />
          </section>
        ) : null}
        <div className={css.actions}>
          {running ? (
            <Button disabled={busy || !job?.cancellable} onClick={cancel}>
              {job?.cancelRequested ? "Cancelando…" : job?.cancellable ? "Cancelar" : "Ya no se puede cancelar"}
            </Button>
          ) : (
            <Button disabled={busy} onClick={start} variant="primary">{scope?.actionLabel ?? "Actualizar"}</Button>
          )}
          {publishedNow ? <Button onClick={reload}>Recargar datos</Button> : null}
        </div>
        {problem ? <p className={css.problem} role="alert">{problem}</p> : null}
      </section>
    </>
  );
}
