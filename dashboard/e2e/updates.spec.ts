import { expect, test, type Page } from "@playwright/test";
import { REAL_SOURCE } from "./sources";

/*
 * Update control (docs/contrato-actualizaciones.md) with simulated /api/v1/updates answers: the e2e server is
 * the demonstration, where updates do not exist. The full flow with the real backend and the local provider is
 * validated separately (api/docker/acquisition/test_updates.py).
 */

const SCOPE = { kind: "pilot", label: "Actualización piloto: calendario de la jornada 1 y un partido", round: 1,
  actionLabel: "Actualizar piloto", description: "Consulta el calendario de la jornada 1 y un partido. No actualiza el resto de la jornada ni de la temporada: los demás partidos se conservan tal como están publicados.",
  matchIds: ["sofascore-match-16599363"] };
const job = (over: Record<string, unknown> = {}) => ({
  id: "7f1c2a3b-4d5e-4f60-8a1b-2c3d4e5f6a7b", status: "running", phase: "acquiring", cancellable: true, cancelRequested: false,
  published: false, requestedAt: "2026-10-28T17:00:00Z", finishedAt: null, pollAfterSeconds: 1, error: null, result: null, ...over,
});
const completed = job({ status: "completed", phase: "finished", cancellable: false, published: true,
  finishedAt: "2026-10-28T17:02:00Z", result: { publishedAt: "2026-10-28T17:02:00Z", reusedBundle: false, coverage: null } });

/** Simulated update routes; `available: false` is an installation without updates. */
async function serveUpdates(page: Page, { available = true } = {}) {
  const state = { started: 0, polls: 0 };
  await page.route("**/api/v1/source", (route) => route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/updates/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/updates/current") {
      const finished = state.polls >= 2;
      return route.fulfill({ json: available
        ? { available: true, scope: SCOPE, lastPublished: finished ? { at: "2026-10-28T17:02:00Z" } : null,
          activeJob: state.started && !finished ? job() : null, lastJob: finished ? completed : null, lastCoverage: null }
        : { available: false, scope: null, lastPublished: null, activeJob: null, lastJob: null, lastCoverage: null } });
    }
    state.polls += 1;
    return route.fulfill({ json: state.polls >= 2 ? completed : job({ phase: "publishing", cancellable: false }) });
  });
  await page.route("**/api/v1/updates", (route) => {
    state.started += 1;
    return route.fulfill({ status: 202, json: { job: job(), reused: state.started > 1 } });
  });
  return state;
}

test("the update screen shows the real scope and follows one update to its publication, without a second key", async ({ page }) => {
  const state = await serveUpdates(page);
  await page.goto("/operador");
  await expect(page.getByRole("heading", { name: "Actualización de datos" })).toBeVisible();
  await expect(page.getByText("No actualiza el resto de la jornada ni de la temporada", { exact: false })).toBeVisible();
  await expect(page.getByLabel(/Clave/)).toHaveCount(0);
  const start = page.getByRole("main").getByRole("button", { name: "Actualizar piloto" });
  await expect(start).toBeVisible();
  await expect(page.getByRole("banner").getByRole("button", { name: /Actualizar piloto: Actualización piloto/ })).toBeVisible();
  await start.dblclick();
  // The top bar follows the job started from this screen.
  await expect(page.getByRole("banner").getByRole("button", { name: /^Cancelar|Recargar datos/ })).toBeVisible();
  await expect(page.getByRole("main").getByText(/Publicado el 28\/10/)).toBeVisible({ timeout: 10_000 });
  await expect(page.getByRole("main").getByRole("button", { name: "Recargar datos" })).toBeVisible();
  expect(state.started).toBe(1);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("the top bar shows the update control only where updates exist, labelled as the pilot", async ({ page }) => {
  await serveUpdates(page, { available: false });
  await page.goto("/teams");
  await expect(page.getByRole("banner").getByRole("button", { name: /Actualizar/ })).toHaveCount(0);
  await page.unrouteAll();
  await serveUpdates(page);
  await page.goto("/teams");
  const control = page.getByRole("banner").getByRole("button", { name: /Actualizar piloto: Actualización piloto/ });
  await expect(control).toBeVisible();
  await expect(control).toHaveText("Actualizar");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("a group update that published with pending resources says so and keeps new, kept and pending apart", async ({ page }) => {
  const group = { kind: "group", label: "Actualización del Grupo 2 (Primera Federación 26/27)", round: null, matchIds: [],
    actionLabel: "Actualizar Grupo 2", description: "Consulta los partidos finalizados que faltan, los aplazados y las correcciones recientes." };
  const coverage = { newResources: 35, keptResources: 608, deferredMatchIds: [], restrictedMatchIds: [], detailRounds: null,
    newMatches: [], matchNames: { "sofascore-match-16599967": "Real Jaén – CE Europa" },
    pending: [{ matchId: "sofascore-match-16599967", resources: [{ resource: "statistics", reason: "empty", kept: false }] }] };
  const done = job({ status: "completed", phase: "finished", cancellable: false, published: true, finishedAt: "2026-10-28T17:02:00Z",
    result: { publishedAt: "2026-10-28T17:02:00Z", reusedBundle: false, coverage } });
  await page.route("**/api/v1/source", (route) => route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/updates/**", (route) => route.fulfill({ json: { available: true, scope: group,
    lastPublished: { at: "2026-10-28T17:02:00Z" }, activeJob: null, lastJob: done } }));
  await page.goto("/operador");
  const main = page.getByRole("main");
  await expect(main.getByText(/^Actualización completada con pendientes/)).toBeVisible();
  await expect(main.getByText("35", { exact: true })).toBeVisible();
  await expect(main.getByRole("link", { name: "Real Jaén – CE Europa" })).toHaveAttribute("href", "/matches/sofascore-match-16599967");
  await expect(main.getByText("estadísticas (respuesta sin datos, sin datos publicados)")).toBeVisible();
  await expect(page.getByRole("banner").getByRole("button", { name: /Actualizar Grupo 2/ })).toHaveText("Actualizar");
  await expect(main.getByText(/piloto/)).toHaveCount(0);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});

test("after a refused update, the last publication's deferred refreshes stay visible with their data served", async ({ page }) => {
  const group = { kind: "group", label: "Actualización del Grupo 2 (Primera Federación 26/27)", round: null, matchIds: [],
    actionLabel: "Actualizar Grupo 2", description: "Consulta los partidos finalizados que faltan, los aplazados y las correcciones recientes." };
  const lastCoverage = { newResources: 12, keptResources: 631, pending: [], newMatches: [],
    deferredMatchIds: ["sofascore-match-16599970"], restrictedMatchIds: [], detailRounds: null, matchNames: { "sofascore-match-16599970": "Real Jaén – CE Europa" } };
  const failed = job({ status: "failed", phase: "finished", cancellable: false, finishedAt: "2026-10-28T18:00:00Z",
    error: { code: "ACQUISITION_FAILED", message: "La fuente rechazó la consulta", retryable: false } });
  await page.route("**/api/v1/source", (route) => route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/updates/**", (route) => route.fulfill({ json: { available: true, scope: group,
    lastPublished: { at: "2026-10-28T17:02:00Z" }, activeJob: null, lastJob: failed, lastCoverage } }));
  await page.goto("/operador");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: /^Última publicación/ })).toBeVisible();
  await expect(main.getByRole("heading", { name: "Revisiones aplazadas" })).toBeVisible();
  await expect(main.getByText(/Se siguen sirviendo sus datos anteriores/)).toBeVisible();
  await expect(main.getByRole("link", { name: "Real Jaén – CE Europa" })).toHaveAttribute("href", "/matches/sofascore-match-16599970");
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);
});
