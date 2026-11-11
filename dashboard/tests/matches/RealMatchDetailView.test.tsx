import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, getAllEnrichedComments, getCompetitions, getMatch, getObservedMatchDetail, getObservedSquad } from "@/client/api";
import type { EnrichedCommentsPage, ObservedGoal, ObservedLineupPlayer, ObservedMatchDetail, ObservedSquad, PlayerRef, TeamMatchStats } from "@/contract";
import { RealMatchDetailView } from "@/features/matches/RealMatchDetailView";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getSource: vi.fn(), getObservedMatchDetail: vi.fn(), getObservedSquad: vi.fn(), getMatch: vi.fn(), getCompetitions: vi.fn(), getAllEnrichedComments: vi.fn(),
}));

const missing = { value: null, reason: "not_available" } as const;
const stats = (values: Partial<Record<keyof TeamMatchStats, number>>): TeamMatchStats => ({
  possession: missing, shots: missing, shotsOnTarget: missing, xg: missing, corners: missing,
  fouls: missing, yellowCards: missing, redCards: missing,
  ...Object.fromEntries(Object.entries(values).map(([k, v]) => [k, { value: v }])),
});
const SCOPE = "Partido, estadísticas colectivas, alineaciones e incidencias según la fuente. Un dato que la fuente no da se muestra como «—».";
const notStored = { state: "not_stored", fetchedAt: null } as const;
type Sections = Partial<Pick<ObservedMatchDetail, "statistics" | "lineups" | "incidents">>;
const detail = (sections: Sections = {}, match: Partial<ObservedMatchDetail["match"]> = {}): ObservedMatchDetail => ({
  match: { id: "sofascore-match-16599363", kickoff: "2026-08-30T17:15:00Z", status: "finished",
    competition: "Primera Federacion, Group 2", season: "26/27", round: 1,
    home: { id: "sofascore-team-135672", name: "CE Europa" }, away: { id: "team-real-jaen", name: "Real Jaén" },
    score: { home: 0, away: 3 }, ...match },
  statistics: notStored, lineups: notStored, incidents: notStored, ...sections,
  scope: SCOPE, source: { synthetic: false, label: "Datos reales" },
});
const statistics: ObservedMatchDetail["statistics"] = { state: "stored", fetchedAt: "2026-10-21T12:07:30+00:00",
  home: stats({ possession: 46, xg: 1.81, shots: 15, yellowCards: 0 }), away: stats({ possession: 54, xg: 1.6, shots: 13, yellowCards: 1 }) };

const ref = (id: number, name: string): PlayerRef => ({ id: `sofascore-player-${id}`, name, position: null });
const lineupPlayer = (id: number, name: string, fields: Partial<ObservedLineupPlayer> = {}): ObservedLineupPlayer => ({
  player: { id: `sofascore-player-${id}`, name, position: "midfielder" }, shirtNumber: id,
  role: "starter", participation: "confirmed", minutes: { value: 90 }, ...fields,
});
const startersFrom = (first: number) => Array.from({ length: 11 }, (_, i) => lineupPlayer(first + i, `Titular ${first + i}`));
const lineups: ObservedMatchDetail["lineups"] = {
  state: "stored", fetchedAt: "2026-10-21T12:07:31+00:00", confirmed: true,
  home: [...startersFrom(1), lineupPlayer(40, "Suplente Sin Jugar", { role: "substitute", participation: "unknown", minutes: missing }),
    lineupPlayer(41, "Suplente Con Minutos", { role: "substitute", minutes: { value: 5 } }),
    lineupPlayer(42, "Suplente No Utilizado", { role: "substitute", participation: "unused", minutes: { value: 0 } })],
  away: [...startersFrom(50), lineupPlayer(90, "Entra Tarde", { role: "substitute", minutes: { value: 22 } })],
};
const noSeasonStat = { value: null, coverage: "none", reason: "not_available", matches: 0, of: 1 } as const;
const observedSquad = (teamId: string, players: PlayerRef[] = [], season = "26/27"): ObservedSquad => ({
  teamId, scope: "Jugadores observados en la competición y temporada de la fuente.",
  source: { synthetic: false, label: "Datos reales" },
  competitions: players.length ? [{
    id: `sofascore-scope-999-${season}`, name: "LaLiga 2", season,
    matches: { counted: 1, withCallUps: 1 },
    callUps: { total: 1, played: 1, unused: 0, unknown: 0, contradictions: 0, mismatches: 0,
      teamWarnings: 0, duplicateRecords: 0, discardedRecords: 0 },
    players: players.map((player) => ({ player, heightCm: null, preferredFoot: null, lastShirtNumber: null, callUps: 1,
      stats: { appearances: { value: 1, coverage: "complete", matches: 1, of: 1 }, starts: noSeasonStat,
        minutes: noSeasonStat, goals: noSeasonStat, assists: noSeasonStat } })),
  }] : [],
});
const goal = (kind: ObservedGoal["kind"], scorer: PlayerRef | null, assist: PlayerRef | null = null, minute = 30): ObservedGoal =>
  ({ kind, minute, addedTime: null, scorer, assist });
const incidents: ObservedMatchDetail["incidents"] = {
  state: "stored", fetchedAt: "2026-10-21T12:07:31+00:00",
  goals: [
    goal("regular", ref(50, "Titular 50"), ref(51, "Titular 51")),
    goal("penalty", ref(50, "Titular 50"), null, 70),
    goal("ownGoal", ref(2, "Titular 2"), ref(52, "Titular 52")),
    goal("unknown", ref(53, "Titular 53")),
  ],
  substitutions: [{ minute: 68, addedTime: 2, playerIn: ref(90, "Entra Tarde"), playerOut: ref(55, "Titular 55") }],
};

beforeEach(() => {
  vi.resetAllMocks();
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ statistics, lineups, incidents }));
    vi.mocked(getObservedSquad).mockImplementation(async (teamId) => observedSquad(teamId));
});

const metric = (label: string) => screen.getByText(label, { selector: "dt" }).parentElement!;
const values = (label: string) => within(metric(label)).getAllByRole("definition").slice(0, 2).map((d) => d.textContent);
async function openLineups(props: { initialSection?: "summary" | "lineups" } = {}) {
  render(<RealMatchDetailView matchId="sofascore-match-16599363" {...props} />);
  if (!props.initialSection) await userEvent.setup().click(await screen.findByRole("tab", { name: "Alineaciones" }));
  return screen.findByRole("tabpanel", { name: "Alineaciones" });
}
const row = (panel: HTMLElement, name: string) => within(panel).getAllByText(name, { exact: true })[0].closest("li")!;

describe("Real match detail: Resumen", () => {
  it("shows the header without competition nor provenance block, and in Resumen the team stats from the source, unknown values as «—»", async () => {
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(await screen.findByRole("heading", { level: 1, name: "CE Europa contra Real Jaén" })).toBeInTheDocument();
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual(["Resumen", "Alineaciones", "Eventos"]);
    expect(screen.getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
    // Competition and season stay in the data, not on screen; the round and date do.
    expect(screen.queryByText(/Primera Federacion/)).toBeNull();
    expect(screen.getByText("Jornada 1")).toBeVisible();
    expect(screen.getByText("0 – 3")).toBeVisible();
    expect(screen.queryByText("Datos reales")).toBeNull();
    expect(screen.queryByText(SCOPE)).toBeNull();
    // Each team named above its column, «Local»/«Visitante» beneath.
    expect(screen.getByRole("link", { name: "CE Europa" })).toHaveAttribute("href", "/teams?team=sofascore-team-135672");
    expect(screen.getByRole("link", { name: "Real Jaén" })).toHaveAttribute("href", "/teams?team=team-real-jaen");
    expect(screen.getAllByText("· Local").length).toBeGreaterThan(0);
    expect(screen.getAllByText("· Visitante").length).toBeGreaterThan(0);
    expect(values("Posesión")).toEqual(["46 %", "54 %"]);
    expect(values("xG")).toEqual(["1,81", "1,60"]);
    expect(values("Amarillas")).toEqual(["0", "1"]);
    // Red cards the source does not give stay unknown, never 0.
    expect(values("Rojas")).toEqual(["—", "—"]);
    expect(screen.getByText(/Obtenidas de la fuente el 21 oct 2026/)).toBeVisible();
    expect(getMatch).not.toHaveBeenCalled();
    expect(getCompetitions).not.toHaveBeenCalled();
  });

  it("moves between tabs with the keyboard, like the synthetic detail", async () => {
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    const summary = await screen.findByRole("tab", { name: "Resumen" });
    summary.focus();
    const user = userEvent.setup();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Alineaciones" })).toHaveFocus();
    expect(screen.getByRole("tabpanel", { name: "Alineaciones" })).toBeVisible();
    await user.keyboard("{Home}");
    expect(screen.getByRole("tabpanel", { name: "Resumen" })).toBeVisible();
  });

  it("shows the unified event tab and links a structured player back to their real profile", async () => {
    const page: EnrichedCommentsPage = {
      enrichmentVersion: "rules-v1", total: 1, limit: 1, offset: 0,
      items: [{ id: "event-1", captureId: 15, position: 1, text: "Tarjeta amarilla para Titular 50.",
        originalType: "yellowCard", minute: 41, periodName: "1ST", isHome: true, score: { home: 0, away: 1 },
        category: "discipline", attributes: {}, participants: [{ sourceRole: "player", role: "booked_player",
          normalizerRole: "player", sourceId: 50, name: "Titular 50", player: { id: "sofascore-player-50", name: "Titular 50", position: null } }] }],
    };
    vi.mocked(getAllEnrichedComments).mockResolvedValue(page);
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Eventos" }));
    const eventList = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    expect(eventList).toHaveTextContent("Tarjeta amarilla para Titular 50.");
    expect(await screen.findByRole("link", { name: "Titular 50" })).toHaveAttribute("href",
      "/players/sofascore-player-50?view=profile&from=observedMatch&origin=sofascore-match-16599363");
  });

  it("uses a unique structured lineup identity when an event participant has no public player ID", async () => {
    const matchLineups: ObservedMatchDetail["lineups"] = {
      ...lineups,
      away: [...lineups.away, lineupPlayer(2137657, "Cheikh Tidiane Thiam")],
    };
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ lineups: matchLineups }, { id: "sofascore-match-16600019" }));
    vi.mocked(getAllEnrichedComments).mockResolvedValue({
      enrichmentVersion: "rules-v1", total: 1, limit: 1, offset: 0,
      items: [{ id: "handball-88", captureId: 22, position: 1, text: "Mano de Cheikh Thiam (Villarreal II).",
        originalType: "freeKickLost", minute: 88, periodName: "2ND", isHome: false, score: { home: 1, away: 2 },
        category: "foul", attributes: {}, participants: [{ sourceRole: "player", role: "foul_committer",
          normalizerRole: "player", sourceId: null, name: "Cheikh Thiam", player: null }] }],
    });
    render(<RealMatchDetailView matchId="sofascore-match-16600019" />);
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Eventos" }));
    const eventList = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    expect(eventList).toHaveTextContent("Mano de Cheikh Thiam (Villarreal II).");
    expect(await screen.findByRole("link", { name: "Cheikh Thiam" })).toHaveAttribute("href",
      "/players/sofascore-player-2137657?view=profile&from=observedMatch&origin=sofascore-match-16600019");
    await userEvent.setup().selectOptions(screen.getByLabelText("Jugador"), "sofascore-player-2137657");
    expect(eventList.children).toHaveLength(1);
  });

  it("finds Oscar Ureña in the matching season squad when the event text is his only mention", async () => {
    const oscar = ref(1134431, "Oscar Ureña");
    const homeId = "sofascore-team-2815";
    const awayId = "sofascore-team-24338";
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ lineups }, {
      id: "sofascore-match-16600019", competition: "LaLiga 2", season: "26/27",
      home: { id: homeId, name: "Real Zaragoza" }, away: { id: awayId, name: "Villarreal II" },
    }));
    vi.mocked(getObservedSquad).mockImplementation(async (teamId) => observedSquad(teamId, teamId === homeId ? [oscar] : []));
    vi.mocked(getAllEnrichedComments).mockResolvedValue({
      enrichmentVersion: "rules-v1", total: 1, limit: 1, offset: 0,
      items: [{ id: "substitution-59", captureId: 96, position: 1,
        text: "Cambio en Real Zaragoza, ingresa al campo Pau Sans sustituyendo a Óscar Ureña.",
        originalType: "substitution", minute: 59, periodName: "2ND", isHome: true, score: { home: 1, away: 2 },
        category: "substitution", attributes: {}, participants: [{ sourceRole: "player", role: "incoming_player",
          normalizerRole: null, sourceId: 1469468, name: "Pau Sans", player: { id: "sofascore-player-1469468", name: "Pau Sans", position: null } }] }],
    });
    render(<RealMatchDetailView matchId="sofascore-match-16600019" initialSection="comments" />);
    const eventList = await screen.findByRole("list", { name: "Eventos en orden cronológico" });
    expect(await screen.findByRole("link", { name: "Óscar Ureña" })).toHaveAttribute("href",
      "/players/sofascore-player-1134431?view=profile&from=observedMatch&origin=sofascore-match-16600019");
    expect(getObservedSquad).toHaveBeenCalledWith(homeId, expect.anything());
    expect(getObservedSquad).toHaveBeenCalledWith(awayId, expect.anything());
    await userEvent.setup().selectOptions(screen.getByLabelText("Jugador"), "sofascore-player-1134431");
    expect(eventList.children).toHaveLength(1);
  });

  it("does not use a squad observation from a different season to identify an event mention", async () => {
    const oscar = ref(1134431, "Oscar Ureña");
    const homeId = "sofascore-team-2815";
    const awayId = "sofascore-team-24338";
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ lineups }, {
      id: "sofascore-match-16600019", competition: "LaLiga 2", season: "26/27",
      home: { id: homeId, name: "Real Zaragoza" }, away: { id: awayId, name: "Villarreal II" },
    }));
    vi.mocked(getObservedSquad).mockImplementation(async (teamId) =>
      observedSquad(teamId, teamId === homeId ? [oscar] : [], "25/26"));
    vi.mocked(getAllEnrichedComments).mockResolvedValue({
      enrichmentVersion: "rules-v1", total: 1, limit: 1, offset: 0,
      items: [{ id: "text-only-oscar", captureId: 96, position: 1, text: "Falta sobre Óscar Ureña.",
        originalType: "freeKickWon", minute: 43, periodName: "1ST", isHome: true, score: { home: 1, away: 2 },
        category: "foul", attributes: {}, participants: [] }],
    });
    render(<RealMatchDetailView matchId="sofascore-match-16600019" initialSection="comments" />);
    const text = await screen.findByText("Falta sobre Óscar Ureña.");
    expect(text.querySelector("a")).toBeNull();
    expect(screen.queryByRole("option", { name: "Oscar Ureña" })).not.toBeInTheDocument();
  });

  it("distinguishes statistics not stored, stored empty and a failed query", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({}, { status: "scheduled", score: null, kickoff: "2026-11-07T15:30:00Z" }));
    const view = render(<RealMatchDetailView matchId="sofascore-match-16599386" />);
    expect(await screen.findByText("La fuente no tiene estadísticas guardadas de este partido.")).toBeVisible();
    expect(screen.getByText("Programado")).toBeVisible();
    expect(screen.queryByText("0 – 0")).toBeNull();
    view.unmount();

    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ statistics: { state: "empty", fetchedAt: "2026-10-21T12:07:30+00:00" } }));
    const emptyView = render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(await screen.findByText("La fuente guardó las estadísticas de este partido sin contenido.")).toBeVisible();
    emptyView.unmount();

    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce(detail({ statistics: { state: "error", fetchedAt: null } }));
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    const panel = await screen.findByRole("tabpanel", { name: "Resumen" });
    expect(within(panel).getByRole("alert")).toHaveTextContent("No se pudieron cargar las estadísticas de este partido.");
    expect(screen.getByText("0 – 3")).toBeVisible();
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ statistics }));
    await userEvent.setup().click(within(panel).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText(/Obtenidas de la fuente/)).toBeVisible();
  });

  it("says a match the real source does not know is not found, without trying the synthetic detail", async () => {
    vi.mocked(getObservedMatchDetail).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "No"));
    render(<RealMatchDetailView matchId="match-r01-01" />);
    expect(await screen.findByRole("heading", { name: "Partido no encontrado en la fuente real" })).toBeVisible();
    expect(getMatch).not.toHaveBeenCalled();
  });

  it("shows a failure of the real source with retry", async () => {
    vi.mocked(getObservedMatchDetail).mockRejectedValueOnce(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "No"));
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(await screen.findByRole("heading", { name: "No se pudo cargar el partido" })).toBeVisible();
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByText("0 – 3")).toBeVisible();
  });

  it("returns to the list by default and to the real profile it was opened from", async () => {
    const { unmount } = render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(screen.getByRole("link", { name: "‹ Volver a partidos" })).toHaveAttribute("href", "/matches");
    unmount();
    const profile = "/players/sofascore-player-7?view=profile&from=team&origin=team-real-jaen";
    const { unmount: unmount2 } = render(<RealMatchDetailView matchId="sofascore-match-16599363" playerReturnHref={profile} />);
    expect(screen.getByRole("link", { name: "‹ Volver al jugador" })).toHaveAttribute("href", profile);
    expect(screen.queryByRole("link", { name: "‹ Volver a partidos" })).toBeNull();
    unmount2();
    const { unmount: unmount3 } = render(<RealMatchDetailView matchId="sofascore-match-16599363" homeReturnHref="/?match=sofascore-match-16599973" />);
    expect(screen.getByRole("link", { name: "‹ Volver a Inicio" })).toHaveAttribute("href", "/?match=sofascore-match-16599973");
    expect(screen.queryByRole("link", { name: "‹ Volver a partidos" })).toBeNull();
    unmount3();
    render(<RealMatchDetailView matchId="sofascore-match-16599363" teamsReturnHref="/teams?team=sofascore-team-24265" />);
    expect(screen.getByRole("link", { name: "‹ Volver al equipo" })).toHaveAttribute("href", "/teams?team=sofascore-team-24265");
    expect(screen.queryByRole("link", { name: "‹ Volver a partidos" })).toBeNull();
    // A profile opened from these line-ups still returns to them, not to the previous profile.
    await userEvent.setup().click(await screen.findByRole("tab", { name: "Alineaciones" }));
    expect(screen.getByRole("link", { name: "Titular 50" })).toHaveAttribute("href",
      "/players/sofascore-player-50?view=profile&from=observedMatch&origin=sofascore-match-16599363&originTab=lineups");
  });

  it("says a local replacement is a local decision and links to the other event with the same origin", async () => {
    const decided = { reason: "Aplazado y jugado con otro id.", decidedBy: "Analista", decidedOn: "2026-10-22" };
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({}, { id: "sofascore-match-16599956", status: "postponed", score: null }));
    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce({ ...detail({}, { id: "sofascore-match-16599956", status: "postponed", score: null }),
      localReplacement: { replacedBy: { matchId: "sofascore-match-17118209", ...decided }, replaces: [] } });
    const origin = { view: "observed", from: "teams", team: "sofascore-team-24329" };
    const { unmount } = render(<RealMatchDetailView matchId="sofascore-match-16599956" teamsReturnHref="/teams?team=sofascore-team-24329" originQuery={origin} />);
    expect(await screen.findByText("Decisión local")).toBeVisible();
    expect(screen.getByText(/está sustituido por otro y no cuenta como encuentro vigente ni pendiente\. Aplazado y jugado con otro id\./)).toBeVisible();
    expect(screen.getByText(/No es un dato de la fuente: SofaScore no enlaza los dos eventos\./)).toBeVisible();
    expect(screen.getByRole("link", { name: "Ver el encuentro vigente" }))
      .toHaveAttribute("href", "/matches/sofascore-match-17118209?from=teams&team=sofascore-team-24329");
    unmount();

    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce({ ...detail(),
      localReplacement: { replacedBy: null, replaces: [{ matchId: "sofascore-match-16599956", ...decided }] } });
    const { unmount: second } = render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(await screen.findByText(/sustituye a un evento anterior, que se conserva sin contar en el calendario/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Ver el evento sustituido" })).toHaveAttribute("href", "/matches/sofascore-match-16599956");
    second();

    // Without a local decision, nothing is said.
    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce(detail());
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(await screen.findByText("0 – 3")).toBeVisible();
    expect(screen.queryByText("Decisión local")).toBeNull();
  });

});

describe("Real match detail: Alineaciones", () => {
  it("lists «Once inicial» and «Banquillo» per team with shirt number and position, like the synthetic detail", async () => {
    const panel = await openLineups();
    // Confirmed line-ups need no note.
    expect(within(panel).queryByText(/Alineaciones confirmadas/)).toBeNull();
    const home = within(panel).getByRole("region", { name: "CE Europa" });
    expect(within(home).getByRole("list", { name: "Once inicial de CE Europa" }).children).toHaveLength(11);
    expect(within(home).getByRole("list", { name: "Banquillo de CE Europa" }).children).toHaveLength(3);
    expect(row(panel, "Titular 1")).toHaveTextContent("1Titular 1MED");
    expect(within(panel).queryByText(/4-4-2|Formación/)).toBeNull();
    // Every listed player opens their real profile, never a synthetic season.
    const links = within(panel).getAllByRole("link");
    expect(links.filter((link) => link.getAttribute("href")!.startsWith("/players/sofascore-player-")).length).toBeGreaterThan(20);
    expect(links.filter((link) => link.getAttribute("href")!.startsWith("/teams?")).length).toBe(2);
  });

  it("marks goals by kind, named assists and recorded substitutions, never from minutes", async () => {
    const panel = await openLineups();
    const scorer = row(panel, "Titular 50");
    expect(within(scorer).getByRole("img", { name: "2 goles (1 de penalti)" })).toBeVisible();
    expect(within(row(panel, "Titular 51")).getByRole("img", { name: "1 asistencia" })).toBeVisible();
    // An own goal is not an ordinary goal, and its assist is not credited.
    const own = row(panel, "Titular 2");
    expect(within(own).getByLabelText("1 gol en propia puerta")).toHaveTextContent("p.p.");
    expect(within(own).queryByRole("img", { name: /gol/ })).toBeNull();
    expect(within(row(panel, "Titular 52")).queryByRole("img", { name: /asistencia/ })).toBeNull();
    const unknownKind = row(panel, "Titular 53");
    expect(within(unknownKind).getByRole("img", { name: "1 gol de tipo no indicado por la fuente" })).toBeVisible();
    expect(within(unknownKind).queryByRole("img", { name: /^\d+ goles?($| \()/ })).toBeNull();
    expect(unknownKind).toHaveTextContent("?");
    // Substitution: double arrows with the minute on the player out, entry line on the player in.
    expect(within(row(panel, "Titular 55")).getByRole("img", { name: "Sustituido en el minuto 68+2′" })).toBeVisible();
    const entered = row(panel, "Entra Tarde");
    expect(within(entered).getByLabelText("Entra en el minuto 68+2′")).toBeVisible();
    expect(entered).toHaveTextContent("Sale · Titular 55");
    // Minutes alone never become a substitution, and an unused substitute is not said to have stayed out.
    const withMinutes = row(panel, "Suplente Con Minutos");
    expect(within(withMinutes).queryByLabelText(/Entra/)).toBeNull();
    expect(withMinutes).toHaveTextContent("5′");
    expect(row(panel, "Suplente Sin Jugar")).toHaveTextContent("Participación no confirmada");
    // Unused as the data service resolves it: still listed, with nothing in its incidents area (never «0′»).
    expect(row(panel, "Suplente No Utilizado")).toHaveTextContent(/^42Suplente No UtilizadoMED$/);
    expect(row(panel, "Suplente No Utilizado")).not.toHaveTextContent(/No utilizado|0′/);
    expect(within(row(panel, "Titular 1")).queryByRole("img")).toBeNull();
  });

  it("keeps line-ups and stats when the incidents fail, marks nothing and can retry", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce(detail({ statistics, lineups, incidents: { state: "error", fetchedAt: null } }));
    const panel = await openLineups();
    expect(within(panel).getByRole("alert")).toHaveTextContent("No se pudieron cargar las incidencias: no se marcan goles, asistencias ni cambios.");
    expect(within(panel).getByRole("list", { name: "Once inicial de CE Europa" })).toBeVisible();
    expect(within(row(panel, "Titular 50")).queryByRole("img")).toBeNull();
    expect(row(panel, "Entra Tarde")).toHaveTextContent("22′");
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ statistics, lineups, incidents }));
    await userEvent.setup().click(within(panel).getByRole("button", { name: "Reintentar" }));
    // The retry reloads the detail but stays on Alineaciones.
    const reloaded = await screen.findByRole("tabpanel", { name: "Alineaciones" });
    expect(await within(reloaded).findByRole("img", { name: "2 goles (1 de penalti)" })).toBeVisible();
    await userEvent.setup().click(screen.getByRole("tab", { name: "Resumen" }));
    expect(values("Posesión")).toEqual(["46 %", "54 %"]);
  });

  it("says when the incidents are not stored or stored empty, without marking anything", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ lineups, incidents: { state: "empty", fetchedAt: null } }));
    const panel = await openLineups({ initialSection: "lineups" });
    expect(within(panel).getByText(/La fuente guardó las incidencias de este partido sin contenido/)).toBeVisible();
  });

  it("links names to the real profile, returning to this match's line-ups, only when real profiles are enabled", async () => {
    const panel = await openLineups({ initialSection: "lineups" });
    expect(within(panel).getByRole("link", { name: "Titular 1" })).toHaveAttribute("href",
      "/players/sofascore-player-1?view=profile&from=observedMatch&origin=sofascore-match-16599363&originTab=lineups");
  });

  it("never guesses: unknown role, shirt number and confirmation, and an incomplete side is said so", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ lineups: {
      state: "stored", fetchedAt: null, confirmed: null,
      home: [lineupPlayer(1, "Sin Rol", { role: null, shirtNumber: null, participation: "unknown", minutes: missing })],
      away: [],
    } }));
    const panel = await openLineups({ initialSection: "lineups" });
    expect(within(panel).getByText("La fuente no indica si las alineaciones están confirmadas.")).toBeVisible();
    expect(within(panel).getByText("Alineación incompleta en la fuente: 0 titulares y 1 sin rol informado.")).toBeVisible();
    expect(within(panel).getByRole("list", { name: "Jugadores sin rol informado de CE Europa" })).toHaveTextContent("—Sin Rol");
    expect(within(panel).getByText("La fuente no registra jugadores de este equipo.")).toBeVisible();
  });

  it("says when the line-ups are not stored, as in a pending match", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({}, { status: "scheduled", score: null }));
    const pending = await openLineups({ initialSection: "lineups" });
    expect(within(pending).getByText("La fuente no tiene alineaciones guardadas de este partido.")).toBeVisible();
  });

  it("never adds a goal of unknown kind to the player's ordinary goals", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ lineups, incidents: { state: "stored", fetchedAt: null, substitutions: [],
      goals: [goal("regular", ref(50, "Titular 50")), goal("unknown", ref(50, "Titular 50"), null, 60), goal("unknown", ref(50, "Titular 50"), null, 70)] } }));
    const panel = await openLineups({ initialSection: "lineups" });
    const scorer = row(panel, "Titular 50");
    expect(within(scorer).getByRole("img", { name: "1 gol" })).toBeVisible();
    expect(within(scorer).getByRole("img", { name: "2 goles de tipo no indicado por la fuente" })).toBeVisible();
    // The visible count next to the ball is the ordinary goals only (no «3», no «2» on that icon).
    expect(scorer.querySelector("[title='1 gol']")).toHaveTextContent(/^$/);
    expect(scorer.querySelector("[title='2 goles de tipo no indicado por la fuente']")).toHaveTextContent("? 2");
  });

  it("recovers line-ups after a failed query, keeping header and stats", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce(detail({ statistics, lineups: { state: "error", fetchedAt: null }, incidents }));
    const panel = await openLineups({ initialSection: "lineups" });
    expect(within(panel).getByRole("alert")).toHaveTextContent("No se pudieron cargar las alineaciones de este partido.");
    expect(screen.getByText("0 – 3")).toBeVisible();
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ statistics, lineups, incidents }));
    await userEvent.setup().click(within(panel).getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("list", { name: "Once inicial de CE Europa" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Alineaciones" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Real match detail: sections pending review after a correction of the event", () => {
  const corrected = detail({ statistics, lineups, incidents: { ...incidents, goals: null } }, { score: { home: 0, away: 4 } });
  const scoreReview = { ...corrected, review: { corrections: ["score" as const], sections: ["lineups" as const, "incidents" as const] } };

  it("says the score was updated while some sections are pending review, and names them", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(scoreReview);
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    const note = await screen.findByRole("note", { name: "Secciones pendientes de revisión" });
    expect(note).toHaveTextContent("El marcador se ha actualizado, pero algunas secciones siguen pendientes de revisión.");
    expect(note).toHaveTextContent("Pendientes: alineaciones e incidencias");
    expect(note).toHaveTextContent("Sus goles y asistencias no se usan");
    // Team stats do not depend on the score: still shown.
    expect(values("Posesión")).toEqual(["46 %", "54 %"]);
  });

  it("marks only the substitutions of incidents read before the correction, never their goals or assists", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValue(scoreReview);
    const panel = await openLineups();
    expect(within(panel).getByText(/Las incidencias son anteriores a la corrección del marcador/)).toBeVisible();
    expect(within(row(panel, "Titular 50")).queryByRole("img", { name: /gol/ })).toBeNull();
    expect(within(row(panel, "Titular 51")).queryByRole("img", { name: /asistencia/ })).toBeNull();
    expect(within(row(panel, "Titular 55")).getByRole("img", { name: "Sustituido en el minuto 68+2′" })).toBeVisible();
  });

  it("after a team change, never shows the previous teams' sections as the match's", async () => {
    const outdated = (fetchedAt: string) => ({ state: "outdated" as const, fetchedAt });
    vi.mocked(getObservedMatchDetail).mockResolvedValue({
      ...detail({ statistics: outdated("2026-10-21T12:07:30+00:00"), lineups: outdated("2026-10-21T12:07:31+00:00"),
        incidents: outdated("2026-10-21T12:07:31+00:00") }),
      review: { corrections: ["teams"], sections: ["statistics", "lineups", "incidents"] },
    });
    render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    const note = await screen.findByRole("note", { name: "Secciones pendientes de revisión" });
    expect(note).toHaveTextContent("Los equipos del partido han cambiado y algunas secciones siguen pendientes de revisión.");
    expect(note).toHaveTextContent("Pendientes: estadísticas, alineaciones e incidencias");
    expect(screen.getByText(/Las estadísticas guardadas son de los equipos anteriores/)).toBeVisible();
    expect(screen.queryByText("Posesión")).toBeNull();
    await userEvent.setup().click(screen.getByRole("tab", { name: "Alineaciones" }));
    const panel = await screen.findByRole("tabpanel", { name: "Alineaciones" });
    expect(within(panel).getByText(/Las alineaciones guardadas son de los equipos anteriores/)).toBeVisible();
    expect(within(panel).queryByText("Titular 1")).toBeNull();
  });

  it("drops the notice once the sections are recovered", async () => {
    vi.mocked(getObservedMatchDetail).mockResolvedValueOnce(scoreReview);
    const { unmount } = render(<RealMatchDetailView matchId="sofascore-match-16599363" />);
    expect(await screen.findByRole("note", { name: "Secciones pendientes de revisión" })).toBeVisible();
    unmount();
    vi.mocked(getObservedMatchDetail).mockResolvedValue(detail({ statistics, lineups, incidents }, { score: { home: 0, away: 4 } }));
    const panel = await openLineups();
    expect(screen.queryByRole("note", { name: "Secciones pendientes de revisión" })).toBeNull();
    expect(within(row(panel, "Titular 50")).getByRole("img", { name: "2 goles (1 de penalti)" })).toBeVisible();
  });
});
