/** Football boot with studs, using the same monochrome pattern as GoalIcon. */
export function AssistIcon({ label = "Asistencia" }: { label?: string }) {
  return <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" role="img" aria-label={label} focusable="false">
    <path d="M3 5h5l2 4 5 3 5 1a2 2 0 0 1 2 2v3H3Z" />
    <path d="M3 15h19M9 8l-2 2M12 10l-2 2M15 12l-2 2" />
    <path d="M5 18v3h2v-3M11 18v3h2v-3M18 18v3h2v-3" />
  </svg>;
}
