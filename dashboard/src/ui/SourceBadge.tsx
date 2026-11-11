"use client";

import { FlaskConical } from "lucide-react";
import { useEffect, useState } from "react";
import { getSource } from "@/client/api";
import type { SourceInfo } from "@/contract";
import css from "./AppShell.module.css";

/**
 * Discreet, permanent notice while the API serves the demonstration
 * (`DATA_MODE=demo`), so no invented result can pass for real sporting
 * information. It asks the API, never the source. With real data there is no
 * notice here: each section labels itself «Datos reales».
 */
export function SourceBadge() {
  const [source, setSource] = useState<SourceInfo | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getSource({ signal: controller.signal })
      .then(setSource)
      .catch(() => {
        // Without an answer nothing is claimed about the data.
      });
    return () => controller.abort();
  }, []);
  if (!source?.synthetic) return null;
  return (
    <p
      className={css.badge}
      title="Modo demostración: competición, rivales, jugadores, resultados y estadísticas son inventados."
    >
      <FlaskConical aria-hidden="true" size={15} />
      {source.label}
    </p>
  );
}
