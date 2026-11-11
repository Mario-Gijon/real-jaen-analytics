import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiRequestError, getCompetitions, getMatch, getObservedMatchDetail, getSource } from "@/client/api";
import { MatchView } from "@/features/matches/MatchView";
import { DEMO_SOURCE, REAL_SOURCE } from "../shared/sources";

vi.mock("@/client/api", async (importOriginal) => ({
  ...await importOriginal<typeof import("@/client/api")>(),
  getSource: vi.fn(), getMatch: vi.fn(), getObservedMatchDetail: vi.fn(), getCompetitions: vi.fn(),
}));

const props = { tab: undefined, playerReturnHref: null, teamsReturnHref: null, homeReturnHref: null, originQuery: {} };

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getMatch).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "Partido no encontrado."));
  vi.mocked(getObservedMatchDetail).mockRejectedValue(new ApiRequestError(404, "NOT_FOUND", "Partido no encontrado."));
  vi.mocked(getCompetitions).mockResolvedValue([]);
});

describe("A match page chooses by the API's data mode, never by the link", () => {
  it("in real mode, opens the real detail even for an old synthetic link, which is then not found", async () => {
    vi.mocked(getSource).mockResolvedValue(REAL_SOURCE);
    render(<MatchView matchId="match-r01-01" {...props} />);
    expect(await screen.findByRole("heading", { name: "Partido no encontrado en la fuente real" })).toBeVisible();
    expect(getObservedMatchDetail).toHaveBeenCalledWith("match-r01-01", expect.anything());
    expect(getMatch).not.toHaveBeenCalled();
  });

  it("in the demonstration, opens its own detail even for a real link (view=observed), never asking the real source", async () => {
    vi.mocked(getSource).mockResolvedValue(DEMO_SOURCE);
    render(<MatchView matchId="sofascore-match-16599363" {...props} originQuery={{ view: "observed" }} />);
    expect(await screen.findByRole("heading", { name: "Partido no encontrado" })).toBeVisible();
    expect(getMatch).toHaveBeenCalledWith("sofascore-match-16599363", expect.anything());
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
  });

  it("says why the mode cannot be known, and loads no match", async () => {
    vi.mocked(getSource).mockRejectedValue(new ApiRequestError(503, "UPSTREAM_UNAVAILABLE",
      "Configuración de datos no válida: DATA_MODE=real requiere LOCAL_DATA_API_URL (la URL de la API local de datos)."));
    render(<MatchView matchId="sofascore-match-16599363" {...props} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("DATA_MODE=real requiere LOCAL_DATA_API_URL");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeVisible();
    expect(getMatch).not.toHaveBeenCalled();
    expect(getObservedMatchDetail).not.toHaveBeenCalled();
  });
});
