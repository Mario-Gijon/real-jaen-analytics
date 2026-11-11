import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { UpdateJob, UpdateStatus } from "@/contract";
import { OperatorView } from "@/features/updates/OperatorView";
import { UpdateControl } from "@/features/updates/UpdateControl";

const SCOPE = { kind: "pilot" as const, label: "Actualización piloto: calendario de la jornada 1 y un partido", round: 1,
  actionLabel: "Actualizar piloto", description: "Consulta el calendario de la jornada 1 y un partido. No actualiza el resto de la jornada ni de la temporada: los demás partidos se conservan tal como están publicados.",
  matchIds: ["sofascore-match-16599363"] };
const job = (over: Partial<UpdateJob> = {}): UpdateJob => ({
  id: "7f1c2a3b-4d5e-4f60-8a1b-2c3d4e5f6a7b", status: "running", phase: "acquiring", cancellable: true, cancelRequested: false,
  published: false, requestedAt: "2026-10-28T17:00:00Z", finishedAt: null, pollAfterSeconds: 1, error: null, result: null, ...over });
const operatorStatus = (over: Partial<UpdateStatus> = {}): UpdateStatus => ({
  available: true, scope: SCOPE, lastPublished: null, activeJob: null, lastJob: null, lastCoverage: null, ...over });

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown };
function api(handler: Handler) {
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const { status = 200, body = null } = handler(String(input), init);
    return status === 204 ? new Response(null, { status }) : new Response(JSON.stringify(body), { status });
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
const posts = (fetch: ReturnType<typeof api>, path: string) =>
  fetch.mock.calls.filter(([url, init]) => String(url) === path && init?.method === "POST");

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  sessionStorage.clear();
});

describe("UpdateControl (top bar)", () => {
  it("is hidden where updates do not exist (the session that reads the data can update them)", async () => {
    for (const status of [{ available: false, scope: null, lastPublished: null, activeJob: null, lastJob: null, lastCoverage: null }]) {
      const fetch = api(() => ({ body: status }));
      const { container, unmount } = render(<UpdateControl />);
      await waitFor(() => expect(fetch).toHaveBeenCalled());
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });

  it("names the pilot scope, and repeated clicks send a single start with one key", async () => {
    let release: () => void = () => {};
    let started = false;
    const fetch = api((url, init) => {
      if (url === "/api/v1/updates" && init?.method === "POST") return { status: 202, body: { job: job(), reused: false } };
      return { body: operatorStatus(started ? { activeJob: job({ pollAfterSeconds: 30 }) } : {}) };
    });
    render(<UpdateControl />);
    const start = await screen.findByRole("button", { name: /Actualizar piloto: Actualización piloto/ });
    expect(start).toHaveTextContent(/^Actualizar$/);
    expect(screen.queryByText(/jornada completa|temporada/i)).toBeNull();
    const user = userEvent.setup();
    const gate = new Promise<void>((resolve) => { release = resolve; });
    fetch.mockImplementationOnce(async () => {
      await gate;
      started = true;
      return new Response(JSON.stringify({ job: job({ pollAfterSeconds: 30 }), reused: false }), { status: 202 });
    });
    await user.click(start);
    await user.click(start);
    await act(async () => release());
    expect(await screen.findByRole("button", { name: "Cancelar" })).toBeVisible();
    const sent = posts(fetch, "/api/v1/updates");
    expect(sent).toHaveLength(1);
    expect(new Headers(sent[0][1]!.headers).get("Idempotency-Key")).toMatch(/^[0-9a-f-]{36}$/);
    expect(screen.getAllByText("Consultando datos…").length).toBeGreaterThan(0);
  });

  it("follows the job to its publication and offers to reload the data", async () => {
    let polls = 0;
    api((url) => {
      if (url.startsWith("/api/v1/updates/7f1c")) {
        polls += 1;
        return { body: polls < 2 ? job({ phase: "publishing", cancellable: false })
          : job({ status: "completed", phase: "finished", published: true, cancellable: false, finishedAt: "2026-10-28T17:02:00Z",
            result: { publishedAt: "2026-10-28T17:02:00Z", reusedBundle: false, coverage: null } }) };
      }
      const done = job({ status: "completed", phase: "finished", published: true, cancellable: false,
        finishedAt: "2026-10-28T17:02:00Z", result: { publishedAt: "2026-10-28T17:02:00Z", reusedBundle: false, coverage: null } });
      return { body: polls < 2 ? operatorStatus({ activeJob: job() })
        : operatorStatus({ lastJob: done, lastPublished: { at: "2026-10-28T17:02:00Z" } }) };
    });
    render(<UpdateControl />);
    expect(await screen.findByRole("button", { name: "Cancelar" })).toBeEnabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Cancelar" })).toBeDisabled(), { timeout: 3000 });
    expect(await screen.findByRole("button", { name: "Recargar datos" }, { timeout: 4000 })).toBeVisible();
    expect(screen.getAllByText(/Publicado el 28\/10/).length).toBeGreaterThan(0);
  });
});

describe("OperatorView (/operador)", () => {
  it("offers the update to the access session, without a second key, and says what the pilot does not update", async () => {
    const fetch = api(() => ({ body: operatorStatus() }));
    render(<OperatorView />);
    expect(await screen.findByText(/No actualiza el resto de la jornada ni de la temporada/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Ver el partido del piloto" })).toHaveAttribute("href", "/matches/sofascore-match-16599363");
    expect(screen.getByRole("button", { name: "Actualizar piloto" })).toBeVisible();
    expect(screen.queryByLabelText(/Clave/)).toBeNull();
    expect(fetch.mock.calls.map(([url]) => String(url))).not.toContain("/api/v1/session");
  });

  it("shows a failed update as failed, keeping the previous data, and cancels only while consulting", async () => {
    api(() => ({ body: operatorStatus({ lastJob: job({ status: "failed", phase: "finished", cancellable: false,
      error: { code: "ACQUISITION_FAILED", message: "La consulta al proveedor no se completó o fue rechazada.", retryable: true } }) }) }));
    render(<OperatorView />);
    expect(await screen.findByText(/no se completó o fue rechazada\. Se mantienen los datos anteriores\./)).toBeVisible();
    expect(screen.getByRole("button", { name: "Actualizar piloto" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Cancelar" })).toBeNull();
  });

  it("marks the current step and refuses to cancel once it verifies or publishes", async () => {
    api(() => ({ body: operatorStatus({ activeJob: job({ phase: "verifying", cancellable: false, pollAfterSeconds: 30 }) }) }));
    render(<OperatorView />);
    expect(await screen.findByRole("button", { name: "Ya no se puede cancelar" })).toBeDisabled();
    expect(screen.getByText("Comprobando los datos recibidos")).toHaveAttribute("aria-current", "step");
  });
});

describe("Group 2 scope", () => {
  const GROUP = { kind: "group" as const, label: "Actualización del Grupo 2 (Primera Federación 26/27)", round: null, matchIds: [],
    actionLabel: "Actualizar Grupo 2", description: "Consulta los partidos finalizados que faltan y revisa las correcciones de las últimas 72 horas." };
  const coverage = { newResources: 35, keptResources: 608, deferredMatchIds: ["sofascore-match-3"],
    restrictedMatchIds: [] as string[], detailRounds: null as number[] | null,
    newMatches: [{ matchId: "sofascore-match-4", replaces: ["sofascore-match-5"] }],
    pending: [
      { matchId: "sofascore-match-1", resources: [{ resource: "statistics" as const, reason: "empty" as const, kept: false }] },
      { matchId: "sofascore-match-2", resources: [{ resource: "lineups" as const, reason: "error" as const, kept: true }] },
    ],
    comments: { newMatchIds: ["sofascore-match-6"], keptMatchIds: ["sofascore-match-7"],
      emptyMatchIds: ["sofascore-match-10"], pendingMatchIds: ["sofascore-match-8"], failed: [
        { matchId: "sofascore-match-9", reason: "not_found" },
        { matchId: "sofascore-match-11", reason: "transport_error" },
        { matchId: "sofascore-match-12", reason: "budget" },
      ] },
    matchNames: { "sofascore-match-1": "Real Jaén – CE Europa", "sofascore-match-2": "Huesca – UD Ibiza",
      "sofascore-match-4": "Real Murcia – Hércules CF", "sofascore-match-5": "Real Murcia – Hércules CF",
      "sofascore-match-6": "Real Jaén – Algeciras CF", "sofascore-match-7": "Hércules CF – Huesca",
      "sofascore-match-8": "CE Europa – Real Jaén", "sofascore-match-9": "Real Jaén – FC Cartagena",
      "sofascore-match-10": "Atlético Sanluqueño – Real Jaén", "sofascore-match-11": "Real Jaén – FC Cartagena",
      "sofascore-match-12": "Real Jaén – Villarreal B" } };
  const done = job({ status: "completed", phase: "finished", published: true, cancellable: false, finishedAt: "2026-10-28T17:02:00Z",
    result: { publishedAt: "2026-10-28T17:02:00Z", reusedBundle: false, coverage } });

  it("says the update completed with pending items, and keeps new, kept and pending apart", async () => {
    const fetch = api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: done, lastPublished: { at: "2026-10-28T17:02:00Z" } }) }));
    render(<OperatorView />);
    expect(await screen.findByText(/^Actualización completada con pendientes/)).toBeVisible();
    expect(screen.getByText(GROUP.description)).toBeVisible();
    expect(screen.getByText("datos nuevos").parentElement).toHaveTextContent("35 datos nuevos");
    expect(screen.getByText("datos anteriores conservados").parentElement).toHaveTextContent("608 datos anteriores conservados");
    expect(screen.getByText(/partidos con pendientes/).parentElement).toHaveTextContent("2 partidos con pendientes");
    const first = (await screen.findByRole("link", { name: "Real Jaén – CE Europa" })).closest("li")!;
    expect(first).toHaveTextContent("estadísticas (respuesta vacía, sin datos publicados)");
    const second = (await screen.findByRole("link", { name: "Huesca – UD Ibiza" })).closest("li")!;
    expect(second).toHaveTextContent("alineaciones (error temporal de consulta, se mantiene la versión anterior)");
    expect(screen.getByText(/1 revisión queda para\s+la próxima actualización por el límite de consultas\. Se siguen sirviendo sus datos anteriores\./)).toBeVisible();
    expect(screen.getByRole("heading", { name: "Eventos detectados para revisión" })).toBeVisible();
    expect(screen.getByText("Evento externo sofascore-match-3").closest("a")).toBeNull();
    expect(screen.getByText(/No se han incorporado ni consultado sus detalles/)).toBeVisible();
    expect(await screen.findAllByRole("link", { name: "Real Murcia – Hércules CF" })).toHaveLength(2);
    expect(screen.getAllByRole("link", { name: "Real Jaén – CE Europa" })[0].parentElement).toHaveTextContent("SofaScore ID 1");
    expect(screen.getByText(/Solicitud recibida el 28\/10, 18:00/)).toBeVisible();
    expect(screen.getByText(/posible sustitución de/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Actualizar Grupo 2" })).toBeEnabled();
    expect(fetch.mock.calls.every(([url]) => String(url).startsWith("/api/v1/updates"))).toBe(true);  // no data API calls
  });

  it("separates unavailable narrative from actual errors and keeps 404s non-fatal", async () => {
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: done }) }));
    render(<OperatorView />);
    expect(await screen.findByText(/^Actualización completada con pendientes/)).toBeVisible();
    expect(screen.getByText("partidos sin narración disponible").parentElement).toHaveTextContent("2 partidos sin narración disponible");
    expect(screen.getByText("partido con error de comentarios").parentElement).toHaveTextContent("1 partido con error de comentarios");
    const unavailable = screen.getByRole("list", { name: "Partidos sin narración disponible" });
    expect(unavailable).toHaveTextContent("SofaScore respondió 404; se volverá a consultar en futuras actualizaciones");
    expect(unavailable).toHaveTextContent("SofaScore devolvió una lista vacía; se volverá a consultar en futuras actualizaciones");
    expect(unavailable).toHaveTextContent("SofaScore ID 9");
    const commentsPending = screen.getByRole("list", { name: "Partidos pendientes de comentarios narrativos" });
    expect(commentsPending).toHaveTextContent("CE Europa – Real Jaén (SofaScore ID 8): pendiente para una próxima actualización");
    expect(commentsPending).toHaveTextContent("Real Jaén – Villarreal B (SofaScore ID 12): aplazado por el límite de consultas");
    expect(screen.getByText("partidos pendientes de comentarios").parentElement).toHaveTextContent("2 partidos pendientes de comentarios");
    expect(screen.getByRole("list", { name: "Errores de comentarios narrativos" })).toHaveTextContent("Error de transporte");
    expect(screen.getByText(/Los 404 y las listas vacías no bloquean resultados ni estadísticas/)).toBeVisible();
  });

  it("shows an empty HTTP 200 narrative capture as a follow-up item, not an update failure", async () => {
    const emptyOnly = job({ ...done, result: { ...done.result!, coverage: { ...coverage, pending: [], deferredMatchIds: [],
      restrictedMatchIds: [], newMatches: [], comments: { newMatchIds: [], keptMatchIds: [],
        emptyMatchIds: ["sofascore-match-10"], pendingMatchIds: [], failed: [] } } } });
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: emptyOnly }) }));
    render(<OperatorView />);
    expect(await screen.findByText(/^Actualización completada con pendientes/)).toBeVisible();
    expect(screen.getByRole("list", { name: "Partidos sin narración disponible" })).toHaveTextContent("Atlético Sanluqueño – Real Jaén");
    expect(screen.queryByText(/actualización fallida/i)).toBeNull();
  });

  it("labels the top bar with the group's action and the same pending line", async () => {
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: done }) }));
    render(<UpdateControl />);
    expect(await screen.findByRole("button", { name: /Actualizar Grupo 2: Actualización del Grupo 2/ })).toHaveTextContent(/^Actualizar$/);
    expect(screen.getAllByText(/^Actualización completada con pendientes/).length).toBeGreaterThan(0);
  });

  it("keeps the last publication's pending and deferred items visible after a later update failed", async () => {
    const failed = job({ status: "failed", phase: "finished", cancellable: false, finishedAt: "2026-10-28T18:00:00Z",
      error: { code: "ACQUISITION_FAILED", message: "La fuente rechazó la consulta", retryable: false } });
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: failed, lastPublished: { at: "2026-10-28T17:02:00Z" },
      lastCoverage: { ...coverage, matchNames: { ...coverage.matchNames, "sofascore-match-3": "SD Ponferradina – Cultural Leonesa" } } }) }));
    render(<OperatorView />);
    expect(await screen.findByRole("heading", { name: /^Última publicación \(28\/10/ })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Revisiones aplazadas" })).toBeVisible();
    expect(screen.getByRole("link", { name: "SD Ponferradina – Cultural Leonesa" })).toHaveAttribute("href", "/matches/sofascore-match-3");
    expect(screen.getByRole("link", { name: "Huesca – UD Ibiza" }).closest("li")).toHaveTextContent("se mantiene la versión anterior");
  });

  it("does not repeat the last publication when the last job is that publication", async () => {
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: done, lastPublished: { at: "2026-10-28T17:02:00Z" }, lastCoverage: coverage }) }));
    render(<OperatorView />);
    expect(await screen.findByText(/^Actualización completada con pendientes/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: /^Última publicación/ })).toBeNull();
    expect(screen.getAllByRole("heading", { name: "Revisiones aplazadas" })).toHaveLength(1);
  });

  it("lists the matches left outside a restricted update, with their data kept", async () => {
    const restricted = job({ ...done, result: { ...done.result!, coverage: { ...coverage, pending: [], deferredMatchIds: [],
      restrictedMatchIds: ["sofascore-match-2"], detailRounds: [9] } } });
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: restricted }) }));
    render(<OperatorView />);
    expect(await screen.findByRole("heading", { name: "Fuera del alcance de esta actualización" })).toBeVisible();
    expect(screen.getByText(/solo consulta los partidos de la jornada 9\. En 1 partido se detectó/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Huesca – UD Ibiza" })).toHaveAttribute("href", "/matches/sofascore-match-2");
  });

  it("reports a group update without pending items as published", async () => {
    const clean = job({ ...done, result: { ...done.result!, coverage: { ...coverage, pending: [], deferredMatchIds: [], newMatches: [],
      comments: { ...coverage.comments, emptyMatchIds: [], pendingMatchIds: [], failed: [] } } } });
    api(() => ({ body: operatorStatus({ scope: GROUP, lastJob: clean }) }));
    render(<OperatorView />);
    expect(await screen.findByText(/^Publicado el 28\/10/)).toBeVisible();
    expect(screen.queryByText(/con pendientes/)).toBeNull();
  });
});
