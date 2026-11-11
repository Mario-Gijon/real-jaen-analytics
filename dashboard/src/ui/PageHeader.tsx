import type { ReactNode } from "react";
import css from "./PageHeader.module.css";

/** Optional page title and description, or an action area without an empty title row. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title?: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  if (!title && !description && !actions) return null;
  return (
    <header className={css.header} data-actions-only={!title && !description || undefined}>
      {title || description ? <div className={css.copy}>
        {title ? <h1>{title}</h1> : null}
        {description ? <p>{description}</p> : null}
      </div> : null}
      {actions ? <div className={css.actions}>{actions}</div> : null}
    </header>
  );
}
