import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, getMatch, getPlayer, getPlayerObservedMatches, getPlayerProfile, getSeasonMatches } from "@/client/api";
import { OBSERVED_MATCH_STAT_KEYS, type ObservedPlayerMatch, type ObservedPlayerMatches, type PlayerProfile } from "@/contract";
import { PlayerProfileView } from "@/features/players/PlayerProfileView";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getPlayerProfile: vi.fn(), getPlayerObservedMatches: vi.fn(), getPlayer: vi.fn(), getSeasonMatches: vi.fn(),
  getSource: vi.fn(), getMatch: vi.fn(),
}));

const SCOPE = "Datos personales según la fuente. No indican equipo actual.";
const moha: PlayerProfile = {
  player: { id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder",
    birthDate: "1999-09-18", heightCm: 165, preferredFoot: null, country: "Senegal" },
  scope: SCOPE,
  source: { synthetic: false, label: "Datos reales" },
};
const RETURN = "/teams?season=s&team=team-real-jaen&tab=players";
const field = (label: string) => screen.getByText(label, { selector: "dt" }).nextElementSibling!;

const noMatches: ObservedPlayerMatches = {
  playerId: moha.player.id, matches: [], scope: "Partidos en los que la fuente registra al jugador en la convocatoria.",
  ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" },
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getPlayerObservedMatches).mockResolvedValue(noMatches);
});

async function load(data: PlayerProfile = moha) {
  vi.mocked(getPlayerProfile).mockResolvedValue(data);
  render(<PlayerProfileView playerId={data.player.id} returnHref={RETURN} />);
  await screen.findByRole("heading", { level: 1, name: data.player.name });
}

describe("Real player profile", () => {
  it("shows personal data without the provenance block, and loads nothing else", async () => {
    await load();
    expect(getPlayerProfile).toHaveBeenCalledWith("sofascore-player-966303", expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByText("Centrocampista")).toBeVisible();
    expect(field("Nacimiento")).toHaveTextContent(/^18\/09\/1999 \(\d+ años\)$/);
    expect(field("Estatura")).toHaveTextContent("1,65 m");
    expect(field("País")).toHaveTextContent("Senegal");
    expect(screen.queryByText("Datos reales")).toBeNull();
    expect(screen.queryByText(SCOPE)).toBeNull();
    expect(screen.getByRole("link", { name: "← Volver" })).toHaveAttribute("href", RETURN);
    // No season, team, stats or matches: neither shown nor requested.
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByLabelText(/Dorsal/)).toBeNull();
    expect(screen.queryByText(/Real Jaén|2026\/27|Participación/)).toBeNull();
    expect(getPlayer).not.toHaveBeenCalled();
    expect(getSeasonMatches).not.toHaveBeenCalled();
  });

  it("shows unknown values as «—», never invented", async () => {
    await load({ ...moha, player: { ...moha.player, position: null, birthDate: null, heightCm: null, preferredFoot: null, country: null } });
    for (const label of ["Nacimiento", "Estatura", "Pierna dominante", "País"]) {
      expect(field(label), label).toHaveTextContent(/^—$/);
    }
    expect(screen.getByText("—", { selector: "p" })).toBeVisible();
  });

  it("translates the source country without changing height or preferred foot", async () => {
    await load({ ...moha, player: { ...moha.player, country: "Spain", preferredFoot: "right" } });
    expect(field("País")).toHaveTextContent("España");
    expect(field("Pierna dominante")).toHaveTextContent("Derecha");
    expect(field("Estatura")).toHaveTextContent("1,65 m");
  });

  it("says when the player does not exist, without retry", async () => {
    vi.mocked(getPlayerProfile).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "Jugador no encontrado en la fuente de datos."));
    render(<PlayerProfileView playerId="sofascore-player-1" returnHref={RETURN} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Jugador no encontrado");
    expect(screen.queryByRole("button", { name: "Reintentar" })).toBeNull();
    expect(screen.getByRole("link", { name: "← Volver" })).toHaveAttribute("href", RETURN);
  });

  it("shows the failure of the real source and retries", async () => {
    vi.mocked(getPlayerProfile).mockRejectedValueOnce(
      new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "La fuente de datos real no está disponible."),
    );
    render(<PlayerProfileView playerId={moha.player.id} returnHref={RETURN} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("La fuente de datos real no está disponible.");
    vi.mocked(getPlayerProfile).mockResolvedValue(moha);
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
    expect(getPlayerProfile).toHaveBeenCalledTimes(2);
  });
});

const missing = { value: null, reason: "not_available" } as const;
const noStats = Object.fromEntries(OBSERVED_MATCH_STAT_KEYS.map((key) => [key, missing])) as ObservedPlayerMatch["stats"];
const played: ObservedPlayerMatch = {
  match: { id: "sofascore-match-1", kickoff: "2026-10-18T16:15:00Z", status: "finished",
    competition: "Primera Federacion, Group 2", season: "26/27", round: 10,
    home: { id: "team-real-jaen", name: "Real Jaén" }, away: { id: "sofascore-team-9", name: "Rayo Majadahonda" },
    score: { home: 1, away: 0 } },
  side: "home", role: "starter", position: "forward", participation: "confirmed",
  stats: { ...noStats, minutes: { value: 90 }, goals: { value: 2 }, assists: { value: 0 }, shots: { value: 4 },
    shotsOnTarget: { value: 0 }, xg: { value: 0.454 }, passes: { value: 29 }, passesCompleted: { value: 23 },
    passAccuracy: { value: (23 / 29) * 100, derivation: { source: "calculated", rule: "ratio" } },
    duelsWon: { value: 5 }, rating: { value: 7 } },
};
const benched: ObservedPlayerMatch = {
  match: { ...played.match, id: "sofascore-match-2", kickoff: "2026-09-06T09:30:00Z", season: "25/26", round: 2,
    competition: "Copa del Rey", home: { id: "sofascore-team-7", name: null }, away: { id: "team-real-jaen", name: "Real Jaén" },
    score: { home: 2, away: 2 } },
  side: "away", role: "substitute", position: "midfielder", participation: "unknown", stats: noStats,
};
const keeper: ObservedPlayerMatch = {
  ...played, match: { ...played.match, id: "sofascore-match-3", kickoff: "2026-08-30T17:15:00Z", round: 1 },
  position: "goalkeeper", stats: { ...noStats, minutes: { value: 90 }, passes: { value: 0 }, saves: { value: 4 } },
};
const history = () => screen.getByRole("list", { name: "Histórico de partidos" });
const rowButtons = () => within(history()).getAllByRole("button");
const rowItems = () => within(history()).getAllByRole("listitem");
const detail = () => screen.getByRole("region", { name: "Detalle del partido" });
const metric = (label: string) => within(detail()).getByText(label, { selector: "dt" }).nextElementSibling as HTMLElement;

async function loadMatches(matches: ObservedPlayerMatch[], props: Partial<Parameters<typeof PlayerProfileView>[0]> = {}) {
  vi.mocked(getPlayerObservedMatches).mockResolvedValue({ ...noMatches, matches });
  vi.mocked(getPlayerProfile).mockResolvedValue(moha);
  render(<PlayerProfileView playerId={moha.player.id} returnHref={RETURN} {...props} />);
  await screen.findByRole("list", { name: "Histórico de partidos" });
}

describe("Partidos (real)", () => {
  it("lists compact cards with round, opponent and home–away score, keeping the supplied order and default selection", async () => {
    await loadMatches([benched, played]);
    const [first, second] = rowItems();
    expect(first).toHaveTextContent("J2Equipo sin identificar2–2");
    expect(second).toHaveTextContent("J1090′Rayo Majadahonda1–0");
    expect(within(second).getByLabelText("90 minutos jugados")).toBeVisible();
    expect(within(first).queryByLabelText(/minutos jugados/)).toBeNull();
    expect(within(first).getByRole("img", { name: "Visitante" })).toBeVisible();
    expect(within(second).getByRole("img", { name: "Local" })).toBeVisible();
    expect(within(first).getByRole("link", { name: "Ver equipo Equipo sin identificar" })).toHaveAttribute("href", "/teams?team=sofascore-team-7");
    expect(within(second.firstElementChild as HTMLElement).getByRole("link", { name: "Ver equipo Rayo Majadahonda" }))
      .toHaveAttribute("href", "/teams?team=sofascore-team-9");
    expect(first.querySelector("button a, a button")).toBeNull();
    expect(within(detail()).getAllByText("Local", { exact: true })).toHaveLength(1);
    expect(screen.queryByText("Ir a jornada", { exact: true })).toBeNull();
    expect(screen.queryByText(/No participó/)).toBeNull();
    // Without a selection, the newest confirmed appearance, not the newest call-up.
    expect(within(second).getByRole("button")).toHaveAttribute("aria-pressed", "true");
    expect(within(first).getByRole("button")).toHaveAttribute("aria-pressed", "false");
    expect(within(detail()).getByText("Delantero", { selector: "span" })).toBeVisible();
    expect(within(detail()).getByText("90 min")).toBeVisible();
    expect(screen.queryByText(/Valoración|Copa del Rey|26\/27|25\/26/)).toBeNull();
    expect(screen.queryByText(noMatches.scope)).toBeNull();
    expect(screen.getByRole("combobox", { name: "Ir a jornada" })).toHaveValue(played.match.id);
    expect(getSeasonMatches).not.toHaveBeenCalled();
    expect(getPlayer).not.toHaveBeenCalled();
  });

  it("shows real values, real zeros and missing values apart, with the service's pass accuracy", async () => {
    await loadMatches([played]);
    expect(metric("Goles")).toHaveTextContent(/^2$/);
    expect(metric("Tiros a puerta")).toHaveTextContent(/^0$/);
    expect(metric("xG")).toHaveTextContent(/^0,45$/);
    expect(metric("Pases")).toHaveTextContent(/^29$/);
    // A percentage is a quotient by definition: no mark.
    expect(metric("Precisión de pase")).toHaveTextContent(/^79,3 %$/);
    expect(metric("Duelos ganados")).toHaveTextContent(/^5$/);
    // Keys the source omitted stay unknown although other stats are given.
    for (const label of ["Regates", "Recuperaciones", "Duelos perdidos", "Faltas"]) {
      expect(metric(label), label).toHaveTextContent(/^—$/);
      expect(within(metric(label)).getByLabelText("Dato no disponible")).toBeVisible();
    }
    // Without deduced or contradictory values there is nothing to explain.
    expect(within(detail()).queryByRole("list", { name: "Notas sobre los datos" })).toBeNull();
    // No synthetic-only metric is invented for a real player.
    expect(within(detail()).queryByText(/Amarillas|Rojas|Goles encajados/)).toBeNull();
  });

  it("never computes a pass accuracy the service does not give", async () => {
    await loadMatches([{ ...played, stats: { ...played.stats, passes: { value: 0 }, passesCompleted: { value: 0 }, passAccuracy: missing } }]);
    expect(metric("Precisión de pase")).toHaveTextContent(/^—$/);
    cleanup();
    // Both pass counts known, but no accuracy from the service: none is calculated here.
    await loadMatches([{ ...played, stats: { ...played.stats, passAccuracy: missing } }]);
    expect(metric("Precisión de pase")).toHaveTextContent(/^—$/);
  });

  it("marks a deduced value visibly and for screen readers, and explains it under the stats", async () => {
    // Moha Keita against Rayo Majadahonda: 2 shots, both blocked.
    await loadMatches([{ ...played, stats: { ...played.stats, shots: { value: 2 },
      shotsOnTarget: { value: 0, derivation: { source: "derived", rule: "shots_partition" } } } }]);
    expect(metric("Tiros a puerta")).toHaveTextContent(/^0\* \(deducido\)$/);
    const notes = within(detail()).getByRole("list", { name: "Notas sobre los datos" });
    expect(notes).toBeVisible();
    expect(within(notes).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "* Deducido: la fuente no lo da, pero sus tiros fuera y bloqueados suman todos sus tiros (o no tiró).",
    ]);
    expect(metric("Tiros")).toHaveTextContent(/^2$/);
  });

  it("tells contradictory, conflicting and invalid figures apart from a plain absence", async () => {
    await loadMatches([{ ...played, stats: { ...played.stats, shots: { value: null, reason: "inconsistent" },
      shotsOnTarget: { value: null, reason: "inconsistent" }, fouls: { value: null, reason: "conflicting" },
      dribblesWon: { value: null, reason: "invalid" } } }]);
    expect(within(metric("Tiros")).getByLabelText("Cifras contradictorias en la fuente")).toHaveTextContent("—");
    expect(metric("Tiros")).toHaveTextContent(/^—† \(cifras contradictorias en la fuente\)$/);
    expect(within(metric("Faltas")).getByLabelText("Versiones de la fuente que no coinciden")).toBeVisible();
    expect(within(metric("Regates ganados")).getByLabelText("Valor no válido en la fuente")).toBeVisible();
    expect(within(metric("Regates")).getByLabelText("Dato no disponible")).toBeVisible();
    const notes = within(detail()).getByRole("list", { name: "Notas sobre los datos" });
    expect(within(notes).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "† Cifras contradictorias en la fuente: no se muestra ninguna.",
      "‡ Las versiones de la fuente no coinciden: no se elige ninguna.",
      "§ Valor no válido en la fuente.",
    ]);
  });

  it("keeps all field metrics in uniform rows and the logical order inside each category regardless of position", async () => {
    for (const position of ["defender", "midfielder", "forward"] as const) {
      await loadMatches([{ ...played, position }]);
      expect(within(detail()).getAllByRole("term").map(term => term.textContent)).toEqual([
        "Goles", "Tiros", "Tiros a puerta", "xG", "Recuperaciones", "Pérdidas", "Duelos ganados", "Duelos perdidos",
        "Asistencias", "Pases", "Pases completados", "Precisión de pase", "Regates", "Regates ganados", "Faltas",
      ]);
      for (const term of within(detail()).getAllByRole("term")) {
        expect(term.nextElementSibling?.tagName).toBe("DD");
        expect(term.nextElementSibling?.querySelector("[class*='mainValue']")).toBeNull();
      }
      cleanup();
    }
  });

  it("hides unavailable or uncertain minutes, without converting them to zero", async () => {
    await loadMatches([{ ...played, stats: { ...played.stats, minutes: missing } }, { ...benched, stats: played.stats }]);
    expect(within(history()).queryByLabelText(/minutos jugados/)).toBeNull();
    expect(within(detail()).queryByText(/\d+ min$/)).toBeNull();
  });

  it("uses the match position, not the usual one, to choose the goalkeeper metrics", async () => {
    await loadMatches([keeper]);
    expect(within(detail()).getByText("Portero", { selector: "span" })).toBeVisible();
    expect(metric("Paradas")).toHaveTextContent(/^4$/);
    expect(within(detail()).getAllByRole("term")).toHaveLength(1);
    expect(within(detail()).queryByText("Tiros")).toBeNull();
  });

  it("tells a call-up without resolved participation from an appearance", async () => {
    await loadMatches([benched], { initialMatchId: "sofascore-match-2" });
    expect(within(detail()).getByText(/Los datos no permiten confirmar su participación: sin minutos ni estadísticas/)).toBeVisible();
    expect(within(detail()).getByText("Suplente", { selector: "span" })).toBeVisible();
    expect(within(detail()).queryByText(/Valoración/)).toBeNull();
    expect(within(detail()).queryByRole("term")).toBeNull();
  });

  it("says an unused substitute was not used, without stats", async () => {
    const unused: ObservedPlayerMatch = { ...benched, participation: "unused",
      stats: { ...noStats, minutes: { value: 0 }, goals: { value: 0 }, assists: { value: 0 } } };
    await loadMatches([unused], { initialMatchId: "sofascore-match-2" });
    expect(within(detail()).getByText("Convocado, no utilizado: no entró en el partido.")).toBeVisible();
    expect(within(detail()).getByText("Suplente", { selector: "span" })).toBeVisible();
    expect(within(detail()).getByText("0 min")).toBeVisible();
    expect(within(detail()).queryByRole("term")).toBeNull();
    expect(rowItems()[0]).toHaveTextContent("J20′Equipo sin identificar2–2");
  });

  it("changes the selected match in place and keeps it in the URL without navigating", async () => {
    const replace = vi.spyOn(window.history, "replaceState");
    await loadMatches([played, benched]);
    const second = rowButtons()[1];
    await userEvent.setup().click(second);
    expect(second).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("combobox", { name: "Ir a jornada" })).toHaveValue(benched.match.id);
    expect(within(detail()).getByRole("link", { name: "Equipo sin identificar" })).toHaveAttribute("href", "/teams?team=sofascore-team-7");
    expect(replace).toHaveBeenLastCalledWith(window.history.state, "", expect.stringContaining("match=sofascore-match-2"));
    replace.mockRestore();
  });

  it("synchronizes the jump selector and cards with one selection, distinguishing repeated rounds by opponent and date", async () => {
    const user = userEvent.setup();
    const repeated = { ...benched, match: { ...benched.match, round: played.match.round } };
    await loadMatches([played, repeated]);
    const jump = screen.getByRole("combobox", { name: "Ir a jornada" });
    expect(within(jump).getAllByRole("option").map(option => option.textContent)).toEqual([
      "J10 · Rayo Majadahonda", "J10 · Equipo sin identificar",
    ]);
    await user.selectOptions(jump, benched.match.id);
    expect(rowButtons()[1]).toHaveAttribute("aria-pressed", "true");
    expect(within(detail()).getByText(/Los datos no permiten confirmar su participación/)).toBeVisible();
    expect(window.location.search).toContain(`match=${benched.match.id}`);
    await user.click(rowButtons()[0]);
    expect(jump).toHaveValue(played.match.id);
    expect(metric("Goles")).toHaveTextContent(/^2$/);
  });

  it("adds dates only for repeated round and opponent, distinguishing equal or absent dates too", async () => {
    const duplicate = { ...played, match: { ...played.match, id: "same-round", kickoff: "2026-09-20T12:00:00Z" } };
    const sameDate = { ...played, match: { ...played.match, id: "same-date" } };
    await loadMatches([played, duplicate, sameDate, benched]);
    const labels = within(screen.getByRole("combobox", { name: "Ir a jornada" })).getAllByRole("option").map(option => option.textContent);
    expect(labels).toEqual([
      "J10 · Rayo Majadahonda · 18 oct 2026 · Encuentro 1", "J10 · Rayo Majadahonda · 20 sept 2026",
      "J10 · Rayo Majadahonda · 18 oct 2026 · Encuentro 2", "J2 · Equipo sin identificar",
    ]);
  });

  it("selects the requested match, and says when it is not among the call-ups", async () => {
    await loadMatches([played, benched], { initialMatchId: "sofascore-match-2" });
    expect(rowButtons()[1]).toHaveAttribute("aria-pressed", "true");
    cleanup();
    await loadMatches([played], { initialMatchId: "sofascore-match-404" });
    expect(screen.getByRole("status")).toHaveTextContent("El partido indicado no figura entre los partidos observados de este jugador.");
    expect(rowButtons()[0]).toHaveAttribute("aria-pressed", "false");
    expect(within(detail()).getByText("Selecciona un partido del histórico.")).toBeVisible();
  });

  it("says when the source has no matches for the player", async () => {
    await load();
    expect(await screen.findByText("La fuente no tiene partidos observados de este jugador.")).toBeVisible();
    expect(within(detail()).getByText("Sin partidos observados.")).toBeVisible();
  });

  it("keeps the personal data when the matches fail, and retries only the matches", async () => {
    vi.mocked(getPlayerObservedMatches).mockRejectedValueOnce(
      new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "La fuente de datos real no está disponible."),
    );
    await load();
    expect(await screen.findByRole("alert")).toHaveTextContent("No se pudieron cargar los partidos observados.");
    expect(field("País")).toHaveTextContent("Senegal");
    vi.mocked(getPlayerObservedMatches).mockResolvedValue({ ...noMatches, matches: [played] });
    await userEvent.setup().click(screen.getByRole("button", { name: "Reintentar" }));
    expect(await screen.findByRole("list", { name: "Histórico de partidos" })).toBeVisible();
    expect(getPlayerProfile).toHaveBeenCalledTimes(1);
  });

  it("links «Ver partido completo» to the real detail, carrying this profile's return, never a synthetic detail", async () => {
    await loadMatches([played, benched], { context: { seasonId: "s", origin: { kind: "team", id: "team-real-jaen" } } });
    const link = await within(detail()).findByRole("link", { name: "Ver partido completo" });
    expect(link).toHaveAttribute("href",
      "/matches/sofascore-match-1?from=player&player=sofascore-player-966303&season=s&playerFrom=team&playerOrigin=team-real-jaen");
    expect(getMatch).not.toHaveBeenCalled();
  });
});
