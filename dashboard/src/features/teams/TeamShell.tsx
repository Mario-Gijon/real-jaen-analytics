"use client";

import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type { TeamRef } from "@/contract";
import { Button } from "@/ui/Button";
import { TeamCrest } from "@/ui/TeamCrest";
import { tabId, tabPanelId, Tabs } from "@/ui/Tabs";
import { CLUB_ID } from "@/lib/club";
import { teamsHref } from "@/lib/navigation";
import { TeamCrestGrid } from "./TeamCrestGrid";
import css from "./TeamsView.module.css";

/*
 * What «Equipos» draws and keeps the same in both data modes: the crest grid,
 * the chosen club's header and tabs, and the view kept in the URL. No data
 * is loaded here.
 */

export type Tab = "performance" | "players";
/** What the «Equipos» URL asks for (`teamsInitialView`). */
export type Props = { initialTeamId?: string; initialSeasonId?: string; initialTab?: Tab };
export const TABS = [
  { id: "performance", label: "Rendimiento del equipo" },
  { id: "players", label: "Jugadores" },
] as const;
export const TABS_PREFIX = "team";
/** Same breakpoint as the phone layout in TeamsView.module.css. */
const PHONE = "(max-width: 640px)";

/**
 * Keeps the team, season and tab chosen (in the URL or on the page) in the
 * URL (`teamsHref`), so the browser's Back, a reload or a shared link open the
 * same view; what was never chosen stays out, as its default (the club, the
 * first season, Rendimiento). The URL is replaced: no navigation and no
 * history entry per choice. `null` while nothing is shown (loading, or an
 * explicit state for an unknown team or season): the URL keeps what was asked
 * for.
 */
export function useTeamsUrl(view: Parameters<typeof teamsHref>[0] | null) {
  const href = view ? teamsHref(view) : null;
  useEffect(() => {
    if (href && `${window.location.pathname}${window.location.search}` !== href) {
      window.history.replaceState(window.history.state, "", href);
    }
  }, [href]);
}

/** Club first, then alphabetical: quick to scan for a name. */
export function gridOrder(teams: readonly TeamRef[]): TeamRef[] {
  return [...teams].sort(
    (a, b) =>
      Number(b.id === CLUB_ID) - Number(a.id === CLUB_ID) ||
      a.shortName.localeCompare(b.shortName, "es"),
  );
}

/**
 * The crest grid (folded on phones once a club is chosen), the chosen club's
 * header and its two tabs, whatever the source of what the tabs show.
 */
export function TeamShell({ teams, team, subtitle, tab, onTab, onSelectTeam, busy, children }: {
  teams: readonly TeamRef[]; team: TeamRef; subtitle: string; tab: Tab; onTab: (tab: Tab) => void;
  onSelectTeam: (teamId: string) => void; busy: boolean; children: ReactNode;
}) {
  // Phones fold the grid while a club is shown; desktop always shows it (CSS).
  const [gridOpen, setGridOpen] = useState(false);
  const gridId = useId();
  const changeButton = useRef<HTMLButtonElement>(null);
  const pendingFocus = useRef<"change" | "grid" | null>(null);
  useEffect(() => {
    const target = pendingFocus.current;
    pendingFocus.current = null;
    if (target === "change") changeButton.current?.focus();
    if (target === "grid") {
      document.getElementById(gridId)?.querySelector<HTMLInputElement>("input:checked")?.focus();
    }
  }, [gridOpen, gridId]);

  return (
    <div className={css.view}>
      <div className={css.picker} data-open={gridOpen || undefined}>
        <TeamCrestGrid
          id={gridId}
          onCommit={() => {
            if (window.matchMedia?.(PHONE).matches) {
              pendingFocus.current = "change";
              setGridOpen(false);
            }
          }}
          onSelect={(next) => {
            onSelectTeam(next.id);
          }}
          selectedId={team.id}
          teams={teams}
        />
      </div>

      <section aria-labelledby="team-name" className={css.team}>
        <header className={css.identity}>
          <TeamCrest size={44} team={team} />
          <div className={css.titles} data-full-name={!subtitle || undefined}>
            <h2 id="team-name">{team.name}</h2>
            {subtitle ? <p>{subtitle}</p> : null}
          </div>
          <Button
            aria-controls={gridId}
            aria-expanded={gridOpen}
            className={css.change}
            icon={<ChevronDown aria-hidden="true" size={16} />}
            onClick={() => {
              pendingFocus.current = gridOpen ? null : "grid";
              setGridOpen(!gridOpen);
            }}
            ref={changeButton}
            size="sm"
          >
            Cambiar equipo
          </Button>
        </header>

        <Tabs
          items={TABS}
          label="Secciones del equipo"
          onSelect={onTab}
          prefix={TABS_PREFIX}
          selected={tab}
        />
        <div
          aria-busy={busy || undefined}
          aria-labelledby={tabId(TABS_PREFIX, tab)}
          className={css.panel}
          id={tabPanelId(TABS_PREFIX)}
          role="tabpanel"
          tabIndex={0}
        >
          {children}
        </div>
      </section>
    </div>
  );
}
