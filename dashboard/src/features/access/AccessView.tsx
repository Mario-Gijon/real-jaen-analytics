"use client";

import { useEffect, useState, type FormEvent } from "react";
import { ApiRequestError, getSession, signIn } from "@/client/api";
import { Button } from "@/ui/Button";
import css from "./Access.module.css";

const MESSAGES: Record<number, string> = {
  401: "Contraseña incorrecta.",
  429: "Demasiados intentos. Espera unos minutos.",
};

/**
 * The shared password, exchanged for a session cookie that lasts 90 days on this browser. The password is sent
 * once and never stored here; after signing in the page returns to where the analyst was going (`next`).
 */
export function AccessView({ next }: { next: string }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [configured, setConfigured] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    getSession({ signal: controller.signal }).catch((error: unknown) => {
      // 503: this installation asks for a password it does not have configured.
      if (error instanceof ApiRequestError && error.status === 503) setConfigured(false);
    });
    return () => controller.abort();
  }, []);

  if (!configured) {
    return <div className={css.screen}>
      <section aria-labelledby="access-title" className={css.card}>
        <h1 className={css.title} id="access-title">Bienvenido</h1>
        <p className={css.problem} role="alert">El acceso no está configurado en esta instalación.</p>
      </section>
    </div>;
  }
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await signIn(password);
      setPassword("");
      window.location.assign(new URL(next, window.location.origin).href);  // a full load: no state from before
    } catch (error) {
      setProblem(error instanceof ApiRequestError ? MESSAGES[error.status] ?? "No se pudo iniciar sesión." : "No se pudo iniciar sesión.");
      setBusy(false);
    }
  };
  return <div className={css.screen}>
    <section aria-labelledby="access-title" className={css.card}>
      <header className={css.heading}>
        <h1 className={css.title} id="access-title">Bienvenido</h1>
        <p>Introduce la contraseña para acceder al panel de análisis.</p>
      </header>
      <form className={css.form} onSubmit={submit}>
        <label htmlFor="access-password">Contraseña</label>
        <input
          autoComplete="current-password"
          autoFocus
          id="access-password"
          onChange={(event) => setPassword(event.target.value)}
          type="password"
          value={password}
        />
        {problem ? <p className={css.problem} role="alert">{problem}</p> : null}
        <Button className={css.submit} disabled={busy || password.length === 0} type="submit" variant="primary">{busy ? "Entrando…" : "Entrar"}</Button>
        <p className={css.note}>La sesión se mantendrá durante 90 días en este navegador.</p>
      </form>
    </section>
  </div>;
}
