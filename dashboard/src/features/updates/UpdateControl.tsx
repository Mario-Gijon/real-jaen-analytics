"use client";

import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getSession, SESSION_CHANGED_EVENT } from "@/client/api";
import { routePath } from "@/contract/base-path";
import { isActive, useUpdates } from "./use-updates";
import css from "./Updates.module.css";

export function UpdateControl() {
  const pathname = routePath(usePathname());
  const router = useRouter();
  const { setAuthorized, job } = useUpdates();
  const [canUpdate, setCanUpdate] = useState(false);

  useEffect(() => {
    let controller: AbortController | null = null;
    let disposed = false;
    const refreshSession = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      setCanUpdate(false);
      setAuthorized(false);
      try {
        const session = await getSession({ signal: request.signal });
        if (disposed || request.signal.aborted) return;
        const allowed = session.signedIn && session.canUpdate;
        setCanUpdate(allowed);
        setAuthorized(allowed);
        if (!allowed && pathname === "/operador") {
          router.replace(session.signedIn ? "/" : "/acceso?next=%2Foperador");
        }
      } catch {
        if (!disposed && !request.signal.aborted) setAuthorized(false);
      }
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refreshSession();
    };
    window.addEventListener(SESSION_CHANGED_EVENT, refreshSession);
    window.addEventListener("focus", refreshSession);
    document.addEventListener("visibilitychange", onVisibilityChange);
    void refreshSession();
    return () => {
      disposed = true;
      controller?.abort();
      window.removeEventListener(SESSION_CHANGED_EVENT, refreshSession);
      window.removeEventListener("focus", refreshSession);
      document.removeEventListener("visibilitychange", onVisibilityChange);
      setAuthorized(false);
    };
  }, [pathname, router, setAuthorized]);

  if (!canUpdate) return null;
  const running = isActive(job);
  return (
    <Link
      aria-current={pathname === "/operador" ? "page" : undefined}
      aria-label={running ? "Actualizar, actualización en curso" : "Actualizar"}
      className={css.navUpdates}
      href="/operador"
      title="Actualizar"
    >
      <RefreshCw aria-hidden="true" size={16} />
      <span className={css.navLabel}>Actualizar</span>
      <span aria-hidden="true" className={css.activityDot} data-active={running || undefined} />
    </Link>
  );
}
