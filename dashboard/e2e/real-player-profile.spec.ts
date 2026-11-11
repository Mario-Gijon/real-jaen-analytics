import { expect, test, type Page } from "@playwright/test";
import { expectEntityAwareRowHover } from "./interactive-row";
import { REAL_SOURCE } from "./sources";

/*
 * Navigation from the real observed players to a real profile. The e2e server
 * is synthetic, so these tests answer the browser's /api/v1 requests for the
 * real sections themselves: simulated responses, not the local API.
 */

const SCOPE = "Jugadores observados en convocatorias; no es una plantilla completa ni necesariamente actual.";
const REAL_DATA = { synthetic: false, label: "Datos reales" };
const observed = {
  teamId: "team-real-jaen",
  players: [
    { id: "sofascore-player-966303", name: "Moha Keita", position: "midfielder" },
    { id: "sofascore-player-1429049", name: "Jugador Sin Estatura", position: "midfielder" },
    { id: "sofascore-player-1", name: "Jugador Inexistente", position: null },
  ],
  scope: SCOPE,
  source: { synthetic: false, label: "Datos reales" },
};
const profile = (player: Record<string, unknown>) => ({
  player,
  scope: "Datos personales según la fuente. No indican equipo actual.",
  source: { synthetic: false, label: "Datos reales" },
});
const PROFILES: Record<string, { status: number; body: unknown }> = {
  "sofascore-player-966303": { status: 200, body: profile({ id: "sofascore-player-966303", name: "Moha Keita",
    position: "midfielder", birthDate: "1999-09-18", heightCm: 165, preferredFoot: null, country: "Senegal" }) },
  "sofascore-player-1429049": { status: 200, body: profile({ id: "sofascore-player-1429049", name: "Jugador Sin Estatura",
    position: "midfielder", birthDate: "2000-08-24", heightCm: null, preferredFoot: null, country: null }) },
  "sofascore-player-1": { status: 404, body: { error: { code: "NOT_FOUND", message: "Jugador no encontrado en la fuente de datos." } } },
};

const missing = { value: null, reason: "not_available" };
const KEYS = ["minutes", "goals", "assists", "shots", "shotsOnTarget", "xg", "passesCompleted", "passes", "passAccuracy", "dribbles",
  "dribblesWon", "recoveries", "possessionLost", "duelsWon", "duelsLost", "fouls", "saves", "rating"];
const stats = (values: Record<string, number | Record<string, unknown>> = {}) => Object.fromEntries(KEYS.map((key) => {
  const value = values[key];
  return [key, value === undefined ? missing : typeof value === "number" ? { value } : value];
}));
/** As the service normalizes Moha Keita against Rayo Majadahonda: 2 shots, both blocked. */
const DEDUCED_ZERO = { value: 0, derivation: { source: "derived", rule: "shots_partition" } };
const accuracy = (completed: number, passes: number) =>
  ({ value: (completed / passes) * 100, derivation: { source: "calculated", rule: "ratio" } });
const observedMatches = <T>(playerId: string, matches: T[]) => ({
  playerId, matches, ratingSource: "SofaScore", source: { synthetic: false, label: "Datos reales" },
  scope: "Partidos en los que la fuente registra al jugador en la convocatoria. No garantiza cobertura completa ni indica equipo actual.",
});
const MOHA_MATCHES = observedMatches("sofascore-player-966303", [
  { match: { id: "sofascore-match-16599928", kickoff: "2026-10-18T16:15:00+00:00", status: "finished",
    competition: "Primera Federacion, Group 2", season: "26/27", round: 10,
    home: { id: "team-real-jaen", name: "Real Jaén" }, away: { id: "sofascore-team-1", name: "Rayo Majadahonda" },
    score: { home: 1, away: 0 } },
    side: "home", role: "starter", position: "midfielder", participation: "confirmed",
    stats: stats({ minutes: 90, assists: 0, shots: 2, shotsOnTarget: DEDUCED_ZERO, xg: 0.31, passesCompleted: 23, passes: 29,
      passAccuracy: accuracy(23, 29), rating: 7 }) },
  { match: { id: "sofascore-match-16599500", kickoff: "2026-09-20T16:00:00+00:00", status: "finished",
    competition: "Primera Federacion, Group 2", season: "26/27", round: 5,
    home: { id: "sofascore-team-3", name: "Antequera CF" }, away: { id: "team-real-jaen", name: "Real Jaén" },
    score: { home: 0, away: 2 } },
    side: "away", role: "substitute", position: "forward", participation: "confirmed",
    stats: stats({ minutes: 22, goals: 1, assists: 1, shots: 1, shotsOnTarget: 1, passes: 8, passesCompleted: 6,
      passAccuracy: accuracy(6, 8), duelsWon: 3, rating: 7.6 }) },
  { match: { id: "sofascore-match-16599388", kickoff: "2026-09-06T09:30:00+00:00", status: "finished",
    competition: "Primera Federacion, Group 2", season: "26/27", round: 2,
    home: { id: "team-real-jaen", name: "Real Jaén" }, away: { id: "sofascore-team-2", name: "Huesca" },
    score: { home: 2, away: 2 } },
    side: "home", role: "substitute", position: "midfielder", participation: "unknown", stats: stats() },
]);

/** Real «Equipos» of the club, only as the way in: a competition without played matches and the observed squad. */
const GROUP_2 = "sofascore-scope-94191-97382";
const zero = { value: 0, coverage: "complete", matches: 1, of: 1 };
const teamPerformance = {
  teamId: "team-real-jaen", teams: [{ id: "team-real-jaen", name: "Real Jaén" }], source: REAL_DATA, scope: "Resultados observados.",
  competitions: [{ id: GROUP_2, name: "Primera Federación, Grupo 2", season: "26/27",
    matches: { inScope: 0, finished: 0, pending: 0, counted: 0, finishedWithoutValidScore: 0, excludedByVersionConflict: 0, unknownStatus: 0, replacedLocally: 0 },
    record: { played: 0, wins: 0, draws: 0, losses: 0, goalsFor: 0, goalsAgainst: 0, goalDifference: 0 },
    venues: { home: { win: 0, draw: 0, loss: 0 }, away: { win: 0, draw: 0, loss: 0 } }, results: [], output: null }],
};
const teamSquad = {
  teamId: "team-real-jaen", source: REAL_DATA, scope: SCOPE,
  competitions: [{ id: GROUP_2, name: "Primera Federación, Grupo 2", season: "26/27", matches: { counted: 1, withCallUps: 1 },
    callUps: { total: 3, played: 0, unused: 3, unknown: 0, contradictions: 0, mismatches: 0, teamWarnings: 0, duplicateRecords: 0, discardedRecords: 0 },
    players: observed.players.map((player) => ({ player, heightCm: null, preferredFoot: null, lastShirtNumber: null, callUps: 1,
      stats: { appearances: zero, starts: zero, minutes: zero, goals: zero, assists: zero } })) }],
};

async function serveReal(page: Page, { profileDown = false, matchesDown = false } = {}) {
  await page.route("**/api/v1/source", (route) => route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/teams/team-real-jaen/observed-performance", (route) => route.fulfill({ json: teamPerformance }));
  await page.route("**/api/v1/teams/team-real-jaen/observed-squad", (route) => route.fulfill({ json: teamSquad }));
  await page.route("**/api/v1/players/*/observed-matches", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    if (matchesDown) {
      return route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "La fuente de datos real no está disponible." } } });
    }
    return route.fulfill({ json: id === "sofascore-player-966303" ? MOHA_MATCHES : observedMatches(id, []) });
  });
  await page.route("**/api/v1/players/*/profile", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    if (profileDown) {
      return route.fulfill({ status: 503, json: { error: { code: "UPSTREAM_UNAVAILABLE", message: "La fuente de datos real no está disponible." } } });
    }
    const answer = PROFILES[id];
    return route.fulfill({ status: answer.status, json: answer.body });
  });
}

const noHorizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("Real players: list to profile, personal data without provenance and back", async ({ page }, testInfo) => {
  await serveReal(page);
  const synthetic: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/players\/[^/]+(\?|$)/.test(request.url())) synthetic.push(request.url());
  });
  await page.goto("/teams?tab=players");
  const table = page.getByRole("table", { name: /Jugadores observados/ });
  await expect(table).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);

  await table.getByRole("link", { name: "Ver ficha de Moha Keita" }).click();
  await expect(page).toHaveURL(/\/players\/sofascore-player-966303\?view=profile&season=[^&]+&from=team&origin=team-real-jaen$/);
  await expect(page.getByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
  await expect(page.getByText("Senegal")).toBeVisible();
  await expect(page.getByText("1,65 m")).toBeVisible();
  await expect(page.getByText("Datos reales", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Datos personales según la fuente. No indican equipo actual./)).toHaveCount(0);
  await expect(page.getByText(/Partidos en los que la fuente registra al jugador/)).toHaveCount(0);
  await expect(page.getByRole("tab")).toHaveCount(0);
  const history = page.getByRole("list", { name: "Histórico de partidos" });
  await expect(history.getByRole("button")).toHaveCount(3);
  await expect(history.getByRole("listitem").nth(0)).toContainText("Rayo Majadahonda");
  await expect(history.getByRole("button").nth(0)).toHaveAttribute("aria-pressed", "true");
  await expect(history.getByRole("listitem").nth(2)).toContainText("J2Huesca2–2");
  await expect(history.getByRole("listitem").nth(0).getByRole("link", { name: "Ver equipo Rayo Majadahonda" }))
    .toHaveAttribute("href", "/teams?team=sofascore-team-1");
  await expect(page.getByRole("combobox", { name: "Ir a jornada" })).toHaveValue("sofascore-match-16599928");
  // The selected match opens its real detail, never a synthetic one.
  await expect(page.getByRole("link", { name: "Ver partido completo" }).first()).toHaveAttribute("href", /^\/matches\/sofascore-match-\d+\?from=player&/);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-profile.png"), fullPage: true });
  // The real profile never asks for a synthetic season profile.
  expect(synthetic).toEqual([]);

  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("table", { name: /Jugadores observados/ })).toBeVisible();
});

test("Real players: unknown fields read «—» and a missing player has its own state", async ({ page }) => {
  await serveReal(page);
  await page.goto("/players/sofascore-player-1429049?view=profile");
  await expect(page.getByRole("heading", { level: 1, name: "Jugador Sin Estatura" })).toBeVisible();
  for (const label of ["Estatura", "Pierna dominante", "País"]) {
    await expect(page.locator("dt", { hasText: label }).locator("+ dd")).toHaveText("—");
  }
  await expect(page.getByText("La fuente no tiene partidos observados de este jugador.")).toBeVisible();

  await page.goto("/players/sofascore-player-1?view=profile");
  await expect(page.locator("main").getByRole("alert")).toContainText("Jugador no encontrado");
  await expect(page.getByRole("button", { name: "Reintentar" })).toHaveCount(0);
});

test("Real players: an unavailable source shows an error with retry", async ({ page }) => {
  await serveReal(page, { profileDown: true });
  await page.goto("/players/sofascore-player-966303?view=profile");
  await expect(page.locator("main").getByRole("alert")).toContainText("La fuente de datos real no está disponible.");
  await page.unroute("**/api/v1/players/*/profile");
  await serveReal(page);
  await page.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
});

test("Real players: failing matches keep the personal data and retry on their own", async ({ page }) => {
  await serveReal(page, { matchesDown: true });
  await page.goto("/players/sofascore-player-966303?view=profile");
  const section = page.getByRole("region", { name: "Partidos" });
  await expect(section.getByRole("alert")).toContainText("No se pudieron cargar los partidos observados.");
  await expect(page.getByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
  await expect(page.getByText("Senegal")).toBeVisible();
  await page.unroute("**/api/v1/players/*/observed-matches");
  await serveReal(page);
  await section.getByRole("button", { name: "Reintentar" }).click();
  await expect(page.getByRole("list", { name: "Histórico de partidos" })).toBeVisible();
});

const RAYO = (MOHA_MATCHES.matches[0] as { match: { id: string } }).match;
const ANTEQUERA = (MOHA_MATCHES.matches[1] as { match: { id: string } }).match;
const lineupPlayer = (id: number, name: string, role = "starter") => ({
  player: { id: `sofascore-player-${id}`, name, position: "midfielder" }, shirtNumber: id % 100,
  role, participation: "confirmed", minutes: { value: 90 },
});
const RAYO_DETAIL = {
  match: RAYO, scope: "Partido, estadísticas colectivas, alineaciones e incidencias según la fuente.",
  source: { synthetic: false, label: "Datos reales" },
  statistics: { state: "not_stored", fetchedAt: null },
  incidents: { state: "not_stored", fetchedAt: null },
  lineups: { state: "stored", fetchedAt: "2026-10-21T12:07:31+00:00", confirmed: true,
    home: [lineupPlayer(966303, "Moha Keita"), ...Array.from({ length: 10 }, (_, i) => lineupPlayer(i + 1, `Jaén ${i + 1}`))],
    away: [lineupPlayer(1429049, "Jugador Sin Estatura"), ...Array.from({ length: 10 }, (_, i) => lineupPlayer(i + 20, `Rayo ${i + 20}`))] },
};

/** Real profiles and real matches both enabled; the match detail is simulated too. */
async function serveRealWithMatches(page: Page) {
  await serveReal(page);
  await page.route("**/api/v1/source", (route) =>
    route.fulfill({ json: REAL_SOURCE }));
  await page.route("**/api/v1/matches/*/observed", (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    return id === RAYO.id || id === ANTEQUERA.id
      ? route.fulfill({ json: { ...RAYO_DETAIL, match: id === RAYO.id ? RAYO : ANTEQUERA } })
      : route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Partido no encontrado en la fuente real de partidos." } } });
  });
}

const visibleDetail = (page: Page) => page.locator("#player-match-detail-panel:visible, #player-inline-detail:visible");
const metric = (page: Page, label: string) => visibleDetail(page).locator("dt", { hasText: new RegExp(`^${label}$`) }).locator("+ dd");

test("Real players: compact goalkeeper block with saves and no empty field categories", async ({ page }, testInfo) => {
  await serveReal(page);
  await page.route("**/api/v1/players/*/profile", (route) => route.fulfill({ json: profile({
    id: "sofascore-player-966303", name: "Portero de prueba", position: "goalkeeper", birthDate: null,
    heightCm: 190, preferredFoot: null, country: "Spain",
  }) }));
  await page.route("**/api/v1/players/*/observed-matches", (route) => route.fulfill({ json: observedMatches("sofascore-player-966303", [
    { ...MOHA_MATCHES.matches[0], position: "goalkeeper", stats: stats({ saves: 4, minutes: 90 }) },
  ]) }));
  await page.goto("/players/sofascore-player-966303?view=profile");
  await expect(page.getByText("España", { exact: true })).toBeVisible();
  await expect(metric(page, "Paradas")).toHaveText("4");
  await expect(visibleDetail(page).locator("dt")).toHaveCount(1);
  const block = page.getByRole("region", { name: "Portería", exact: true });
  const bounds = await block.boundingBox();
  expect(bounds!.width).toBeLessThanOrEqual(240);
  expect(bounds!.height).toBeLessThan(180);
  await expect(page.getByRole("region", { name: "Juego", exact: true })).toHaveCount(0);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("profile-keeper.png"), fullPage: true });
});

test("Real players: many match cards, repeated rounds, jump synchronization and one-match profiles", async ({ page }, testInfo) => {
  await serveReal(page);
  const base = MOHA_MATCHES.matches[0];
  const many = Array.from({ length: 15 }, (_, index) => ({ ...base,
    match: { ...base.match, id: `sofascore-match-${index + 1}`, round: 10 - Math.floor(index / 2),
      kickoff: new Date(Date.UTC(2026, 9, 20 - index)).toISOString(),
      away: { id: `sofascore-team-${index + 1}`, name: index === 14 ? "Juventud Torremolinos CF" : `Rival ${index + 1}` } },
  }));
  await page.route("**/api/v1/players/*/observed-matches", (route) => route.fulfill({ json: observedMatches("sofascore-player-966303", many) }));
  await page.goto("/players/sofascore-player-966303?view=profile&match=sofascore-match-15");
  const history = page.getByRole("list", { name: "Histórico de partidos" });
  const cards = history.getByRole("button");
  const jump = page.getByRole("combobox", { name: "Ir a jornada" });
  await expect(cards).toHaveCount(15);
  await expect(jump).toHaveValue("sofascore-match-15");
  await expect(cards.last()).toHaveAttribute("aria-pressed", "true");
  await expect.poll(() => history.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
  await expect(cards.last()).toBeInViewport({ ratio: 1 });
  const shortBounds = await cards.first().boundingBox();
  const longBounds = await cards.last().boundingBox();
  expect(longBounds!.height).toBe(shortBounds!.height);
  const longName = history.getByRole("listitem").last().getByRole("link", { name: "Ver equipo Juventud Torremolinos CF" });
  expect(await longName.evaluate(element => element.scrollHeight)).toBeLessThanOrEqual(40);
  await page.screenshot({ path: testInfo.outputPath("profile-cards-long-name.png"), fullPage: true });
  await expect(jump.locator("option").nth(0)).toContainText("J10 · Rival 1");
  await expect(jump.locator("option").nth(1)).toContainText("J10 · Rival 2");
  const pageScroll = await page.evaluate(() => window.scrollY);
  await jump.selectOption("sofascore-match-1");
  await expect(cards.first()).toHaveAttribute("aria-pressed", "true");
  await expect(cards.first()).toBeInViewport({ ratio: 1 });
  expect(await page.evaluate(() => window.scrollY)).toBe(pageScroll);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("profile-many-matches.png"), fullPage: true });
  // Selection from a card updates the same selector and URL.
  await cards.nth(1).click({ position: { x: 6, y: 6 } });
  await expect(jump).toHaveValue("sofascore-match-2");
  await expect(page).toHaveURL(/match=sofascore-match-2$/);
  await page.unroute("**/api/v1/players/*/observed-matches");
  await page.route("**/api/v1/players/*/observed-matches", (route) => route.fulfill({ json: observedMatches("sofascore-player-966303", [many[0]]) }));
  await page.goto("/players/sofascore-player-966303?view=profile");
  await expect(cards).toHaveCount(1);
  await expect(jump.locator("option")).toHaveCount(1);
  await expect(jump).toHaveValue("sofascore-match-1");
  await page.screenshot({ path: testInfo.outputPath("profile-one-match.png"), fullPage: true });
});

test("Real players: horizontal match selector, selection in place, kept on reload and on return from the match", async ({ page }, testInfo) => {
  await serveRealWithMatches(page);
  await page.goto("/teams?tab=players");
  await page.getByRole("table", { name: /Jugadores observados/ }).getByRole("link", { name: "Ver ficha de Moha Keita" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Moha Keita" })).toBeVisible();
  // From the profile to the match and back nothing synthetic is asked (the team view itself is).
  const synthetic: string[] = [];
  page.on("request", (r) => { if (/\/api\/v1\/(competitions|matches\/[^/]+$|players\/[^/]+$)/.test(new URL(r.url()).pathname)) synthetic.push(r.url()); });
  const history = page.getByRole("list", { name: "Histórico de partidos" });
  const rows = history.getByRole("button");
  const rowItems = history.getByRole("listitem");

  // Default: the newest appearance, with real values, real zeros and missing values apart.
  await expect(rows.nth(0)).toHaveAttribute("aria-pressed", "true");
  await expect(visibleDetail(page).getByText("Centrocampista", { exact: true })).toBeVisible();
  await expect(visibleDetail(page)).not.toContainText(/Valoración|SofaScore|Primera Federacion|26\/27/);
  const jump = page.getByRole("combobox", { name: "Ir a jornada" });
  await expect(jump).toHaveValue("sofascore-match-16599928");
  await expect(jump.locator("option").first()).toHaveText("J10 · Rayo Majadahonda");
  await expect(rowItems.first().getByLabel("90 minutos jugados", { exact: true })).toBeVisible();
  const headingBounds = await page.getByRole("heading", { name: "Partidos", exact: true }).boundingBox();
  const selectorBounds = await jump.boundingBox();
  if (testInfo.project.name === "desktop") {
    expect(Math.abs(selectorBounds!.y + selectorBounds!.height / 2 - headingBounds!.y - headingBounds!.height / 2)).toBeLessThan(2);
  } else {
    expect(selectorBounds!.y - headingBounds!.y - headingBounds!.height).toBeLessThan(16);
  }
  await expect(page.getByText("Ir a jornada", { exact: true })).toHaveCount(0);
  await expect(rowItems.first().getByRole("img", { name: "Local" })).toBeVisible();
  await expect(rowItems.nth(1).getByRole("img", { name: "Visitante" })).toBeVisible();
  const cardGeometry = await rowItems.evaluateAll(items => items.map(item => {
    const card = item.firstElementChild!;
    const bounds = card.getBoundingClientRect();
    const footer = card.lastElementChild!;
    const score = footer.firstElementChild!.getBoundingClientRect();
    const icon = footer.lastElementChild!.getBoundingClientRect();
    const opponent = card.children[1].getBoundingClientRect();
    return { height: bounds.height, scoreY: score.y, iconY: icon.y, scoreRight: score.right, iconX: icon.x, opponentBottom: opponent.bottom };
  }));
  expect(new Set(cardGeometry.map(card => card.height)).size).toBe(1);
  for (const card of cardGeometry) {
    expect(Math.abs(card.scoreY - card.iconY)).toBeLessThan(5);
    expect(card.scoreRight).toBeLessThan(card.iconX);
    expect(card.opponentBottom).toBeLessThanOrEqual(card.scoreY);
  }
  await expect(visibleDetail(page).getByText("Local", { exact: true })).toBeVisible();
  const finish = await page.getByRole("region", { name: "Finalización", exact: true }).boundingBox();
  const recovery = await page.getByRole("region", { name: "Recuperación", exact: true }).boundingBox();
  const game = await page.getByRole("region", { name: "Juego", exact: true }).boundingBox();
  if (testInfo.project.name === "desktop") {
    expect(recovery!.y - finish!.y - finish!.height).toBeLessThanOrEqual(17);
    expect(game!.x).toBeGreaterThan(finish!.x);
  } else {
    expect(game!.y).toBeGreaterThan(finish!.y);
    expect(recovery!.y).toBeGreaterThan(game!.y);
  }
  await expect(metric(page, "Pases")).toHaveText("29");
  await expect(metric(page, "Precisión de pase")).toHaveText("79,3 %");
  // Deduced by the service: a visible mark, its meaning under the stats, and the word for screen readers.
  await expect(metric(page, "Tiros a puerta")).toHaveText("0* (deducido)");
  await expect(visibleDetail(page).getByRole("list", { name: "Notas sobre los datos" }))
    .toHaveText("* Deducido: la fuente no lo da, pero sus tiros fuera y bloqueados suman todos sus tiros (o no tiró).");
  await expect(metric(page, "Goles")).toHaveText("—");
  await expect(metric(page, "Duelos ganados")).toHaveText("—");
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("real-profile-matches.png"), fullPage: true });

  // Keyboard: another match is selected in place, without leaving the profile.
  const profilePath = new URL(page.url()).pathname;
  await rows.nth(1).focus();
  await page.keyboard.press("Enter");
  await expect(rows.nth(1)).toHaveAttribute("aria-pressed", "true");
  await expect(rowItems.nth(1)).toContainText("J522′Antequera CF0–2");
  await expect(rowItems.nth(1).getByLabel("22 minutos jugados", { exact: true })).toBeVisible();
  await expect(jump).toHaveValue("sofascore-match-16599500");
  await expect(metric(page, "Goles")).toHaveText("1");
  await expect(metric(page, "Asistencias")).toHaveText("1");
  await expect(visibleDetail(page).getByText("Suplente", { exact: true })).toBeVisible();
  await expect(visibleDetail(page).getByText("22 min", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/&match=sofascore-match-16599500$/);
  expect(new URL(page.url()).pathname).toBe(profilePath);

  // A call-up without confirmed participation: no minutes nor stats.
  await jump.selectOption("sofascore-match-16599388");
  await expect(rows.nth(2)).toHaveAttribute("aria-pressed", "true");
  await expect(visibleDetail(page)).toContainText("Los datos no permiten confirmar su participación");
  await expect(visibleDetail(page).locator("dt")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("real-profile-unconfirmed.png"), fullPage: true });

  // Reload keeps the selection.
  await rows.nth(1).click({ position: { x: 6, y: 6 } });
  await page.reload();
  await expect(history.getByRole("button").nth(1)).toHaveAttribute("aria-pressed", "true");
  const profileUrl = page.url();

  // «Ver partido completo»: a real link (also in another tab) to the general detail, and back with the selection.
  const full = page.getByRole("link", { name: "Ver partido completo" });
  await expect(full).toHaveAttribute("href",
    /^\/matches\/sofascore-match-16599500\?from=player&player=sofascore-player-966303&season=[^&]+&playerFrom=team&playerOrigin=team-real-jaen$/);
  await expect(full).toHaveAttribute("href", /\/matches\/sofascore-match-16599500\?from=player/);
  await full.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { level: 1, name: "Antequera CF contra Real Jaén" })).toBeVisible();
  await page.getByRole("link", { name: "‹ Volver al jugador" }).click();
  await expect(page).toHaveURL(profileUrl);
  await expect(page.getByRole("list", { name: "Histórico de partidos" }).getByRole("button").nth(1)).toHaveAttribute("aria-pressed", "true");
  expect(synthetic).toEqual([]);
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page).toHaveURL(/\/teams\?season=[^&]+&team=team-real-jaen&tab=players$/);
  await expect(page.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");
});

test("Real player match: an identified rival opens its identity-only team view", async ({ page }) => {
  await serveReal(page);
  await page.route("**/api/v1/teams/sofascore-team-1/observed-performance", (route) =>
    route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "No tiene exportación propia." } } }));
  await page.route("**/api/v1/teams/sofascore-team-1", (route) =>
    route.fulfill({ json: { id: "sofascore-team-1", name: "Rayo Majadahonda" } }));
  await page.goto("/players/sofascore-player-966303?view=profile");
  const history = page.getByRole("list", { name: "Histórico de partidos" });
  const rival = history.getByRole("listitem").first().getByRole("link", { name: "Ver equipo Rayo Majadahonda" });
  await expect(rival).toHaveAttribute("href", "/teams?team=sofascore-team-1");
  await rival.click();
  await expect(page).toHaveURL("/teams?team=sofascore-team-1");
  await expect(page.getByRole("heading", { level: 2, name: "Rayo Majadahonda" })).toBeVisible();
  await expect(page.getByRole("status")).toContainText("identificado, pero no tiene estadísticas propias disponibles");
  await expect(page.getByLabel("Balance")).toHaveCount(0);
});

test("Real player history: the opponent name opens the team; card space and score keep selecting", async ({ page }, testInfo) => {
  await serveReal(page);
  await page.route("**/api/v1/teams/sofascore-team-1", route =>
    route.fulfill({ json: { id: "sofascore-team-1", name: "Rayo Majadahonda" } }));
  await page.route("**/api/v1/teams/sofascore-team-1/observed-performance", route =>
    route.fulfill({ status: 404, json: { error: { code: "NOT_FOUND", message: "Sin análisis propio." } } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/players/sofascore-player-966303?view=profile");
  const history = page.getByRole("list", { name: "Histórico de partidos" });
  const cards = history.getByRole("button", { name: /^Seleccionar/ });
  const first = cards.nth(0);
  const second = cards.nth(1);
  const firstCard = first.locator("xpath=..");
  const secondCard = second.locator("xpath=..");
  await expect(first).toHaveAttribute("aria-pressed", "true");
  if (testInfo.project.name === "desktop") {
    await expectEntityAwareRowHover(page, secondCard, secondCard.locator(":scope > span").first(),
      secondCard.getByRole("link", { name: /^Ver equipo/ }));
  }

  const opponent = firstCard.getByRole("link", { name: "Ver equipo Rayo Majadahonda" });
  await expect(opponent.locator("[data-crest]")).toHaveCount(0);
  const teamHref = new URL((await opponent.getAttribute("href"))!, page.url()).href;
  const opponentBox = await opponent.boundingBox();
  expect(opponentBox).not.toBeNull();
  await page.mouse.click(opponentBox!.x + opponentBox!.width / 2, opponentBox!.y + opponentBox!.height / 2);
  await expect(page).toHaveURL(teamHref);
  await expect(page.getByRole("heading", { level: 2, name: "Rayo Majadahonda" })).toBeVisible();
  await page.goBack();
  await expect(first).toHaveAttribute("aria-pressed", "true");

  const secondLinkBox = await secondCard.getByRole("link").boundingBox();
  const secondBox = await secondCard.boundingBox();
  expect(secondLinkBox).not.toBeNull();
  expect(secondBox).not.toBeNull();
  const beforeX = secondLinkBox!.x - 2;
  expect(beforeX).toBeGreaterThan(secondBox!.x);
  await page.mouse.click(beforeX, secondLinkBox!.y + secondLinkBox!.height / 2);
  await expect(second).toHaveAttribute("aria-pressed", "true");

  const third = cards.nth(2);
  const thirdCard = third.locator("xpath=..");
  const thirdLinkBox = await thirdCard.getByRole("link").boundingBox();
  const thirdBox = await thirdCard.boundingBox();
  expect(thirdLinkBox).not.toBeNull();
  expect(thirdBox).not.toBeNull();
  const afterX = thirdLinkBox!.x + thirdLinkBox!.width + 2;
  expect(afterX).toBeLessThan(thirdBox!.x + thirdBox!.width);
  await page.mouse.click(afterX, thirdLinkBox!.y + thirdLinkBox!.height / 2);
  await expect(third).toHaveAttribute("aria-pressed", "true");

  const score = firstCard.getByLabel(/Resultado local–visitante/);
  const scoreBox = await score.boundingBox();
  expect(scoreBox).not.toBeNull();
  await page.mouse.click(scoreBox!.x + scoreBox!.width / 2, scoreBox!.y + scoreBox!.height / 2);
  await expect(first).toHaveAttribute("aria-pressed", "true");
});

test("Real players: from a match's line-ups the profile opens on that match; another player returns to the line-ups", async ({ page }) => {
  await serveRealWithMatches(page);
  // Opened from Huesca's line-ups (not the newest match): that match is selected.
  await page.goto("/players/sofascore-player-966303?view=profile&from=observedMatch&origin=sofascore-match-16599388&originTab=lineups");
  const rows = page.getByRole("list", { name: "Histórico de partidos" }).getByRole("button");
  await expect(rows.nth(2)).toHaveAttribute("aria-pressed", "true");
  await expect(rows.nth(0)).toHaveAttribute("aria-pressed", "false");
  await expect(page.getByRole("link", { name: "← Volver", exact: true })).toHaveAttribute("href", "/matches/sofascore-match-16599388?tab=lineups");

  await rows.nth(0).click({ position: { x: 6, y: 6 } });
  await page.getByRole("link", { name: "Ver partido completo" }).click();
  await page.getByRole("tab", { name: "Alineaciones" }).click();
  await page.getByRole("tabpanel", { name: "Alineaciones" }).getByRole("link", { name: "Jugador Sin Estatura" }).click();
  await expect(page).toHaveURL(/\/players\/sofascore-player-1429049\?view=profile&from=observedMatch&origin=sofascore-match-16599928&originTab=lineups$/);
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page).toHaveURL(/\/matches\/sofascore-match-16599928\?tab=lineups$/);
  await expect(page.getByRole("tab", { name: "Alineaciones" })).toHaveAttribute("aria-selected", "true");
  // One level only: the parameters never pile up.
  expect(page.url().length).toBeLessThan(120);
});

test("Real players: an unknown or tampered selection selects nothing and says so", async ({ page }) => {
  await serveRealWithMatches(page);
  for (const match of ["sofascore-match-404", "%2F%2Fexample.com"]) {
    await page.goto(`/players/sofascore-player-966303?view=profile&match=${match}`);
    const rows = page.getByRole("list", { name: "Histórico de partidos" }).getByRole("button");
    await expect(rows).toHaveCount(3);
    if (match === "sofascore-match-404") {
      await expect(page.getByText("El partido indicado no figura entre los partidos observados de este jugador.")).toBeVisible();
      await expect(page.locator("[aria-pressed=true]")).toHaveCount(0);
    } else {
      // Not a valid id: ignored, the default selection applies.
      await expect(rows.nth(0)).toHaveAttribute("aria-pressed", "true");
    }
  }
});

test("Real players: direct and tampered match links fall back to internal destinations", async ({ page }) => {
  await serveRealWithMatches(page);
  for (const player of ["%2F%2Fexample.com", "https%3A%2F%2Fexample.com", "..%2Fteams", ""]) {
    await page.goto(`/matches/sofascore-match-16599928?view=observed&from=player&player=${player}`);
    await expect(page.getByRole("link", { name: "‹ Volver a partidos" })).toHaveAttribute("href", "/matches");
  }
  await page.goto("/matches/sofascore-match-16599928?view=observed&from=player&player=sofascore-player-966303&season=..%2Fx&playerFrom=team&playerOrigin=%2F%2Fexample.com");
  await expect(page.getByRole("link", { name: "‹ Volver al jugador" }))
    .toHaveAttribute("href", "/players/sofascore-player-966303?view=profile&match=sofascore-match-16599928");
  await page.getByRole("link", { name: "‹ Volver al jugador" }).click();
  await page.getByRole("link", { name: "← Volver", exact: true }).click();
  await expect(page).toHaveURL(/\/teams\?tab=players$/);

  // An id the real source does not know keeps the way back to the profile.
  await page.goto("/matches/sofascore-match-999?view=observed&from=player&player=sofascore-player-966303");
  await expect(page.getByRole("heading", { name: "Partido no encontrado en la fuente real" })).toBeVisible();
  await expect(page.getByRole("link", { name: "‹ Volver al jugador" }))
    .toHaveAttribute("href", "/players/sofascore-player-966303?view=profile&match=sofascore-match-999");
});
