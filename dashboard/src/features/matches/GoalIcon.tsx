/** Shared football symbol for goals; monochrome and labelled for screen readers. */
export function GoalIcon({ label = "Gol" }: { label?: string }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinejoin="round" role="img" aria-label={label} focusable="false">
    <circle cx="12" cy="12" r="10" />
    <path d="M12 7 16.76 10.45 14.94 16.05H9.06L7.24 10.45Z" fill="currentColor" />
    <path d="M12 7V2M16.76 10.45 21.51 8.91M14.94 16.05 17.88 20.09M9.06 16.05 6.12 20.09M7.24 10.45 2.49 8.91" />
  </svg>;
}
