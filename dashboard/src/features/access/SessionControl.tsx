"use client";

import { LogOut } from "lucide-react";
import { useEffect, useState } from "react";
import { getSession, signOut } from "@/client/api";
import css from "./Access.module.css";

/** «Cerrar sesión» in the top bar, only where the installation asks for the password and this browser has a session. */
export function SessionControl() {
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    getSession({ signal: controller.signal })
      .then((session) => setShown(session.required && session.signedIn))
      .catch(() => {
        // Without an answer there is nothing to close here.
      });
    return () => controller.abort();
  }, []);
  if (!shown) return null;
  const close = async () => {
    setBusy(true);
    try {
      await signOut();
    } finally {
      window.location.assign(new URL("/acceso", window.location.origin).href);  // a full load: nothing left on screen
    }
  };
  return (
    <button aria-label="Cerrar sesión" className={css.signOut} disabled={busy} onClick={close} title="Cerrar sesión" type="button">
      <LogOut aria-hidden="true" size={18} />
    </button>
  );
}
