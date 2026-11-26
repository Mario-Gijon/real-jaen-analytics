"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";
import { getSession } from "@/client/api";
import { Button } from "@/ui/Button";
import { isActive, useUpdates } from "./use-updates";
import { jobLine, updateTime } from "./labels";
import css from "./Updates.module.css";

export function UpdateControl() {
  const { authorized, setAuthorized, status, statusFresh, job, busy, problem, publishedNow, start, cancel, reload } = useUpdates();

  useEffect(() => {
    const controller = new AbortController();
    void getSession({ signal: controller.signal })
      .then((session) => setAuthorized(session.signedIn && session.canUpdate))
      .catch(() => {
        if (!controller.signal.aborted) setAuthorized(false);
      });
    return () => controller.abort();
  }, [setAuthorized]);

  if (!authorized) return null;
  if (!status?.scope) return null;
  const scope = status.scope;
  const running = isActive(job);
  const line = job ? jobLine(job) : status.lastPublished ? `Publicado el ${updateTime(status.lastPublished.at)}` : "Sin publicaciones";
  return (
    <div className={css.bar}>
      <span aria-live="polite" className={css.announce} role="status">{line}</span>
      <Link className={css.barState} href="/operador" title={scope.label}>{line}</Link>
      {publishedNow ? (
        <Button className={css.barButton} onClick={reload} size="sm" variant="primary">Recargar datos</Button>
      ) : running ? (
        <Button className={css.barButton} disabled={busy || !job?.cancellable} onClick={cancel} size="sm">
          {job?.cancelRequested ? "Cancelando" : "Cancelar"}
        </Button>
      ) : !status.available ? (
        <Button
          aria-label="Actualización temporalmente no disponible"
          className={css.barButton}
          disabled
          size="sm"
          title="El servicio de actualización no está disponible o no tiene el proveedor esperado."
        >
          No disponible
        </Button>
      ) : (
        <Button
          aria-label={`${scope.actionLabel}: ${scope.label}`}
          className={css.barButton}
          disabled={busy || !statusFresh}
          icon={<RefreshCw aria-hidden="true" size={15} />}
          onClick={start}
          size="sm"
          title={statusFresh ? scope.description : "El estado está desactualizado; espera a que se confirme antes de iniciar."}
          variant="primary"
        >
          Actualizar
        </Button>
      )}
      {problem ? <span className={css.barProblem} role="alert">{problem}</span> : null}
    </div>
  );
}
