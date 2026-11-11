import css from "./LoadingState.module.css";

export type LoadingVariant = "page" | "section" | "inline";

/** One accessible loading indicator, sized for a page, visible section or small inline value. */
export function LoadingState({ variant, label }: { variant: LoadingVariant; label: string }) {
  return <div className={css.root} data-variant={variant} role="status" aria-live="polite" aria-atomic="true">
    <span className={css.indicator} aria-hidden="true">
      <svg className={css.ball} viewBox="0 0 32 32" focusable="false">
        <circle cx="16" cy="16" r="12.5" />
        <path d="m16 9 4.1 3-1.6 4.8h-5L12 12l4-3Zm-4 3-4.2.2m10.7 4.6 3.5 3.1m-8.5-3.1-2.3 5m4.8-9.8 1.5-4.7m-5.5 2.7L9.1 8.5m5.9 8.3-3.8 3.1m7.4-2.9 4.3-.1m-9.5-6.8-1.3-4.5" />
      </svg>
    </span>
    <span className={css.label}>{label}</span>
  </div>;
}
