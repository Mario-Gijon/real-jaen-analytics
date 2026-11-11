/** Moves only the history viewport, never the page. */
export function revealMatch(history: HTMLElement | null, matchId: string, axis: "vertical" | "horizontal" = "vertical") {
  const row = history && [...history.querySelectorAll<HTMLElement>("[data-match-id]")]
    .find(element => element.dataset.matchId === matchId);
  if (!history || !row) return;
  if (axis === "horizontal") {
    if (history.scrollWidth <= history.clientWidth) return;
    const bounds = history.getBoundingClientRect();
    const rowBounds = row.getBoundingClientRect();
    if (rowBounds.left < bounds.left) history.scrollLeft += rowBounds.left - bounds.left;
    else if (rowBounds.right > bounds.right) history.scrollLeft += rowBounds.right - bounds.right;
    return;
  }
  if (history.scrollHeight <= history.clientHeight) return;
  const bounds = history.getBoundingClientRect();
  const rowBounds = row.getBoundingClientRect();
  const visibleTop = Math.max(bounds.top, 0);
  const visibleBottom = Math.min(bounds.bottom, document.documentElement.clientHeight);
  const top = visibleBottom > visibleTop ? visibleTop : bounds.top;
  const bottom = visibleBottom > visibleTop ? visibleBottom : bounds.bottom;
  if (rowBounds.top < top) history.scrollTop += rowBounds.top - top;
  else if (rowBounds.bottom > bottom) history.scrollTop += rowBounds.bottom - bottom;
}
