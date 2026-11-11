import type { ReactNode } from "react";
import css from "./PendingSection.module.css";

/** Reserved area for content still to be built; it shows no data. */
export function PendingSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section aria-label={label} className={css.pending}>
      <p>{children}</p>
    </section>
  );
}
