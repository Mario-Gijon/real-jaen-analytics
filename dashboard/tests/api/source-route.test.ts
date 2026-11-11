// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "@/app/api/v1/source/route";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

const local = () => vi.stubEnv("LOCAL_DATA_API_URL", "http://127.0.0.1:8001");

describe("GET /api/v1/source", () => {
  it("is real by default: every section from the local API, labelled as real data", async () => {
    local();
    const response = GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ synthetic: false, label: "Datos reales", realSections: ["observedPlayers", "matches", "teams"] });
  });

  it("is the demonstration only when DATA_MODE=demo, labelled as such and without any real section", async () => {
    vi.stubEnv("DATA_MODE", "demo");
    local();
    expect(await GET().json()).toEqual({ synthetic: true, label: "Demostración · datos sintéticos", realSections: [] });
  });

  it("says a wrong configuration is one, never falling back to the demonstration", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.stubEnv("DATA_MODE", "sintetico");
    local();
    const invalid = GET();
    expect(invalid.status).toBe(503);
    expect(await invalid.json()).toEqual({ error: { code: "UPSTREAM_UNAVAILABLE",
      message: "Configuración de datos no válida: DATA_MODE=sintetico no es válido: usa real o demo." } });
    vi.stubEnv("DATA_MODE", "real");
    vi.stubEnv("LOCAL_DATA_API_URL", "");
    const withoutUrl = GET();
    expect(withoutUrl.status).toBe(503);
    expect((await withoutUrl.json()).error.message).toContain("DATA_MODE=real requiere LOCAL_DATA_API_URL");
  });

  it("ignores the retired per-section switches and reports them in the log, once", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    local();
    vi.stubEnv("MATCHES_SOURCE", "synthetic");
    vi.stubEnv("TEAMS_SOURCE", "local");
    expect((await GET().json()).synthetic).toBe(false);
    GET();
    expect(warn).toHaveBeenCalledTimes(1);
    expect(warn.mock.calls[0][0]).toContain("MATCHES_SOURCE, TEAMS_SOURCE ya no se usan y se ignoran");
  });
});
