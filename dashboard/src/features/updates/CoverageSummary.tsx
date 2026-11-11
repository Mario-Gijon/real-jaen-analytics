"use client";

import Link from "next/link";
import type { UpdateCoverage } from "@/contract";
import { REASON_LABELS, RESOURCE_LABELS, updateTime } from "./labels";
import css from "./Updates.module.css";

const COMMENT_FAILURE_LABELS: Record<string, string> = {
  parse: "Respuesta ilegible",
  error: "Error de consulta",
  persistence_error: "No se pudo guardar la captura",
  transport_error: "Error de transporte",
};

const COMMENT_PENDING_LABELS: Record<string, string> = {
  budget: "aplazado por el límite de consultas; se conserva la versión previa si existía",
  not_requested: "pendiente para una próxima actualización",
};

const COMMENT_UNAVAILABLE_LABELS: Record<string, string> = {
  not_found: "SofaScore respondió 404; se volverá a consultar en futuras actualizaciones",
  empty: "SofaScore devolvió una lista vacía; se volverá a consultar en futuras actualizaciones",
};

/**
 * What a group update published: new data, previous data kept and pending resources, never mixed. A pending
 * resource is not "no data": it keeps the previous version if there was one, and is never shown as zero.
 */
export function CoverageSummary({ coverage, requestedAt }: { coverage: UpdateCoverage; requestedAt?: string }) {
  // A match by its teams (named by the backend from the data just published), linked to its page.
  const MatchName = ({ matchId }: { matchId: string }) => {
    const sourceId = matchId.replace(/^sofascore-match-/, "");
    return <>
      {coverage.matchNames[matchId]
        ? <Link href={`/matches/${matchId}`}>{coverage.matchNames[matchId]}</Link>
        : <span>Evento externo {matchId}</span>}
      {sourceId !== matchId ? <span className={css.matchId}> (SofaScore ID {sourceId})</span> : null}
    </>;
  };
  const commentsUnavailableById = new Map([
    ...coverage.comments.failed
      .filter(({ reason }) => reason === "not_found" || reason === "empty")
      .map((item) => [item.matchId, item] as const),
    ...coverage.comments.emptyMatchIds.map((matchId) => [matchId, { matchId, reason: "empty" }] as const),
  ]);
  const commentsUnavailable = [...commentsUnavailableById.values()];
  const deferredComments = new Set(coverage.deferredMatchIds);
  const commentsPendingById = new Map<string, { matchId: string; reason: string }>();
  for (const matchId of coverage.comments.pendingMatchIds) {
    commentsPendingById.set(matchId, { matchId, reason: deferredComments.has(matchId) ? "budget" : "not_requested" });
  }
  for (const item of coverage.comments.failed.filter(({ reason }) => reason === "budget")) {
    commentsPendingById.set(item.matchId, item);
  }
  const commentsPending = [...commentsPendingById.values()];
  const commentsErrors = coverage.comments.failed.filter(({ reason }) =>
    reason !== "not_found" && reason !== "empty" && reason !== "budget");
  return (
    <div className={css.coverage}>
      {requestedAt ? <p className={css.muted}>Solicitud recibida el {updateTime(requestedAt)}; la cobertura corresponde a esta operación.</p> : null}
      <ul className={css.counts}>
        <li><strong>{coverage.newResources}</strong> datos nuevos</li>
        <li><strong>{coverage.keptResources}</strong> datos anteriores conservados</li>
        {coverage.pending.length ? (
          <li><strong>{coverage.pending.length}</strong> {coverage.pending.length === 1 ? "partido" : "partidos"} con pendientes</li>
        ) : (
          <li>sin pendientes</li>
        )}
      </ul>
      {coverage.pending.length ? (
        <>
          <h3>Pendientes</h3>
          <ul className={css.pending}>
            {coverage.pending.map((match) => (
              <li key={match.matchId}>
                <MatchName matchId={match.matchId} />
                {": "}
                {match.resources.map((r, i) => (
                  <span key={r.resource}>
                    {i ? "; " : ""}
                    {RESOURCE_LABELS[r.resource]} ({REASON_LABELS[r.reason]}
                    {r.kept ? ", se mantiene la versión anterior" : ", sin datos publicados"})
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {coverage.deferredMatchIds.length ? (
        <>
          <h3>Revisiones aplazadas</h3>
          <p className={css.muted}>
            {coverage.deferredMatchIds.length} {coverage.deferredMatchIds.length === 1 ? "revisión queda" : "revisiones quedan"} para
            la próxima actualización por el límite de consultas. Se siguen sirviendo sus datos anteriores.
          </p>
          <ul className={css.pending}>
            {coverage.deferredMatchIds.map((matchId) => (
              <li key={matchId}><MatchName matchId={matchId} /></li>
            ))}
          </ul>
        </>
      ) : null}
      {coverage.restrictedMatchIds.length ? (
        <>
          <h3>Fuera del alcance de esta actualización</h3>
          <p className={css.muted}>
            Esta actualización solo consulta los partidos de {coverage.detailRounds?.length === 1 ? "la jornada" : "las jornadas"}{" "}
            {(coverage.detailRounds ?? []).join(", ")}. En {coverage.restrictedMatchIds.length}{" "}
            {coverage.restrictedMatchIds.length === 1 ? "partido" : "partidos"} se detectó un cambio o una revisión pendiente:
            no se han consultado y se siguen sirviendo sus datos anteriores.
          </p>
          <ul className={css.pending}>
            {coverage.restrictedMatchIds.map((matchId) => (
              <li key={matchId}><MatchName matchId={matchId} /></li>
            ))}
          </ul>
        </>
      ) : null}
      {coverage.newMatches.length ? (
        <>
          <h3>Eventos detectados para revisión</h3>
          <p className={css.muted}>No se han incorporado ni consultado sus detalles. Confirma su identidad antes de añadir una sustitución.</p>
          <ul className={css.pending}>
            {coverage.newMatches.map((match) => (
              <li key={match.matchId}>
                <MatchName matchId={match.matchId} />
                {match.replaces.length ? (
                  <> — posible sustitución de <MatchName matchId={match.replaces[0]} />: decide el operador con la herramienta de sustituciones</>
                ) : null}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <h3>Comentarios narrativos</h3>
      <ul className={css.counts}>
        <li><strong>{coverage.comments.newMatchIds.length}</strong> partidos con narración nueva</li>
        <li><strong>{coverage.comments.keptMatchIds.length}</strong> partidos con comentarios conservados</li>
        <li><strong>{commentsPending.length}</strong> partidos pendientes de comentarios</li>
        <li><strong>{commentsUnavailable.length}</strong> {commentsUnavailable.length === 1 ? "partido sin narración disponible" : "partidos sin narración disponible"}</li>
        <li><strong>{commentsErrors.length}</strong> {commentsErrors.length === 1 ? "partido con error de comentarios" : "partidos con errores de comentarios"}</li>
      </ul>
      <p className={css.muted}>Los 404 y las listas vacías no bloquean resultados ni estadísticas; se volverán a consultar en futuras actualizaciones.</p>
      {commentsPending.length ? (
        <ul className={css.pending} aria-label="Partidos pendientes de comentarios narrativos">
          {commentsPending.map(({ matchId, reason }) => (
            <li key={matchId}><MatchName matchId={matchId} />: {COMMENT_PENDING_LABELS[reason] ?? COMMENT_PENDING_LABELS.not_requested}</li>
          ))}
        </ul>
      ) : null}
      {commentsUnavailable.length ? (
        <ul className={css.pending} aria-label="Partidos sin narración disponible">
          {commentsUnavailable.map(({ matchId, reason }) => (
            <li key={matchId}><MatchName matchId={matchId} />: {COMMENT_UNAVAILABLE_LABELS[reason]}</li>
          ))}
        </ul>
      ) : null}
      {commentsErrors.length ? (
        <ul className={css.pending} aria-label="Errores de comentarios narrativos">
          {commentsErrors.map(({ matchId, reason }) => (
            <li key={matchId}><MatchName matchId={matchId} />: {COMMENT_FAILURE_LABELS[reason] ?? reason}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
