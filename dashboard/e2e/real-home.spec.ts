import { expect, test, type Page } from "@playwright/test";
import { expectEntityAwareRowHover } from "./interactive-row";
import { REAL_SOURCE } from "./sources";

/*
 * «Inicio» with the real source. The e2e server is synthetic, so these tests
 * answer the browser's /api/v1 requests for the real sections themselves:
 * simulated responses, not the local API. The upcoming matches are far ahead
 * so that the server's clock always finds them in the future.
 */

const jaen = { id: "team-real-jaen", name: "Real Jaén" };
const rival = { id: "sofascore-team-24326", name: "Atlético Madrileño" };
const next = { id: "sofascore-team-4489", name: "Algeciras CF" };
const other = { id: "sofascore-team-2840", name: "Gimnàstic de Tarragona" };
type Team = { id: string; name: string };
const match = (id: number, kickoff: string, status: string, home: Team, away: Team, round: number,
  score: { home: number; away: number } | null = null) => ({
  id: `sofascore-match-${id}`, kickoff, status, competition: "Primera Federación, Grupo 2", season: "26/27", round, home, away, score,
});
const target = match(9, "2030-10-25T11:00:00+00:00", "scheduled", rival, jaen, 9);
const second = match(10, "2030-11-02T17:15:00+00:00", "scheduled", jaen, next, 10);
const postponed = match(5, "2026-09-27T16:15:00+00:00", "postponed", jaen, other, 5);
const played = [1, 2, 3, 4, 5, 6].map((n) => match(100 + n, `2026-09-${String(n * 4).padStart(2, "0")}T17:00:00+00:00`, "finished",
  n % 2 ? rival : other, n % 2 ? other : rival, n, { home: n % 3, away: 1 }));
const SOURCE = { synthetic: false, label: "Datos reales" };
const rowsOf = (matches: ReturnType<typeof match>[], team: { id: string }) =>
  matches.map((m) => ({ match: m, side: m.home.id === team.id ? "home" : "away" }));
const listing = (teamId: string, matches: ReturnType<typeof match>[], team: { id: string }) => ({
  teamId, matches: rowsOf(matches, team), competitions: [{ name: "Primera Federación, Grupo 2", season: "26/27" }],
  scope: "Partidos del equipo que registra la fuente.", source: SOURCE,
});
const missing = { value: null, reason: "not_available" };
const stats = (xg: number) => ({ possession: missing, shots: missing, shotsOnTarget: missing, xg: { value: xg }, corners: missing,
  fouls: missing, yellowCards: missing, redCards: missing });

async function serveReal(page: Page, { missingXgMatchId }: { missingXgMatchId?: string } = {}) {
  await page.route("**/api/v1/source", (route) =>
    route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/teams/*/observed-matches", (route) => {
    const teamId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    return route.fulfill({ json: teamId === jaen.id ? listing(teamId, [postponed, target, second], jaen)
      : teamId === rival.id ? listing(teamId, [...played, target], rival) : listing(teamId, [second], next) });
  });
  await page.route("**/api/v1/teams/*/observed-performance", (route) => {
    const teamId = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    const results = teamId === rival.id ? rowsOf(played, rival).map(({ match: m, side }) => {
      const own = m.score![side as "home" | "away"], against = m.score![side === "home" ? "away" : "home"];
      const xgFor = side === "home" ? 1.25 : 0.5;
      const xgAgainst = side === "home" ? 0.5 : 1.25;
      return { match: m, side, result: own > against ? "win" : own === against ? "draw" : "loss", goalsFor: own, goalsAgainst: against,
        xgFor: m.id === missingXgMatchId ? null : xgFor, xgAgainst: m.id === missingXgMatchId ? null : xgAgainst };
    }) : [];
    return route.fulfill({ json: { teamId, teams: [jaen, rival, next], scope: "Rendimiento.", source: SOURCE,
      competitions: [{ id: "sofascore-scope-94191-97382", name: "Primera Federación, Grupo 2", season: "26/27", results }] } });
  });
  await page.route("**/api/v1/matches/*/observed", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    const found = [...played, target, second].find((m) => m.id === id)!;
    return route.fulfill({ json: { match: found, statistics: { state: "stored", fetchedAt: null, home: stats(1.25), away: stats(0.5) },
      lineups: { state: "not_stored", fetchedAt: null }, incidents: { state: "not_stored", fetchedAt: null },
      scope: "Partido según la fuente.", source: SOURCE } });
  });
}
const noHorizontalOverflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("Inicio real: next current match, rival's last five before it, Volver a Inicio and a new target", async ({ page }) => {
  await serveReal(page);
  const synthetic: string[] = [];
  const matchDetails: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(competitions|seasons\/)/.test(r.url())) synthetic.push(r.url()); });
  page.on("request", (r) => { if (/\/api\/v1\/matches\/[^/]+\/observed$/.test(new URL(r.url()).pathname)) matchDetails.push(r.url()); });
  await page.goto("/");
  const header = page.getByRole("region", { name: "Encuentro de preparación" });
  await expect(header.getByText("Atlético Madrileño", { exact: true })).toBeVisible();
  await expect(header.getByText(/aplazado, pendiente de disputar sin nueva fecha confirmada/)).toBeVisible();
  await expect(page.getByText("Datos reales", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 2, name: "Últimos 5 partidos del Atlético Madrileño" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  const historyHeading = page.getByRole("heading", { name: "Últimos 5 partidos del Atlético Madrileño", exact: true });
  await expect(historyHeading).toHaveCSS("color", "rgb(75, 38, 119)");
  const accent = await historyHeading.evaluate(element => {
    const before = getComputedStyle(element, "::before");
    return { width: before.width, color: before.backgroundColor };
  });
  expect(accent).toEqual({ width: "4px", color: "rgb(212, 167, 44)" });
  const list = page.getByRole("list", { name: "Últimos partidos del rival" });
  await expect(list.getByRole("link")).toHaveCount(15);
  await expect(list.getByRole("link", { name: /^Ver partido/ }).first()).toHaveAttribute("href", "/matches/sofascore-match-106?from=home&match=sofascore-match-9");
  // xG from the performance response's individual values, oriented to the rival: 0.80 for and 0.95 against.
  const expectation = page.getByRole("term").filter({ hasText: "Expectativa" }).locator("xpath=following-sibling::dd");
  await expect(expectation).toHaveText(/0,80\s*xG a favor\s*0,95\s*xG en contra/);
  expect(matchDetails).toEqual([]);
  expect(await noHorizontalOverflow(page)).toBe(true);

  // A match opened here returns to this target.
  await list.getByRole("link", { name: /^Ver partido/ }).first().click();
  await expect(page).toHaveURL("/matches/sofascore-match-106?from=home&match=sofascore-match-9");
  await page.reload();
  await expect(page).toHaveURL("/matches/sofascore-match-106?from=home&match=sofascore-match-9");
  await page.getByRole("link", { name: "‹ Volver a Inicio" }).click();
  await expect(page).toHaveURL(/\/\?match=sofascore-match-9$/);
  await expect(header.getByText("Atlético Madrileño", { exact: true })).toBeVisible();

  // Another target: its rival and only its matches before that date.
  await page.getByRole("button", { name: "Cambiar rival" }).click();
  await page.getByRole("region", { name: "Próximos encuentros del Real Jaén" }).getByRole("button", { name: /Jornada 10/ }).click();
  await expect(page).toHaveURL(/\/\?match=sofascore-match-10$/);
  await expect(header.getByText("Algeciras CF", { exact: true })).toBeVisible();
  await expect(page.getByText("Todavía no hay partidos finalizados con fecha anterior al encuentro objetivo.")).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
  expect(synthetic).toEqual([]);
});

test("Inicio real keeps a partial xG value readable on a phone", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "mobile", "Partial coverage is checked in the phone layout.");
  await serveReal(page, { missingXgMatchId: "sofascore-match-106" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const summary = page.locator('dl[aria-label="Síntesis de partidos recientes"]');
  await expect(summary).toBeVisible();
  await expect(summary.getByText("4/5 partidos con dato")).toHaveCount(2);
  const partialValues = summary.getByTitle("Datos disponibles en 4 de 5 partidos");
  await expect(partialValues).toHaveCount(2);
  await expect(partialValues.first()).toContainText("0,");
  await expect(partialValues.nth(1)).toContainText("0,");
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("partial-xg-phone.png"), fullPage: true });
});

test("Inicio real waits for the rival synthesis and does not fetch match details just for xG", async ({ page }) => {
  await serveReal(page);
  let release!: () => void;
  let markStarted!: () => void;
  const responseGate = new Promise<void>((resolve) => { release = resolve; });
  const requestStarted = new Promise<void>((resolve) => { markStarted = resolve; });
  const matchDetails: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/matches\/[^/]+\/observed$/.test(new URL(r.url()).pathname)) matchDetails.push(r.url()); });
  await page.route("**/api/v1/teams/*/observed-performance", async (route) => {
    markStarted();
    await responseGate;
    await route.fallback();
  });

  await page.goto("/");
  await requestStarted;
  const header = page.getByRole("region", { name: "Encuentro de preparación" });
  await expect(header.getByText("Atlético Madrileño", { exact: true })).toBeVisible();
  await expect(page.getByText("Cargando partidos del rival…")).toBeVisible();
  await expect(page.getByText("Cargando xG…")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Últimos 5 partidos del Atlético Madrileño" })).toHaveCount(0);

  release();
  const expectation = page.getByRole("term").filter({ hasText: "Expectativa" }).locator("xpath=following-sibling::dd");
  await expect(page.getByRole("heading", { name: "Últimos 5 partidos del Atlético Madrileño" })).toBeVisible();
  await expect(expectation).toHaveText(/0,80\s*xG a favor\s*0,95\s*xG en contra/);
  expect(matchDetails).toEqual([]);
});

test("Inicio real opens both target teams and the teams and scores in its history", async ({ page }) => {
  await serveReal(page);
  const identities = Object.fromEntries([jaen, rival, next, other].map(team => [team.id, team.name]));
  await page.route("**/api/v1/teams/*", route => {
    const segments = new URL(route.request().url()).pathname.split("/").filter(Boolean);
    if (segments.length !== 4) return route.fallback();
    const id = decodeURIComponent(segments[3]);
    const name = identities[id];
    return route.fulfill(name
      ? { json: { id, name } }
      : { status: 404, json: { error: { code: "NOT_FOUND", message: "Equipo no identificado." } } });
  });
  await page.goto("/");
  await expect(page.getByRole("list", { name: "Últimos partidos del rival" })).toBeVisible();
  const first = page.getByRole("list", { name: "Últimos partidos del rival" }).getByRole("listitem").first();
  const historyTeams = first.getByRole("link", { name: /^Ver equipo/ });
  await expect(historyTeams).toHaveCount(2);
  const score = first.getByRole("link", { name: /^Ver partido/ });
  await expect(score).toHaveAttribute("href", "/matches/sofascore-match-106?from=home&match=sofascore-match-9");
  const historyTeamName = (await historyTeams.first().getAttribute("aria-label"))!.replace(/^Ver equipo /, "");
  // Keep the loaded history while destinations are served as known teams without their own export.
  await page.route("**/api/v1/teams/*/observed-performance", route =>
    route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "El equipo no tiene exportación propia." } } }));
  await historyTeams.first().click();
  await expect(page.getByRole("heading", { level: 2, name: historyTeamName })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("identificado, pero no tiene estadísticas propias disponibles");
});

test("Inicio real keeps team names independent from the recent-match row action", async ({ page }, testInfo) => {
  await serveReal(page);
  const identities = Object.fromEntries([jaen, rival, next, other].map(team => [team.id, team.name]));
  await page.route("**/api/v1/teams/*", route => {
    const segments = new URL(route.request().url()).pathname.split("/").filter(Boolean);
    if (segments.length !== 4) return route.fallback();
    const id = decodeURIComponent(segments[3]);
    const name = identities[id];
    return route.fulfill(name
      ? { json: { id, name } }
      : { status: 404, json: { error: { code: "NOT_FOUND", message: "Equipo no identificado." } } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");

  const list = page.getByRole("list", { name: "Últimos partidos del rival" });
  const item = list.getByRole("listitem").first();
  const row = item.locator(":scope > div");
  const teamLink = item.getByRole("link", { name: /^Ver equipo/ }).first();
  if (testInfo.project.name === "desktop") {
    await expectEntityAwareRowHover(page, row, row.locator(":scope > div").first(), teamLink);
  }
  const teamHref = new URL((await teamLink.getAttribute("href"))!, page.url()).href;
  await expect(teamLink.locator("[data-crest]")).toHaveCount(0);
  const teamBox = await teamLink.boundingBox();
  expect(teamBox).not.toBeNull();
  await page.mouse.click(teamBox!.x + teamBox!.width / 2, teamBox!.y + teamBox!.height / 2);
  await expect(page).toHaveURL(teamHref);
  await page.goBack();
  await expect(list).toBeVisible();

  const nameBox = await item.getByRole("link", { name: /^Ver equipo/ }).first().boundingBox();
  const rowBox = await row.boundingBox();
  expect(nameBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  for (const x of [nameBox!.x - 2, nameBox!.x + nameBox!.width + 2]) {
    expect(x).toBeGreaterThan(rowBox!.x);
    expect(x).toBeLessThan(rowBox!.x + rowBox!.width);
    const matchHref = new URL((await item.getByRole("link", { name: /^Ver partido/ }).getAttribute("href"))!, page.url()).href;
    await page.mouse.click(x, nameBox!.y + nameBox!.height / 2);
    await expect(page).toHaveURL(matchHref);
    await expect(page.getByRole("link", { name: "‹ Volver a Inicio" })).toBeVisible();
    await page.goBack();
    await expect(list).toBeVisible();
  }

  const score = item.getByRole("link", { name: /^Ver partido/ });
  const scoreHref = new URL((await score.getAttribute("href"))!, page.url()).href;
  await score.click();
  await expect(page).toHaveURL(scoreHref);
  await page.goBack();
  await expect(list).toBeVisible();

  const crestBox = await row.locator("[data-crest]").first().boundingBox();
  expect(crestBox).not.toBeNull();
  const crestMatchHref = new URL((await item.getByRole("link", { name: /^Ver partido/ }).getAttribute("href"))!, page.url()).href;
  await page.mouse.click(crestBox!.x + crestBox!.width / 2, crestBox!.y + crestBox!.height / 2);
  await expect(page).toHaveURL(crestMatchHref);
});

test("Inicio target teams open a known identity-only team view", async ({ page }) => {
  await serveReal(page);
  const identities = Object.fromEntries([jaen, rival, next, other].map(team => [team.id, team.name]));
  await page.route("**/api/v1/teams/*/observed-performance", route =>
    route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "El equipo no tiene exportación propia." } } }));
  await page.route("**/api/v1/teams/*", route => {
    const segments = new URL(route.request().url()).pathname.split("/").filter(Boolean);
    if (segments.length !== 4) return route.fallback();
    const id = decodeURIComponent(segments[3]);
    const name = identities[id];
    return route.fulfill(name
      ? { json: { id, name } }
      : { status: 404, json: { error: { code: "NOT_FOUND", message: "Equipo no identificado." } } });
  });
  for (const name of ["Atlético Madrileño", "Real Jaén"]) {
    await page.goto("/");
    const target = page.getByRole("region", { name: "Encuentro de preparación" });
    await target.getByRole("link", { name: `Ver equipo ${name}` }).click();
    await expect(page.getByRole("heading", { level: 2, name })).toBeVisible();
    await expect(page.getByRole("status")).toContainText("identificado, pero no tiene estadísticas propias disponibles");
  }
});
