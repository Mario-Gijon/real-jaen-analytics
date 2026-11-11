import type { ReactNode } from "react";
import type { DataProvenance } from "@/contract";
import css from "./ProvenanceNotice.module.css";

/** Where a section's data come from and what they cover, always above those data. */
export function ProvenanceNotice({ source, children }: { source: DataProvenance; children: ReactNode }) {
  return (
    <div className={css.notice}>
      <p className={css.source}>{source.label}</p>
      {children}
    </div>
  );
}
