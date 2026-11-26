"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { getSession } from "@/client/api";
import { routePath } from "@/contract/base-path";
import { isActive, useUpdates } from "./use-updates";
import css from "./Updates.module.css";

export function UpdateControl() {
  const pathname = routePath(usePathname());
  const { setAuthorized, job } = useUpdates();

  useEffect(() => {
    const controller = new AbortController();
    void getSession({ signal: controller.signal })
      .then((session) => setAuthorized(session.signedIn && session.canUpdate))
      .catch(() => {
        if (!controller.signal.aborted) setAuthorized(false);
      });
    return () => controller.abort();
  }, [setAuthorized]);

  const running = isActive(job);
  return (
    <Link
      aria-current={pathname === "/operador" ? "page" : undefined}
      aria-label={running ? "Actualizaciones, actualización en curso" : "Actualizaciones"}
      className={css.navUpdates}
      href="/operador"
    >
      <RefreshCw aria-hidden="true" size={16} />
      <span>Actualizaciones</span>
      <span aria-hidden="true" className={css.activityDot} data-active={running || undefined} />
    </Link>
  );
}
