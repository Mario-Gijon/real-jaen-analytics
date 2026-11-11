import { expect, test, type Page } from "@playwright/test";
import { expectEntityAwareRowHover, expectInlineEntityHover, expectKeyboardFocus } from "./interactive-row";
import { REAL_SOURCE } from "./sources";

/*
 * «Equipos» with the real source (TEAMS_SOURCE=local). The e2e server is
 * synthetic, so these tests answer the browser's /api/v1 requests for the
 * real sections themselves: simulated responses, not the local API.
 */

const jaen = { id: "team-real-jaen", name: "Real Jaén" };
const rival = (n: number, name: string) => ({ id: `sofascore-team-${n}`, name });
const match = (round: number, home: { id: string; name: string }, away: { id: string; name: string }, score: [number, number]) => ({
  id: `sofascore-match-${round}`, kickoff: `2026-09-${String(round).padStart(2, "0")}T17:00:00+00:00`, status: "finished",
  competition: "Primera Federacion, Group 2", season: "26/27", round, home, away, score: { home: score[0], away: score[1] },
});
const results = [
  { match: match(1, rival(1, "CE Europa"), jaen, [0, 3]), side: "away", result: "win", goalsFor: 3, goalsAgainst: 0 },
  { match: match(2, jaen, rival(2, "Huesca"), [2, 2]), side: "home", result: "draw", goalsFor: 2, goalsAgainst: 2 },
  { match: match(5, rival(5, "Antequera"), jaen, [2, 0]), side: "away", result: "loss", goalsFor: 0, goalsAgainst: 2 },
];
const GROUP_2 = "sofascore-scope-94191-97382";
const INDICATORS = ["goals", "xg", "shots", "shotsOnTarget", "goalsAgainst", "xgAgainst", "shotsAgainst", "shotsOnTargetAgainst", "possession", "corners"];
const stat = (value: number | null, matches: number, of: number) => value === null
  ? { value: null, coverage: "none", reason: "not_available", matches: 0, of }
  : { value, coverage: matches === of ? "complete" : "partial", matches, of };
const averages = (of: number, values: Record<string, unknown>) =>
  Object.fromEntries(INDICATORS.map((key) => [key, values[key] ?? stat(null, 0, of)]));
/** As the source computes it: corners missing everywhere, xG in two of three matches. */
const output = {
  season: averages(3, { goals: stat(5 / 3, 3, 3), goalsAgainst: stat(4 / 3, 3, 3), xg: stat(1.42, 2, 3), xgAgainst: stat(1.1, 2, 3),
    shots: stat(11, 3, 3), shotsAgainst: stat(13, 3, 3), shotsOnTarget: stat(4, 3, 3), shotsOnTargetAgainst: stat(3, 3, 3),
    possession: stat(48.5, 2, 3) }),
  last3: { matchIds: results.map((r) => r.match.id), values: averages(3, { goals: stat(5 / 3, 3, 3), goalsAgainst: stat(4 / 3, 3, 3),
    xg: stat(1.42, 2, 3), xgAgainst: stat(1.1, 2, 3), shots: stat(11, 3, 3), shotsAgainst: stat(13, 3, 3),
    shotsOnTarget: stat(4, 3, 3), shotsOnTargetAgainst: stat(3, 3, 3), possession: stat(48.5, 2, 3) }) },
  undated: 0,
};
const performance = {
  teamId: "team-real-jaen", teams: [jaen], source: { synthetic: false, label: "Datos reales" },
  scope: "Resultados de los partidos finalizados observados en esta competición. No es necesariamente la temporada completa: sin clasificación ni puntos.",
  competitions: [{
    id: GROUP_2, name: "Primera Federación, Grupo 2", season: "26/27",
    matches: { inScope: 37, finished: 4, pending: 29, counted: 3, finishedWithoutValidScore: 1, excludedByVersionConflict: 0, unknownStatus: 0, replacedLocally: 0 },
    record: { played: 3, wins: 1, draws: 1, losses: 1, goalsFor: 5, goalsAgainst: 4, goalDifference: 1 },
    venues: { home: { win: 0, draw: 1, loss: 0 }, away: { win: 1, draw: 0, loss: 1 } },
    results,
    output,
  }],
};

async function serveReal(page: Page, { down = false } = {}) {
  await page.route("**/api/v1/source", (route) =>
    route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/teams/*/observed-performance", (route) => down
    ? route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "La fuente de datos real no está disponible." } } })
    : route.fulfill({ json: performance }));
}
const noHorizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("Equipos reales: la tabla de partidos cabe en móviles estrechos y conserva accesibles L/V y nombres largos", async ({ page }, testInfo) => {
  const longName = "Atlético Madrileño del Noroeste";
  const expanded = {
    ...performance,
    competitions: performance.competitions.map((scope) => ({
      ...scope,
      results: scope.results.map((entry, index) => index === 0
        ? { ...entry, match: { ...entry.match, home: { ...entry.match.home, name: longName } } }
        : entry),
    })),
  };
  await serveReal(page);
  await page.route("**/api/v1/teams/*/observed-performance", (route) => route.fulfill({ json: expanded }));
  await page.goto("/teams");
  const table = page.getByRole("table", { name: "Partidos disputados" });
  await expect(table).toBeVisible();
  const longLink = table.getByRole("link", { name: `Ver equipo ${longName}` });
  const longRow = longLink.locator("xpath=../..");
  await expect(longLink).toHaveCount(1);
  await expect(longLink).toHaveAccessibleName(`Ver equipo ${longName}`);

  for (const width of [320, 360, 375, 390, 430]) {
    await page.setViewportSize({ width, height: 844 });
    const extent = await page.evaluate(() => ({
      width: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      overflowing: [...document.body.querySelectorAll<HTMLElement>("*")]
        .map((element) => ({
          element: `${element.tagName.toLowerCase()}${element.id ? `#${element.id}` : ""}${typeof element.className === "string" && element.className ? `.${element.className.trim().replace(/\s+/g, ".")}` : ""}`,
          text: element.innerText?.slice(0, 48),
          left: Math.round(element.getBoundingClientRect().left),
          right: Math.round(element.getBoundingClientRect().right),
        }))
        .filter((box) => box.right > window.innerWidth + 1 || box.left < -1)
        .sort((a, b) => b.right - a.right)
        .slice(0, 6),
    }));
    expect(extent.scrollWidth <= extent.width, `horizontal overflow at ${width}px: ${JSON.stringify(extent)}`).toBe(true);
    const condition = table.locator("tbody tr td:nth-child(4) abbr[aria-label]").first();
    const label = await condition.getAttribute("aria-label");
    expect(["Local", "Visitante"]).toContain(label);
    await expect(condition).toHaveAccessibleName(label!);
    await expect(condition).toContainText(label === "Local" ? "L" : "V");
    await expect(longLink.locator("span")).toHaveCSS("text-overflow", "ellipsis");
    const rowBox = await longRow.boundingBox();
    const nameBox = await longLink.locator("span").boundingBox();
    expect(rowBox).not.toBeNull();
    expect(nameBox).not.toBeNull();
    expect(nameBox!.x).toBeGreaterThanOrEqual(rowBox!.x);
    expect(nameBox!.x + nameBox!.width).toBeLessThanOrEqual(rowBox!.x + rowBox!.width);
    if (width === 320) {
      expect(await longLink.locator("span").evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);
    }
  }

  await page.setViewportSize({ width: 320, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("real-team-matches-320.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("real-team-matches-390.png"), fullPage: true });
  await page.setViewportSize({ width: 768, height: 900 });
  expect(await noHorizontalOverflow(page)).toBe(true);
  await expect(table.locator("thead th")).toHaveCount(5);
  await expect(table.getByRole("columnheader", { name: "Condición" })).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 900 });
  expect(await noHorizontalOverflow(page)).toBe(true);
  await expect(table.locator("thead th")).toHaveCount(5);
  await expect(table.getByRole("columnheader", { name: "Condición" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("real-team-matches-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  const teamHref = await longLink.getAttribute("href");
  await longLink.locator("span").click();
  await expect(page).toHaveURL(teamHref!);
});

test("Equipos reales: the 20 teams keep their grid, full names and native tooltips", async ({ page }, testInfo) => {
  const teams = [jaen, ...[
    [24322, "AD Alcorcón"], [4489, "Algeciras CF"], [24323, "Antequera"], [24326, "Atlético Madrileño"],
    [43751, "CD Teruel"], [217210, "CDA Águilas FC"], [135672, "CE Europa"], [24329, "FC Cartagena"],
    [2840, "Gimnàstic de Tarragona"], [2877, "Hércules CF"], [24265, "Huesca"], [263407, "Juventud Torremolinos CF"],
    [202074, "Rayo Majadahonda"], [5069, "Real Madrid Castilla U21"], [4487, "Real Murcia"], [2815, "Real Zaragoza"],
    [24368, "UD Ibiza"], [24363, "UE Sant Andreu"], [24338, "Villarreal B U23"],
  ].map(([id, name]) => ({ id: `sofascore-team-${id}`, name: String(name) }))];
  await serveReal(page);
  await page.route("**/api/v1/teams/*/observed-performance", (route) => {
    const teamId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    return route.fulfill({ json: { ...performance, teams, teamId,
      competitions: performance.competitions.map((c) => ({ ...c,
        venues: teamId === jaen.id ? c.venues : { home: c.venues.away, away: c.venues.home },
      })),
    } });
  });
  await page.route("**/api/v1/teams/*/observed-squad", (route) => route.fulfill({ json: squad }));
  await page.goto("/teams");
  await expect(page.getByRole("heading", { level: 2, name: jaen.name })).toBeVisible();
  const grid = page.getByRole("group", { name: "Equipo", exact: true });
  const phone = testInfo.project.name === "mobile";
  if (phone) await page.getByRole("button", { name: "Cambiar equipo" }).click();
  await expect(grid.getByRole("radio")).toHaveCount(20);
  await expect(grid.getByRole("radio").first()).toHaveAccessibleName(jaen.name);
  const tiles = await grid.locator("label").evaluateAll(labels => labels.map(label => {
    const { y, height } = label.getBoundingClientRect();
    return { y, height };
  }));
  const rowSizes = [...new Set(tiles.map(tile => tile.y))]
    .map(y => tiles.filter(tile => tile.y === y).length);
  expect(rowSizes).toEqual(phone ? [4, 4, 4, 4, 4] : [5, 5, 5, 5]);
  expect(tiles.every(tile => tile.height >= 44)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("teams-grid.png"), fullPage: true });
  for (const team of teams) {
    if (phone && !await grid.isVisible()) await page.getByRole("button", { name: "Cambiar equipo" }).click();
    const tile = grid.locator("label").filter({ has: page.getByRole("radio", { name: team.name, exact: true }) });
    await expect(tile).toHaveAttribute("title", team.name);
    await tile.click();
    await expect(page.locator(`input[type="radio"][value="${team.id}"]`)).toBeChecked();
    await expect(page.getByRole("heading", { level: 2, name: team.name, exact: true })).toBeVisible();
    const venues = page.getByLabel("Local y visitante");
    await expect(venues.locator(":scope > div").nth(0).locator("strong"))
      .toHaveText(team.id === jaen.id ? ["0", "1", "0"] : ["1", "0", "1"]);
    await expect(venues.locator(":scope > div").nth(1).locator("strong"))
      .toHaveText(team.id === jaen.id ? ["1", "0", "1"] : ["0", "1", "0"]);
    expect(await noHorizontalOverflow(page)).toBe(true);
  }
  if (phone) await page.getByRole("button", { name: "Cambiar equipo" }).click();
  const tile = grid.locator("label").filter({ has: page.getByRole("radio", { name: "Juventud Torremolinos CF", exact: true }) });
  if (!phone) await tile.hover();
  await expect(tile.locator("span").last()).toHaveCSS("text-overflow", "ellipsis");
  await tile.click();
  const heading = page.getByRole("heading", { level: 2, name: "Juventud Torremolinos CF", exact: true });
  await expect(heading).toHaveCSS("white-space", "normal");
  const venues = page.getByLabel("Local y visitante");
  await expect(venues.locator('[data-result="win"]').first()).toHaveCSS("color", "rgb(24, 134, 93)");
  await expect(venues.locator('[data-result="draw"]').first()).toHaveCSS("color", "rgb(94, 97, 114)");
  await expect(venues.locator('[data-result="loss"]').first()).toHaveCSS("color", "rgb(200, 59, 80)");
  await expect(page.getByText("Tiros a puerta concedidos / partido", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("teams-long-name.png"), fullPage: true });
  await page.getByRole("button", { name: "Últimos 3", exact: true }).click();
  await expect(page.getByRole("table", { name: "Partidos disputados" }).locator("tr[data-in-period]")).toHaveCount(3);
  const reference = await page.getByRole("table", { name: "Partidos disputados" }).locator("thead th").first().evaluate((cell) => {
    const style = getComputedStyle(cell);
    return { color: style.color, fontSize: style.fontSize, fontWeight: style.fontWeight,
      textTransform: style.textTransform, letterSpacing: style.letterSpacing,
      paddingTop: style.paddingTop, paddingLeft: style.paddingLeft };
  });
  await page.screenshot({ path: testInfo.outputPath("teams-last3.png"), fullPage: true });
  await page.getByRole("tab", { name: "Jugadores", exact: true }).click();
  await expect(page.getByRole("table", { name: "Jugadores observados" })).toBeVisible();
  const playersTable = page.getByRole("table", { name: "Jugadores observados" });
  await expect(playersTable.locator("thead th")).toHaveText([
    "Jugador", "Posición", "Partidos", "Titularidades", "Minutos", "Goles", "Asistencias", "Estatura", "Pierna",
  ]);
  await expect(playersTable.locator("thead th").first()).toHaveCSS("white-space", "nowrap");
  for (const header of await playersTable.locator("thead th").all()) {
    for (const [property, value] of Object.entries(reference)) {
      const cssName = property.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`);
      await expect(header).toHaveCSS(cssName, value);
    }
  }
  for (const button of await playersTable.locator("thead button").all()) {
    await expect(button).toHaveCSS("text-transform", "uppercase");
    await expect(button).toHaveCSS("color", reference.color);
  }
  const note = page.getByRole("complementary", { name: "Nota sobre los datos" });
  await expect(note).toBeVisible();
  await expect(note).not.toContainText(/ordenar|—/);
  await expect(page.getByText("Datos reales", { exact: true })).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("teams-players-viewport.png") });
  await page.screenshot({ path: testInfo.outputPath("teams-players.png"), fullPage: true });
  expect(await noHorizontalOverflow(page)).toBe(true);
  await playersTable.locator("..").evaluate((scroll) => { scroll.scrollLeft = (scroll.scrollWidth - scroll.clientWidth) / 2; });
  await page.screenshot({ path: testInfo.outputPath("teams-players-middle.png") });
  await playersTable.locator("..").evaluate((scroll) => { scroll.scrollLeft = scroll.scrollWidth; });
  await expect(note).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("teams-players-scrolled.png"), fullPage: true });
  await page.getByRole("button", { name: /^Porteros/ }).click();
  await expect(note).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("teams-players-complete.png"), fullPage: true });
});

test("Equipos reales: balance, casa/fuera, resultados y partidos del ámbito, sin nada sintético", async ({ page }, testInfo) => {
  await serveReal(page);
  const synthetic: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(competitions|seasons\/)/.test(new URL(r.url()).pathname)) synthetic.push(r.url()); });
  await page.goto("/teams");
  await expect(page.getByRole("heading", { level: 2, name: "Real Jaén" })).toBeVisible();
  await expect(page.locator("header").getByText("Primera Federación, Grupo 2", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Competición" })).toHaveCount(0);
  await expect(page.getByRole("combobox", { name: "Temporada" })).toHaveCount(0);
  await expect(page.getByText("Datos reales", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("columnheader", { name: "Jornada", exact: true })).toBeVisible();
  // The crest grid keeps its design (folded on phones) with the only team with correspondence.
  await expect(page.locator("input[type=radio]")).toHaveCount(1);

  const balance = page.getByLabel("Balance");
  await expect(balance).toContainText("DG+1");
  await expect(balance).toContainText("PJ3");
  await expect(page.getByText(/1 finalizado sin marcador válido no se incluye/)).toBeVisible();
  await expect(page.getByText(/Clasificación|Puntos/)).toHaveCount(0);
  // Production and concession from the source, with partial coverage marked and a missing value as «—».
  await expect(page.getByRole("heading", { name: "Rendimiento", exact: true })).toBeVisible();
  await expect(page.locator('[data-indicator="goals"]')).toHaveText("1,67");
  await expect(page.locator('[data-indicator="xg"]')).toHaveText("1,42*");
  await expect(page.locator('[data-indicator="corners"]')).toHaveText("—");
  await page.getByRole("button", { name: "Últimos 3" }).click();
  await expect(page.locator('[data-indicator="goals"]')).toContainText("±0,00 vs temporada");
  await expect(page.getByRole("table", { name: "Partidos disputados" }).locator("tr[data-in-period]")).toHaveCount(3);
  await page.getByRole("button", { name: "Temporada", exact: true }).click();
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-teams.png"), fullPage: true });

  // Keyboard: the result sequence names each result.
  const chips = page.getByRole("list", { name: "Resultados por jornada" }).getByRole("button");
  await chips.first().focus();
  await expect(page.locator("p[aria-live]")).toContainText("CE Europa");
  await page.keyboard.press("Tab");
  await expect(page.locator("p[aria-live]")).toContainText("Huesca");

  // Each played match opens its real detail.
  const table = page.getByRole("table", { name: "Partidos disputados" });
  await expect(table.getByRole("link", { name: /^Ver equipo/ })).toHaveCount(3);
  await expect(table.getByRole("link", { name: /^Ver partido/ })).toHaveCount(3);
  await expect(table.getByRole("link", { name: /^Ver partido/ }).first()).toHaveAttribute("href", "/matches/sofascore-match-5?from=teams");
  if (testInfo.project.name === "desktop") {
    const row = table.locator("tbody tr").first();
    await expectEntityAwareRowHover(page, row, row.locator("td").first(), row.getByRole("link", { name: /^Ver equipo/ }));
  }
  expect(synthetic).toEqual([]);
});

test("Equipos reales: un error tiene reintento y una temporada sintética no se sustituye", async ({ page }) => {
  await serveReal(page, { down: true });
  await page.goto("/teams");
  await expect(page.locator("main").getByRole("alert")).toContainText("No se pudieron cargar los datos reales del equipo.");
  await page.unroute("**/api/v1/teams/*/observed-performance");
  await serveReal(page);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByLabel("Balance")).toContainText("DG+1");

  await page.goto("/teams?season=season-demo-2026-27");
  await expect(page.getByText(/La temporada indicada no corresponde a la fuente real de equipos/)).toBeVisible();
  await page.getByRole("link", { name: "Ver la temporada real" }).click();
  await expect(page.getByLabel("Balance")).toBeVisible();
});

const figure = (value: number | null, matches: number, of: number) => stat(value, matches, of);
/** As the service accumulates them: a starter, a player with one call-up it could not resolve and a substitute never used. */
const squad = {
  teamId: "team-real-jaen", source: { synthetic: false, label: "Datos reales" },
  scope: "Jugadores observados en las convocatorias de los partidos finalizados disponibles de esta competición. No garantiza toda la temporada ni es una plantilla completa o necesariamente actual.",
  competitions: [{
    id: GROUP_2, name: "Primera Federación, Grupo 2", season: "26/27", matches: { counted: 3, withCallUps: 3 },
    callUps: { total: 9, played: 5, unused: 3, unknown: 1, contradictions: 0, mismatches: 0, teamWarnings: 0, duplicateRecords: 0,
      discardedRecords: 0 },
    players: [
      { player: { id: "sofascore-player-942663", name: "Marco Siverio Toro", position: "forward" }, heightCm: 184, preferredFoot: null,
        lastShirtNumber: 9, callUps: 3, stats: { appearances: figure(3, 3, 3), starts: figure(3, 3, 3), minutes: figure(250, 3, 3),
          goals: figure(2, 3, 3), assists: figure(0, 3, 3) } },
      { player: { id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder" }, heightCm: 165, preferredFoot: "right",
        lastShirtNumber: 19, callUps: 3, stats: { appearances: figure(2, 2, 3), starts: figure(1, 3, 3), minutes: figure(110, 2, 3),
          goals: figure(1, 2, 3), assists: figure(1, 2, 3) } },
      { player: { id: "sofascore-player-1086428", name: "Salvi", position: "goalkeeper" }, heightCm: 190, preferredFoot: null,
        lastShirtNumber: 13, callUps: 3, stats: { appearances: figure(0, 3, 3), starts: figure(0, 3, 3), minutes: figure(0, 3, 3),
          goals: figure(0, 3, 3), assists: figure(0, 3, 3) } },
    ],
  }],
};
const realProfile = {
  player: { id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder", birthDate: "1999-09-18", heightCm: 165,
    preferredFoot: "right", country: "Senegal" },
  scope: "Datos personales según la fuente. No indican equipo actual.", source: { synthetic: false, label: "Datos reales" },
};

test("Equipos reales: jugadores con acumulados reales, orden, filtro, ficha y vuelta", async ({ page }, testInfo) => {
  await serveReal(page);
  await page.route("**/api/v1/teams/*/observed-squad", (route) => route.fulfill({ json: squad }));
  await page.route("**/api/v1/players/*/profile", (route) => route.fulfill({ json: realProfile }));
  await page.route("**/api/v1/players/*/observed-matches", (route) => route.fulfill({ json: {
    playerId: "sofascore-player-966303", matches: [], ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" },
    scope: "Partidos en los que la fuente registra al jugador en la convocatoria." } }));
  const perPlayer: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(players\/[^/]+$|teams\/[^/]+\/players$|seasons\/)/.test(new URL(r.url()).pathname)) perPlayer.push(r.url()); });

  await page.goto("/teams?tab=players");
  const table = page.getByRole("table", { name: "Jugadores observados" });
  await expect(table).toBeVisible();
  const row = (name: string) => table.getByRole("row").filter({ has: page.getByRole("link", { name: `Ver ficha de ${name}` }) });
  await expect(row("Marco Siverio Toro").locator("td").nth(4)).toHaveText("2");
  await expect(row("Moha Keita").locator("td").nth(1)).toContainText("2*");
  await expect(row("Salvi").locator("td").nth(1)).toHaveText("0");
  await expect(row("Salvi").locator("td").nth(2)).toHaveText("0");
  await expect(row("Salvi").locator("td").nth(3)).toHaveText("0");
  await expect(row("Moha Keita").locator("td").nth(6)).toHaveText("165 cm");
  await expect(page.getByRole("complementary", { name: "Nota sobre los datos" })).toBeVisible();
  await expect(page.getByText(/sin participación resuelta; esas cifras/)).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-teams-players.png"), fullPage: true });

  // Keyboard: sort by goals (complete first, then partial, then missing) and filter by line.
  const goals = page.getByRole("columnheader", { name: /^Goles|^G/ });
  await goals.getByRole("button").focus();
  await page.keyboard.press("Enter");
  await expect(goals).toHaveAttribute("aria-sort", "descending");
  const names = () => table.getByRole("link").evaluateAll((links) => links.map((l) => l.getAttribute("aria-label")!.replace("Ver ficha de ", "")));
  expect(await names()).toEqual(["Marco Siverio Toro", "Salvi", "Moha Keita"]);
  await page.getByRole("button", { name: /^Porteros/ }).focus();
  await page.keyboard.press("Enter");
  expect(await names()).toEqual(["Salvi"]);
  await page.getByRole("button", { name: /^Todos/ }).click();

  // The real profile, and back to this real season and tab.
  const mohaRow = row("Moha Keita");
  const mohaLink = mohaRow.getByRole("link");
  if (testInfo.project.name === "desktop") {
    await expectInlineEntityHover(page, mohaRow, mohaRow.locator("td").first(), mohaLink);
  }
  await expectKeyboardFocus(page, mohaLink);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(new RegExp(`/players/sofascore-player-966303[?]view=profile&season=${GROUP_2}&from=team&origin=team-real-jaen`));
  await expect(page.getByRole("heading", { name: "Moha Keita" })).toBeVisible();
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page).toHaveURL(new RegExp(`/teams[?]season=${GROUP_2}&team=team-real-jaen&tab=players`));
  await expect(page.getByRole("table", { name: "Jugadores observados" })).toBeVisible();
  expect(perPlayer).toEqual([]);
});

/** Partial goals and assists far to the right of a phone: their screen-reader notes must scroll with the table. */
const partialSquad = {
  ...squad,
  competitions: [{
    ...squad.competitions[0],
    players: [
      { player: { id: "sofascore-player-1", name: "Francisco Javier Gómez de la Torre", position: "midfielder" }, heightCm: 178,
        preferredFoot: "left", lastShirtNumber: 7, callUps: 8, stats: { appearances: figure(5, 7, 8), starts: figure(4, 8, 8),
          minutes: figure(410, 7, 8), goals: figure(2, 7, 8), assists: figure(1, 7, 8) } },
      { player: { id: "sofascore-player-2", name: "Mohamed Abdelkader Benali", position: "forward" }, heightCm: null,
        preferredFoot: null, lastShirtNumber: 9, callUps: 8, stats: { appearances: figure(6, 6, 8), starts: figure(3, 8, 8),
          minutes: figure(380, 8, 8), goals: figure(3, 6, 8), assists: figure(0, 5, 8) } },
    ],
  }],
};

test("Equipos reales: las cifras parciales de goles y asistencias no ensanchan la página en el móvil", async ({ page }) => {
  await serveReal(page);
  await page.route("**/api/v1/teams/*/observed-squad", (route) => route.fulfill({ json: partialSquad }));
  for (const width of [360, 390]) {
    await page.setViewportSize({ width, height: 844 });
    await page.goto("/teams?tab=players");
    const table = page.getByRole("table", { name: "Jugadores observados" });
    await expect(table).toBeVisible();
    const row = table.getByRole("row").filter({ has: page.getByRole("link", { name: "Ver ficha de Francisco Javier Gómez de la Torre" }) });
    const nameLayout = await row.evaluate((element) => {
      const cell = element.querySelector<HTMLElement>("th[scope='row']")!;
      const link = cell.querySelector<HTMLElement>("a")!;
      const name = link.querySelector<HTMLElement>("span")!;
      const position = element.querySelector<HTMLElement>("td")!;
      return {
        cellRight: cell.getBoundingClientRect().right,
        linkRight: link.getBoundingClientRect().right,
        nameRight: name.getBoundingClientRect().right,
        positionLeft: position.getBoundingClientRect().left,
        textOverflow: getComputedStyle(name).textOverflow,
        clipped: name.scrollWidth > name.clientWidth,
      };
    });
    expect(nameLayout.linkRight).toBeLessThanOrEqual(nameLayout.cellRight);
    expect(nameLayout.nameRight).toBeLessThanOrEqual(nameLayout.positionLeft);
    expect(nameLayout.textOverflow).toBe("ellipsis");
    expect(nameLayout.clipped).toBe(true);
    // Goals and assists keep their partial mark and the note read by screen readers.
    await expect(row.locator("td").nth(4)).toContainText("2*");
    await expect(row.locator("td").nth(4)).toContainText("(dato de 7 de 8 convocatorias)");
    await expect(row.locator("td").nth(5)).toContainText("(dato de 7 de 8 convocatorias)");
    const note = row.locator("td").nth(5).getByText("(dato de 7 de 8 convocatorias)");
    expect((await note.boundingBox())!.x, `${width} px: the note starts beyond the screen`).toBeGreaterThan(width);

    // Against the chosen width: in phone emulation the layout viewport (innerWidth) grows with an overflowing page.
    const pageWidth = () => page.evaluate(() => document.documentElement.scrollWidth);
    expect(await pageWidth(), `${width} px: the page does not scroll sideways`).toBeLessThanOrEqual(width);
    const box = table.locator("xpath=..");
    expect(await box.evaluate((el) => el.scrollWidth > el.clientWidth), `${width} px: the table scrolls inside its box`).toBe(true);
    await box.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
    expect(await box.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
    await expect(row.locator("td").nth(7)).toBeInViewport();
    expect(await pageWidth(), `${width} px: still no page scroll after scrolling the table`).toBeLessThanOrEqual(width);
  }
});

test("Equipos reales: un fallo de los acumulados tiene reintento, sin jugadores sintéticos", async ({ page }) => {
  await serveReal(page);
  let down = true;
  await page.route("**/api/v1/teams/*/observed-squad", (route) => down
    ? route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "La fuente de datos real no está disponible." } } })
    : route.fulfill({ json: squad }));
  await page.goto("/teams?tab=players");
  await expect(page.locator("main").getByRole("alert")).toContainText("La fuente de datos real no está disponible.");
  await expect(page.getByRole("table", { name: /Jugadores/ })).toHaveCount(0);
  down = false;
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("table", { name: "Jugadores observados" })).toBeVisible();
});

test("Equipos reales: el equipo elegido, su temporada y su pestaña viven en la URL", async ({ page }, testInfo) => {
  const ibiza = { id: "sofascore-team-24368", name: "UD Ibiza" };
  await serveReal(page);
  await page.route("**/api/v1/teams/*/observed-performance", (route) => {
    const teamId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    return teamId === "team-montealto"
      ? route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "No disponible." } } })
      : route.fulfill({ json: { ...performance, teamId, teams: [jaen, ibiza] } });
  });
  await page.route("**/api/v1/teams/*/observed-squad", (route) => route.fulfill({ json: { ...squad, teamId: ibiza.id } }));
  await page.route("**/api/v1/players/*/profile", (route) => route.fulfill({ json: realProfile }));
  await page.route("**/api/v1/players/*/observed-matches", (route) => route.fulfill({ json: {
    playerId: "sofascore-player-966303", matches: [], ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" },
    scope: "Partidos en los que la fuente registra al jugador en la convocatoria." } }));
  await page.route("**/api/v1/matches/*/observed", (route) =>
    route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "La fuente de datos real no está disponible." } } }));
  const entries = () => page.evaluate(() => history.length);

  // Without a team in the URL the club is the default, and the URL stays as it was.
  await page.goto("/teams");
  await expect(page.getByRole("heading", { level: 2, name: "Real Jaén" })).toBeVisible();
  await expect(page).toHaveURL(/\/teams$/);
  const before = await entries();

  // Choosing UD Ibiza replaces the URL: no reload, no history entry.
  if (testInfo.project.name === "mobile") await page.getByRole("button", { name: "Cambiar equipo" }).click();
  await page.locator("label").filter({ hasText: "UD Ibiza" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();
  await expect(page).toHaveURL(/\/teams[?]team=sofascore-team-24368$/);
  expect(await entries()).toBe(before);

  // UD Ibiza → a match → Back: the same team, season and tab.
  await page.getByRole("table", { name: "Partidos disputados" }).getByRole("link", { name: /^Ver partido/ }).first().click();
  await expect(page).toHaveURL(/\/matches\/sofascore-match-\d+[?]from=teams&team=sofascore-team-24368$/);
  await page.goBack();
  await expect(page.getByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Temporada" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Rendimiento del equipo" })).toHaveAttribute("aria-selected", "true");

  // «Volver al equipo» returns there too, not to Partidos.
  await page.getByRole("table", { name: "Partidos disputados" }).getByRole("link", { name: /^Ver partido/ }).first().click();
  await page.getByRole("link", { name: "‹ Volver al equipo" }).click();
  await expect(page).toHaveURL(/\/teams[?]team=sofascore-team-24368$/);
  await expect(page.getByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();

  // The players tab is kept too, also on reload.
  await page.getByRole("tab", { name: "Jugadores" }).click();
  await expect(page).toHaveURL(new RegExp(`team=sofascore-team-24368&tab=players$`));
  await page.reload();
  await expect(page.getByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");

  // A player and «Volver»: back to UD Ibiza's players.
  await page.getByRole("table", { name: "Jugadores observados" }).getByRole("link", { name: "Ver ficha de Moha Keita" }).click();
  await expect(page.getByRole("heading", { name: "Moha Keita" })).toBeVisible();
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "UD Ibiza" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");

  // An unknown or unavailable team: an explicit state, never the club in its place.
  await page.goto("/teams?team=team-montealto");
  await expect(page.getByRole("status")).toContainText("La fuente real no pudo resolver la identidad de este equipo.");
  await expect(page.getByRole("link", { name: "Ver el Real Jaén" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 2, name: "Real Jaén" })).toHaveCount(0);
  await expect(page).toHaveURL(/\/teams[?]team=team-montealto$/);
  expect(await noHorizontalOverflow(page)).toBe(true);
});
