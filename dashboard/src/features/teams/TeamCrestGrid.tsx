"use client";

import { Check } from "lucide-react";
import { useRef, type KeyboardEvent } from "react";
import type { TeamRef } from "@/contract";
import { TeamCrest } from "@/ui/TeamCrest";
import css from "./TeamCrestGrid.module.css";

/**
 * Compact grid of crests to pick a club. Native radios give one tab stop and
 * arrow-key browsing (each arrow selects); a click, a tap or Enter/Space
 * confirms the choice (`onCommit`), which lets phones fold the grid.
 */
export function TeamCrestGrid({
  teams,
  selectedId,
  onSelect,
  onCommit,
  id,
}: {
  teams: readonly TeamRef[];
  selectedId: string | null;
  onSelect: (team: TeamRef) => void;
  onCommit?: (team: TeamRef) => void;
  id?: string;
}) {
  const pointer = useRef(false);
  function confirmKey(event: KeyboardEvent<HTMLInputElement>, team: TeamRef) {
    if (event.key !== "Enter" && event.key !== " ") return;
    // Space checks a radio natively; Enter does not, so it selects here.
    if (event.key === "Enter") {
      event.preventDefault();
      if (team.id !== selectedId) onSelect(team);
    }
    onCommit?.(team);
  }
  return (
    <fieldset className={css.grid} id={id}>
      <legend className={css.legend}>Equipo</legend>
      <div className={css.tiles}>
        {teams.map((team) => {
          const selected = team.id === selectedId;
          return (
            <label
              className={css.tile}
              data-selected={selected || undefined}
              key={team.id}
              onPointerDown={() => {
                pointer.current = true;
              }}
              title={team.name}
            >
              <input
                aria-label={team.name}
                checked={selected}
                className={css.radio}
                name="team"
                onChange={() => onSelect(team)}
                onClick={() => {
                  // Arrow keys also fire `click` on radios; only a pointer confirms here.
                  if (pointer.current) onCommit?.(team);
                  pointer.current = false;
                }}
                onKeyDown={(event) => confirmKey(event, team)}
                type="radio"
                value={team.id}
              />
              <TeamCrest size={30} team={team} />
              <span className={css.name}>{team.shortName}</span>
              {selected ? <Check aria-hidden="true" className={css.check} size={14} /> : null}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
