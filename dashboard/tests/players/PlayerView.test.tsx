import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, getPlayer, getPlayerObservedMatches, getPlayerProfile, getSource } from "@/client/api";
import { PlayerView } from "@/features/players/PlayerView";
import { DEMO_SOURCE, REAL_SOURCE } from "../shared/sources";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getSource: vi.fn(), getPlayer: vi.fn(), getPlayerProfile: vi.fn(), getPlayerObservedMatches: vi.fn(),
}));

const props = { returnHref: "/teams", origin: null, profileMatchId: null, initialTab: "season" as const, seasonMatchId: null };
const notFound = () => new ApiRequestError(404, "NOT_FOUND", "Jugador no encontrado.");

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getPlayer).mockRejectedValue(notFound());
  vi.mocked(getPlayerProfile).mockRejectedValue(notFound());
  vi.mocked(getPlayerObservedMatches).mockRejectedValue(notFound());
});

describe("A player page chooses by the API's data mode, never by the link", () => {
  it("in real mode, opens the real profile even for an old link to a demonstration season, which is then not found", async () => {
    vi.mocked(getSource).mockResolvedValue(REAL_SOURCE);
    render(<PlayerView playerId="player-0001" seasonId="season-demo-2026-27" {...props} />);
    expect(await screen.findByRole("heading", { name: "Jugador no encontrado" })).toBeVisible();
    expect(getPlayerProfile).toHaveBeenCalledWith("player-0001", expect.anything());
    expect(getPlayer).not.toHaveBeenCalled();
  });

  it("in the demonstration, opens the season view even for a real profile link, never asking the real source", async () => {
    vi.mocked(getSource).mockResolvedValue(DEMO_SOURCE);
    render(<PlayerView playerId="sofascore-player-966303" seasonId="sofascore-scope-94191-97382" {...props} />);
    expect(await screen.findByRole("heading", { name: "Jugador no encontrado en esta temporada" })).toBeVisible();
    expect(getPlayer).toHaveBeenCalledWith("sofascore-player-966303", "sofascore-scope-94191-97382", expect.anything());
    expect(getPlayerProfile).not.toHaveBeenCalled();
    expect(getPlayerObservedMatches).not.toHaveBeenCalled();
  });

  it("says why the mode cannot be known, and loads no player", async () => {
    vi.mocked(getSource).mockRejectedValue(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE", "Configuración de datos no válida: DATA_MODE=x no es válido: usa real o demo."));
    render(<PlayerView playerId="sofascore-player-966303" seasonId={null} {...props} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("DATA_MODE=x no es válido");
    expect(getPlayer).not.toHaveBeenCalled();
    expect(getPlayerProfile).not.toHaveBeenCalled();
  });
});
