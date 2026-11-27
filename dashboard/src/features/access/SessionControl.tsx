"use client";

import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getSession, SESSION_CHANGED_EVENT, signOut } from "@/client/api";
import css from "./Access.module.css";

/** «Cerrar sesión» in the top bar, only where the installation asks for the password and this browser has a session. */
export function SessionControl() {
  const router = useRouter();
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let controller: AbortController | null = null;
    let disposed = false;
    const refreshSession = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      setShown(false);
      try {
        const session = await getSession({ signal: request.signal });
        if (!disposed && !request.signal.aborted) setShown(session.required && session.signedIn);
      } catch {
        // No logout control is shown until the current session is confirmed.
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
    };
  }, []);
  if (!shown) return null;
  const close = async () => {
    setBusy(true);
    try {
      await signOut();
    } finally {
      setShown(false);
      router.replace("/acceso");
    }
  };
  return (
    <button aria-label="Cerrar sesión" className={css.signOut} disabled={busy} onClick={close} title="Cerrar sesión" type="button">
      <LogOut aria-hidden="true" size={18} />
    </button>
  );
}
