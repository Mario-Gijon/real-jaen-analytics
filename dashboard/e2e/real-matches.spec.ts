import { expect, test, type Page } from "@playwright/test";
import { expectEntityAwareRowHover, expectKeyboardFocus } from "./interactive-row";
import { REAL_SOURCE } from "./sources";

/*
 * «Partidos» with the real source. The e2e server is synthetic, so these tests
 * answer the browser's /api/v1 requests for the real section themselves:
 * simulated responses, not the local API.
 */

const jaen = { id: "team-real-jaen", name: "Real Jaén" };
const match = (id: string, fields: Record<string, unknown>) => ({
  id, kickoff: "2026-08-30T17:15:00+00:00", status: "finished", competition: "Primera Federacion, Group 2",
  season: "26/27", round: 1, home: { id: "sofascore-team-1", name: "CE Europa" }, away: jaen,
  score: { home: 0, away: 3 }, ...fields,
});
const MATCHES = [
  match("sofascore-match-1", {}),
  match("sofascore-match-2", { kickoff: "2026-10-03T11:00:00+00:00", round: 6, home: { id: "sofascore-team-2", name: "Hércules CF" }, score: { home: 0, away: 0 } }),
  match("sofascore-match-3", { kickoff: "2026-10-28T19:00:00+00:00", status: "scheduled", round: 9, home: { id: "sofascore-team-3", name: "Antequera CF" }, score: null }),
  match("sofascore-match-4", { kickoff: "2026-11-02T17:15:00+00:00", status: "scheduled", round: 10, home: jaen, away: { id: "sofascore-team-4", name: "Atlético Sanluqueño" }, score: null }),
];
const body = {
  teamId: "team-real-jaen", source: { synthetic: false, label: "Datos reales" },
  scope: "Partidos del equipo que registra la fuente. No es necesariamente un calendario completo.",
  // This version's real source serves a single competition.
  competitions: [{ name: "Primera Federación, Grupo 2", season: "26/27" }],
  matches: MATCHES.map((m) => ({ match: m, side: m.home.id === jaen.id ? "home" : "away" })),
};

/** The real catalog of teams (as «Equipos» gets it) and each one's matches: the club's, and Antequera's single one with it. */
const antequera = { id: "sofascore-team-3", name: "Antequera CF" };
const catalog = { teamId: jaen.id, teams: [antequera, jaen], competitions: [], scope: "", source: REAL_SOURCE };
const listOf = (teamId: string) => ({ ...body, teamId,
  matches: body.matches.filter(({ match }) => match.home.id === teamId || match.away.id === teamId)
    .map(({ match }) => ({ match, side: match.home.id === teamId ? "home" : "away" })) });

async function serveReal(page: Page, { down = false } = {}) {
  await page.route("**/api/v1/source", (route) =>
    route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/teams/team-real-jaen/observed-performance", (route) => route.fulfill({ json: catalog }));
  await page.route("**/api/v1/teams/*/observed-matches", (route) => down
    ? route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "La fuente de datos real no está disponible." } } })
    : route.fulfill({ json: listOf(decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!)) }));
}
const noHorizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("Partidos reales: finished and pending, filters, coverage and links to the real detail only", async ({ page }, testInfo) => {
  await serveReal(page);
  const synthetic: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(competitions|seasons\/)/.test(r.url())) synthetic.push(r.url()); });
  await page.goto("/matches");
  const list = page.getByRole("list", { name: "Partidos reales" });
  await expect(list.getByRole("listitem")).toHaveCount(4);
  // Only Equipo and Estado above; no provenance block, no competition nor season, no competition per row.
  await expect(page.getByRole("combobox", { name: "Equipo" })).toHaveValue("team-real-jaen");
  await expect(page.getByRole("combobox")).toHaveCount(2);
  await expect(page.getByText("Datos reales", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Primera Federaci/)).toHaveCount(0);
  await expect(list.getByRole("listitem").first()).toContainText("26/27 · J1");
  await expect(list.getByRole("listitem").nth(1)).toContainText("0 – 0");
  await expect(list.getByRole("listitem").nth(2)).toContainText("Programado");
  await expect(list.getByRole("listitem").nth(2)).not.toContainText("0 – 0");
  await expect(list.getByRole("link")).toHaveCount(12);
  await expect(list.getByRole("link", { name: /^Ver partido/ }).first()).toHaveAttribute("href", "/matches/sofascore-match-1?from=matches");
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-matches.png"), fullPage: true });

  await page.getByRole("combobox", { name: "Estado" }).selectOption("pending");
  await expect(list.getByRole("listitem")).toHaveCount(2);

  // Another team of the catalog, kept in the URL with the status filter.
  await page.getByRole("combobox", { name: "Equipo" }).selectOption(antequera.id);
  await expect(page).toHaveURL(/\/matches\?team=sofascore-team-3&state=pending$/);
  await expect(list.getByRole("listitem")).toHaveCount(1);
  await expect(page.getByRole("combobox", { name: "Estado" })).toHaveValue("pending");
  // All the teams: the match both lists share appears once.
  await page.getByRole("combobox", { name: "Equipo" }).selectOption("all");
  await page.getByRole("combobox", { name: "Estado" }).selectOption("all");
  await expect(page).toHaveURL(/\/matches\?team=all$/);
  await expect(list.getByRole("listitem")).toHaveCount(4);
  expect(await noHorizontalOverflow(page)).toBe(true);

  // Back from a match detail recovers the team from the URL.
  await page.getByRole("combobox", { name: "Equipo" }).selectOption(antequera.id);
  await list.getByRole("link", { name: /^Ver partido/ }).first().click();
  await expect(page).toHaveURL(/\/matches\/sofascore-match-3\?from=matches&team=sofascore-team-3$/);
  await page.goBack();
  await expect(page.getByRole("combobox", { name: "Equipo" })).toHaveValue(antequera.id);
  await expect(list.getByRole("listitem")).toHaveCount(1);
  expect(synthetic).toEqual([]);
});

test("Selectores reales reservan el canal de la flecha y muestran hover y foco visibles", async ({ page }, testInfo) => {
  await serveReal(page);
  await page.goto("/matches");
  const team = page.getByRole("combobox", { name: "Equipo", exact: true });
  const state = page.getByRole("combobox", { name: "Estado", exact: true });
  for (const select of [team, state]) {
    const metrics = await select.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        height: element.getBoundingClientRect().height,
        paddingLeft: parseFloat(style.paddingLeft),
        paddingRight: parseFloat(style.paddingRight),
        appearance: style.appearance,
        arrow: style.backgroundImage,
      };
    });
    expect(metrics.height).toBe(44);
    expect(metrics.paddingLeft).toBe(12);
    expect(metrics.paddingRight).toBeGreaterThanOrEqual(36);
    expect(metrics.appearance).toBe("none");
    expect(metrics.arrow).not.toBe("none");
  }
  if (testInfo.project.name === "desktop") {
    const desktopViewport = page.viewportSize()!;
    // A 1,440 px desktop at 125% zoom has an effective layout viewport of about 1,152 CSS px.
    await page.setViewportSize({ width: 1152, height: 800 });
    expect(await noHorizontalOverflow(page)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("matches-effective-125-percent.png"), fullPage: true });
    await page.setViewportSize(desktopViewport);
    const baseBorder = await team.evaluate((element) => getComputedStyle(element).borderColor);
    await team.hover();
    await expect.poll(() => team.evaluate((element) => getComputedStyle(element).borderColor)).not.toBe(baseBorder);
  }
  await expectKeyboardFocus(page, state);
});

test("Partidos reales: a team the catalog does not offer falls back to Real Jaén, saying so", async ({ page }) => {
  await serveReal(page);
  await page.goto("/matches?team=team-montealto");
  await expect(page.getByText("El equipo indicado no está disponible en la fuente real: se muestran los partidos del Real Jaén.")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Equipo" })).toHaveValue("team-real-jaen");
  await expect(page.getByRole("list", { name: "Partidos reales" }).getByRole("listitem")).toHaveCount(4);
});

test("a real match's identified rival opens an identity-only team view when it has no export", async ({ page }) => {
  await serveReal(page);
  await page.route("**/api/v1/teams/sofascore-team-1/observed-performance", (route) =>
    route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "No tiene exportación propia." } } }));
  await page.route("**/api/v1/teams/sofascore-team-1", (route) =>
    route.fulfill({ json: { id: "sofascore-team-1", name: "CE Europa" } }));
  await page.goto("/matches");
  const row = page.getByRole("list", { name: "Partidos reales" }).getByRole("listitem").first();
  await expect(row.getByRole("link", { name: "Ver equipo CE Europa" })).toHaveAttribute("href", "/teams?team=sofascore-team-1");
  await expect(row.getByRole("link", { name: /^Ver partido/ })).toHaveAttribute("href", "/matches/sofascore-match-1?from=matches");
  await expect(row.locator("a a")).toHaveCount(0);
  await row.getByRole("link", { name: "Ver equipo CE Europa" }).click();
  await expect(page).toHaveURL("/teams?team=sofascore-team-1");
  await expect(page.getByRole("heading", { level: 2, name: "CE Europa" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("identificado, pero no tiene estadísticas propias disponibles");
  await expect(page.getByLabel("Balance")).toHaveCount(0);
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "CE Europa" })).toBeVisible();
});

test("Partidos reales: an unavailable source shows an error with retry", async ({ page }) => {
  await serveReal(page, { down: true });
  await page.goto("/matches");
  await expect(page.locator("main").getByRole("alert")).toContainText("No se pudieron cargar los partidos reales.");
  await page.unroute("**/api/v1/teams/*/observed-matches");
  await serveReal(page);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("list", { name: "Partidos reales" }).getByRole("listitem")).toHaveCount(4);
});

test("Partidos reales: a synthetic season or rival from a link is not swapped silently", async ({ page }) => {
  await serveReal(page);
  await page.goto("/matches?season=season-demo-2026-27&team=team-montealto&round=all");
  await expect(page.getByText(/La temporada seleccionada no corresponde a la fuente real de partidos/)).toBeVisible();
  await page.getByRole("link", { name: "Ver los partidos reales del Real Jaén" }).click();
  await expect(page.getByRole("list", { name: "Partidos reales" }).getByRole("listitem")).toHaveCount(4);
});

const missing = { value: null, reason: "not_available" };
const side = (values: Record<string, number>) => Object.fromEntries(
  ["possession", "shots", "shotsOnTarget", "xg", "corners", "fouls", "yellowCards", "redCards"].map((k) => [k, k in values ? { value: values[k] } : missing]));
const DETAIL = {
  match: MATCHES[0], scope: "Partido y estadísticas colectivas según la fuente. Un dato que la fuente no da se muestra como «—».",
  source: { synthetic: false, label: "Datos reales" },
  statistics: { state: "stored", fetchedAt: "2026-10-21T12:07:30+00:00",
    home: side({ possession: 46, shots: 15, shotsOnTarget: 4, xg: 1.81, corners: 3, fouls: 13, yellowCards: 0 }),
    away: side({ possession: 54, shots: 13, shotsOnTarget: 5, xg: 1.6, corners: 3, fouls: 5, yellowCards: 1 }) },
  lineups: { state: "not_stored", fetchedAt: null } as Record<string, unknown>,
  incidents: { state: "not_stored", fetchedAt: null } as Record<string, unknown>,
};
const ref = (id: number, name: string) => ({ id: `sofascore-player-${id}`, name, position: null });
const INCIDENTS = {
  state: "stored", fetchedAt: "2026-10-21T12:07:31+00:00",
  goals: [
    { kind: "regular", minute: 20, addedTime: null, scorer: ref(40, "Jaén 40"), assist: ref(41, "Jaén 41") },
    { kind: "penalty", minute: 80, addedTime: null, scorer: ref(40, "Jaén 40"), assist: null },
  ],
  substitutions: [{ minute: 68, addedTime: null, playerIn: ref(966303, "Moha Keita"), playerOut: ref(45, "Jaén 45") }],
};
const lineupPlayer = (id: number, name: string, fields: Record<string, unknown> = {}) => ({
  player: { id: `sofascore-player-${id}`, name, position: "midfielder" }, shirtNumber: id,
  role: "starter", participation: "confirmed", minutes: { value: 90 }, ...fields,
});
const LINEUPS = {
  state: "stored", fetchedAt: "2026-10-21T12:07:31+00:00", confirmed: true,
  home: [...Array.from({ length: 11 }, (_, i) => lineupPlayer(i + 1, `Local ${i + 1}`)),
    lineupPlayer(30, "Suplente Sin Jugar", { role: "substitute", participation: "unknown", minutes: missing })],
  away: [...Array.from({ length: 11 }, (_, i) => lineupPlayer(i + 40, `Jaén ${i + 40}`)),
    lineupPlayer(966303, "Moha Keita", { role: "substitute", minutes: { value: 22 } })],
};

async function serveDetail(page: Page, statistics: Record<string, unknown> = DETAIL.statistics,
  lineups: Record<string, unknown> = DETAIL.lineups, incidents: Record<string, unknown> = DETAIL.incidents) {
  await serveReal(page);
  await page.route("**/api/v1/matches/*/observed", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    return id === "sofascore-match-1"
      ? route.fulfill({ json: { ...DETAIL, statistics, lineups, incidents } })
      : route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Partido no encontrado en la fuente real de partidos." } } });
  });
}

test("Partidos reales: exact team text opens its team; row space, crest and score open the match", async ({ page }, testInfo) => {
  await serveDetail(page);
  await page.route("**/api/v1/teams/sofascore-team-1/observed-performance", route =>
    route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Sin análisis propio." } } }));
  await page.route("**/api/v1/teams/sofascore-team-1", route =>
    route.fulfill({ json: { id: "sofascore-team-1", name: "CE Europa" } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/matches");
  const list = page.getByRole("list", { name: "Partidos reales" });
  const item = list.getByRole("listitem").first();
  const row = item.locator(":scope > div");
  const teamLink = item.getByRole("link", { name: "Ver equipo CE Europa" });
  if (testInfo.project.name === "desktop") {
    await expectEntityAwareRowHover(page, row, row.locator(":scope > div").first(), teamLink);
  }
  await expect(teamLink.locator("[data-crest]")).toHaveCount(0);
  const teamHref = new URL((await teamLink.getAttribute("href"))!, page.url()).href;
  const teamBox = await teamLink.boundingBox();
  expect(teamBox).not.toBeNull();
  await page.mouse.click(teamBox!.x + teamBox!.width / 2, teamBox!.y + teamBox!.height / 2);
  await expect(page).toHaveURL(teamHref);
  await expect(page.getByRole("heading", { level: 2, name: "CE Europa" })).toBeVisible();
  await page.goBack();
  await expect(list).toBeVisible();

  const nameBox = await item.getByRole("link", { name: "Ver equipo CE Europa" }).boundingBox();
  const rowBox = await row.boundingBox();
  expect(nameBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  for (const x of [nameBox!.x - 2, nameBox!.x + nameBox!.width + 2]) {
    expect(x).toBeGreaterThan(rowBox!.x);
    expect(x).toBeLessThan(rowBox!.x + rowBox!.width);
    await page.mouse.click(x, nameBox!.y + nameBox!.height / 2);
    await expect(page).toHaveURL(/\/matches\/sofascore-match-1\?from=matches$/);
    await expect(page.getByRole("heading", { level: 1, name: "CE Europa contra Real Jaén" })).toBeVisible();
    await page.goBack();
    await expect(list).toBeVisible();
  }

  const score = item.getByRole("link", { name: /^Ver partido/ });
  await score.click();
  await expect(page).toHaveURL(/\/matches\/sofascore-match-1\?from=matches$/);
  await expect(page.getByRole("heading", { level: 1, name: "CE Europa contra Real Jaén" })).toBeVisible();
  await page.goBack();
  await expect(list).toBeVisible();

  const crestBox = await row.locator("[data-crest]").first().boundingBox();
  expect(crestBox).not.toBeNull();
  await page.mouse.click(crestBox!.x + crestBox!.width / 2, crestBox!.y + crestBox!.height / 2);
  await expect(page).toHaveURL(/\/matches\/sofascore-match-1\?from=matches$/);
  await expect(page.getByRole("heading", { level: 1, name: "CE Europa contra Real Jaén" })).toBeVisible();
});

test("Partido real: from the list to its detail with team stats, unknown red cards and back", async ({ page }, testInfo) => {
  await serveDetail(page);
  const synthetic: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(competitions|matches\/[^/]+$)/.test(new URL(r.url()).pathname)) synthetic.push(r.url()); });
  await page.goto("/matches");
  await page.getByRole("combobox", { name: "Estado" }).selectOption("finished");
  await page.getByRole("list", { name: "Partidos reales" }).getByRole("link", { name: /^Ver partido/ }).first().click();
  await expect(page).toHaveURL(/\/matches\/sofascore-match-1\?from=matches&state=finished$/);
  await expect(page.getByText("0 – 3")).toBeVisible();
  // No competition nor season in the header, no provenance block; the round and date stay.
  await expect(page.getByText(/Primera Federacion/)).toHaveCount(0);
  await expect(page.getByText("Datos reales", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Jornada 1", { exact: true })).toBeVisible();
  await expect(page.locator("strong", { hasText: "CE Europa" }).last()).toBeVisible();
  const reds = page.locator("dt", { hasText: "Rojas" }).locator("..");
  await expect(reds.locator("dd").nth(0)).toHaveText("—");
  await expect(reds.locator("dd").nth(1)).toHaveText("—");
  await expect(page.locator("dt", { hasText: "Amarillas" }).locator("..").locator("dd").nth(0)).toHaveText("0");
  await expect(page.getByRole("tab")).toHaveText(["Resumen", "Alineaciones"]);
  await expect(page.getByRole("tab", { name: "Resumen" })).toHaveAttribute("aria-selected", "true");
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-match-detail.png"), fullPage: true });
  expect(synthetic).toEqual([]);
  // Back to the list with the status it had.
  await page.getByRole("link", { name: "‹ Volver a partidos" }).click();
  await expect(page).toHaveURL(/\/matches\?state=finished$/);
  await expect(page.getByRole("combobox", { name: "Estado" })).toHaveValue("finished");
  await expect(page.getByRole("list", { name: "Partidos reales" }).getByRole("listitem")).toHaveCount(2);

  // And with the team it had, also from a match the source cannot show.
  await page.getByRole("combobox", { name: "Estado" }).selectOption("all");
  await page.getByRole("combobox", { name: "Equipo" }).selectOption("sofascore-team-3");
  await page.getByRole("list", { name: "Partidos reales" }).getByRole("link", { name: /^Ver partido/ }).first().click();
  await page.getByRole("link", { name: "‹ Volver a partidos" }).click();
  await expect(page).toHaveURL(/\/matches\?team=sofascore-team-3$/);
  await expect(page.getByRole("combobox", { name: "Equipo" })).toHaveValue("sofascore-team-3");

  // A direct URL without parameters opens normally and returns to plain «Partidos».
  await page.goto("/matches/sofascore-match-1");
  await expect(page.getByRole("link", { name: "‹ Volver a partidos" })).toHaveAttribute("href", "/matches");

  // Legacy `view=observed` URLs remain usable and retain their old URL.
  await page.goto("/matches/sofascore-match-1?view=observed");
  await expect(page).toHaveURL("/matches/sofascore-match-1?view=observed");
  await expect(page.getByRole("link", { name: "‹ Volver a partidos" })).toHaveAttribute("href", "/matches");
});

test("Partido real: an excluded match (Copa del Rey) cannot be opened by its id, nor its sections", async ({ page }) => {
  await serveDetail(page);
  for (const url of ["/matches/sofascore-match-17259821?view=observed", "/matches/sofascore-match-17259821?view=observed&tab=lineups"]) {
    await page.goto(url);
    await expect(page.getByRole("heading", { name: "Partido no encontrado en la fuente real" })).toBeVisible();
    await expect(page.getByRole("tab")).toHaveCount(0);
    await expect(page.getByRole("link", { name: "‹ Volver a partidos" })).toBeVisible();
  }
});

test("Partido real: statistics not stored or failing, and an id unknown to the real source", async ({ page }) => {
  await serveDetail(page, { state: "not_stored", fetchedAt: null });
  await page.goto("/matches/sofascore-match-1?view=observed");
  await expect(page.getByText("La fuente no tiene estadísticas guardadas de este partido.")).toBeVisible();

  await page.unroute("**/api/v1/matches/*/observed");
  await serveDetail(page, { state: "error", fetchedAt: null });
  await page.reload();
  await expect(page.locator("main").getByRole("alert")).toContainText("No se pudieron cargar las estadísticas de este partido.");
  await expect(page.getByText("0 – 3")).toBeVisible();

  await page.goto("/matches/match-r01-01?view=observed");
  await expect(page.getByRole("heading", { name: "Partido no encontrado en la fuente real" })).toBeVisible();
});

test("Alineaciones reales: tab with once inicial and banquillo, recorded marks, real profile and back to the tab", async ({ page }, testInfo) => {
  await serveDetail(page, DETAIL.statistics, LINEUPS, INCIDENTS);
  // Real profiles enabled too, so names link to them; the profile itself is simulated as well.
  await page.route("**/api/v1/source", (route) =>
    route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/players/*/profile", (route) => route.fulfill({ json: {
    player: { id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder", birthDate: "1999-09-18", heightCm: 165, preferredFoot: null, country: "Senegal" },
    scope: "Datos personales según la fuente. No indican equipo actual.", source: { synthetic: false, label: "Datos reales" } } }));
  await page.route("**/api/v1/players/*/observed-matches", (route) => route.fulfill({ json: {
    playerId: "sofascore-player-966303", matches: [], scope: "—", ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" } } }));
  const synthetic: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(competitions|matches\/[^/]+$|players\/[^/]+$)/.test(new URL(r.url()).pathname)) synthetic.push(r.url()); });

  await page.goto("/matches/sofascore-match-1?view=observed");
  await page.getByRole("tab", { name: "Alineaciones" }).click();
  const lineups = page.getByRole("tabpanel", { name: "Alineaciones" });
  await expect(lineups.getByText(/Alineaciones confirmadas/)).toHaveCount(0);
  await expect(lineups.getByRole("list", { name: "Once inicial de CE Europa" }).getByRole("listitem")).toHaveCount(11);
  await expect(lineups.getByRole("list", { name: "Banquillo de CE Europa" })).toContainText("Participación no confirmada");
  const jaen = lineups.getByRole("list", { name: "Once inicial de Real Jaén" });
  await expect(jaen.getByRole("img", { name: "2 goles (1 de penalti)" })).toBeVisible();
  await expect(jaen.getByRole("img", { name: "1 asistencia" })).toBeVisible();
  await expect(jaen.getByRole("img", { name: "Sustituido en el minuto 68′" })).toBeVisible();
  await expect(lineups.getByRole("list", { name: "Banquillo de Real Jaén" })).toContainText("68′Sale · Jaén 45");
  await expect(lineups.getByText(/4-4-2|Formación/)).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-lineups.png"), fullPage: true });

  await lineups.getByRole("link", { name: "Moha Keita" }).click();
  await expect(page).toHaveURL(/\/players\/sofascore-player-966303\?view=profile&from=observedMatch&origin=sofascore-match-1&originTab=lineups$/);
  await expect(page.getByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page).toHaveURL(/\/matches\/sofascore-match-1\?tab=lineups$/);
  await expect(page.getByRole("tab", { name: "Alineaciones" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel", { name: "Alineaciones" })).toBeVisible();
  expect(synthetic).toEqual([]);

  // The complete journey keeps both filters through the player profile.
  await serveReal(page);
  await page.goto("/matches?team=all&state=finished");
  await page.getByRole("list", { name: "Partidos reales" }).getByRole("link", { name: /^Ver partido/ }).first().click();
  await expect(page.getByRole("tabpanel", { name: "Resumen" })).toBeVisible();
  await expect(page.getByText("CE Europa", { exact: true }).last()).toBeVisible();
  await expect(page.getByText("Estadísticas de equipo", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("filtered-summary.png"), fullPage: true });
  await page.getByRole("tab", { name: "Alineaciones" }).click();
  await page.screenshot({ path: testInfo.outputPath("filtered-lineups.png"), fullPage: true });
  await page.getByRole("link", { name: "Moha Keita", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Alineaciones" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("link", { name: "‹ Volver a partidos" }).click();
  await expect(page).toHaveURL(/\/matches\?team=all&state=finished$/);
  await expect(page.getByRole("combobox", { name: "Equipo" })).toHaveValue("all");
  await expect(page.getByRole("combobox", { name: "Estado" })).toHaveValue("finished");
});

test("Alineaciones reales: failed incidents keep line-ups and stats; failed line-ups recover on the same tab", async ({ page }) => {
  await serveDetail(page, DETAIL.statistics, LINEUPS, { state: "error", fetchedAt: null });
  await page.goto("/matches/sofascore-match-1?view=observed&tab=lineups");
  const lineups = page.getByRole("tabpanel", { name: "Alineaciones" });
  await expect(lineups.getByRole("alert")).toContainText("No se pudieron cargar las incidencias");
  await expect(lineups.getByRole("list", { name: "Once inicial de Real Jaén" }).getByRole("img")).toHaveCount(0);

  await page.unroute("**/api/v1/matches/*/observed");
  await serveDetail(page, DETAIL.statistics, { state: "error", fetchedAt: null }, INCIDENTS);
  await page.reload();
  await expect(lineups.getByRole("alert")).toContainText("No se pudieron cargar las alineaciones de este partido.");
  await expect(page.getByText("0 – 3")).toBeVisible();

  await page.unroute("**/api/v1/matches/*/observed");
  await serveDetail(page, DETAIL.statistics, { ...LINEUPS, away: LINEUPS.away.slice(0, 9) }, INCIDENTS);
  await lineups.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("tab", { name: "Alineaciones" })).toHaveAttribute("aria-selected", "true");
  await expect(lineups.getByText("Alineación incompleta en la fuente: 9 titulares.")).toBeVisible();
  await page.getByRole("tab", { name: "Resumen" }).click();
  await expect(page.locator("dt", { hasText: "Posesión" })).toBeVisible();
});

test("Partido real: after a score correction, sections pending review are said so and their goals are not marked", async ({ page }) => {
  await serveReal(page);
  let review = true;
  await page.route("**/api/v1/matches/*/observed", (route) => route.fulfill({ json: review
    ? { ...DETAIL, lineups: LINEUPS, incidents: { ...INCIDENTS, goals: null },
      review: { corrections: ["score"], sections: ["lineups", "incidents"] } }
    : { ...DETAIL, lineups: LINEUPS, incidents: INCIDENTS } }));
  await page.goto("/matches/sofascore-match-1?view=observed");
  const main = page.getByRole("main");
  const note = main.getByRole("note", { name: "Secciones pendientes de revisión" });
  await expect(note).toContainText("El marcador se ha actualizado, pero algunas secciones siguen pendientes de revisión.");
  await expect(note).toContainText("Pendientes: alineaciones e incidencias");
  await main.getByRole("tab", { name: "Alineaciones" }).click();
  const panel = main.getByRole("tabpanel", { name: "Alineaciones" });
  await expect(panel.getByText(/Las incidencias son anteriores a la corrección del marcador/)).toBeVisible();
  await expect(panel.getByRole("img", { name: /gol/ })).toHaveCount(0);
  await expect(panel.getByLabel("Entra en el minuto 68′")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  expect(overflow).toBe(false);

  // Once the sections are recovered, the notice goes and the goals are marked again.
  review = false;
  await page.reload();
  await expect(main.getByRole("tab", { name: "Alineaciones" })).toBeVisible();
  await expect(note).toHaveCount(0);
  await main.getByRole("tab", { name: "Alineaciones" }).click();
  await expect(panel.getByRole("img", { name: "2 goles (1 de penalti)" })).toBeVisible();
});
