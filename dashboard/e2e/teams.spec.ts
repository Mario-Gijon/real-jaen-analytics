import { expect, test, type Page } from "@playwright/test";
import { expectEntityAwareRowHover, expectInlineEntityHover, expectKeyboardFocus } from "./interactive-row";

const noHorizontalOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);

test("Equipos: Real Jaén by default, team change, tabs and direct player navigation", async ({
  page,
}, testInfo) => {
  const phone = testInfo.project.name === "mobile";
  await page.goto("/teams");
  const grid = page.getByRole("group", { name: "Equipo" });
  await expect(page.getByRole("heading", { level: 2, name: "Real Jaén CF" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 3, name: "Rendimiento" })).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("teams-performance.png"), fullPage: true });

  // Phones fold the grid behind «Cambiar equipo»; desktop always shows it.
  const change = page.getByRole("button", { name: "Cambiar equipo" });
  if (phone) {
    await expect(grid).toBeHidden();
    await change.click();
    await expect(grid).toBeVisible();
    await expect(grid.getByRole("radio", { name: "Real Jaén CF" })).toBeFocused();
  } else {
    await expect(change).toBeHidden();
    await expect(grid).toBeVisible();
  }
  await expect(grid.getByRole("radio", { name: "Real Jaén CF" })).toBeChecked();
  await grid.getByText("Montealto").click();
  await expect(page.getByRole("heading", { level: 2, name: "CD Montealto" })).toBeVisible();
  if (phone) {
    await expect(grid).toBeHidden();
    await expect(change).toBeFocused();
  }

  await page.getByRole("tab", { name: "Jugadores" }).click();
  const table = page.getByRole("table", { name: "Jugadores de la plantilla" });
  await expect(table).toBeVisible();
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("teams-players.png"), fullPage: true });

  const first = table.getByRole("link", { name: /Ver ficha de/ }).first();
  const name = (await first.getAttribute("aria-label"))!.replace("Ver ficha de ", "");
  if (!phone) {
    const playerRow = first.locator("xpath=../../..");
    await expectInlineEntityHover(page, playerRow, playerRow.locator("td").first(), first);
  }
  await expectKeyboardFocus(page, first);
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("link", { name: "\u2190 Volver", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "CD Montealto" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Jugadores" })).toHaveAttribute("aria-selected", "true");
});

test("Rendimiento resalta solo las zonas libres de cada fila de partido", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Hover feedback is checked with a fine pointer.");
  await page.goto("/teams");
  const table = page.getByRole("table", { name: "Partidos disputados" });
  const row = table.locator("tbody tr").first();
  const opponent = row.getByRole("link", { name: /^Ver equipo/ });
  await page.mouse.move(0, 0);
  const baseBackground = await row.evaluate((element) => getComputedStyle(element).backgroundColor);
  await expectEntityAwareRowHover(page, row, row.locator("td").first(), opponent);

  const matchLink = row.getByRole("link", { name: /^Ver partido/ });
  await page.mouse.move(0, 0);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(baseBackground);
  await expectKeyboardFocus(page, matchLink);
  const hoverColor = await page.evaluate(() => {
    const probe = document.createElement("span");
    probe.style.cssText = "position:fixed;visibility:hidden;background:var(--rj-bg-hover)";
    document.body.append(probe);
    const color = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return color;
  });
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(hoverColor);
  const opponentBox = await opponent.boundingBox();
  expect(opponentBox).not.toBeNull();
  await page.mouse.move(opponentBox!.x + opponentBox!.width / 2, opponentBox!.y + opponentBox!.height / 2);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(baseBackground);
  const freeBox = await row.locator("td").first().boundingBox();
  expect(freeBox).not.toBeNull();
  await page.mouse.move(freeBox!.x + freeBox!.width / 2, freeBox!.y + freeBox!.height / 2);
  await expect.poll(() => row.evaluate((element) => getComputedStyle(element).backgroundColor)).toBe(hoverColor);
  await matchLink.press("Enter");
  await expect(page).toHaveURL(/\/matches\/match-/);
});

test("Rendimiento: Temporada and Últimos 3 with the three matches highlighted", async ({
  page,
}, testInfo) => {
  await page.goto("/teams");
  const output = page.getByRole("region", { name: "Rendimiento" });
  await expect(output.getByRole("region", { name: "Ataque" })).toBeVisible();
  await expect(output.getByRole("region", { name: "Defensa" })).toBeVisible();
  await expect(output).not.toContainText("vs temporada");
  await page.screenshot({ path: testInfo.outputPath("rendimiento-temporada.png"), fullPage: true });

  await output.getByRole("button", { name: "Últimos 3" }).click();
  await expect(output).toContainText("vs temporada");
  const history = page.getByRole("table", { name: "Partidos disputados" });
  await expect(history.locator("tbody tr")).toHaveCount(13);
  await expect(history.locator("tbody tr[data-in-period]")).toHaveCount(3);
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: testInfo.outputPath("rendimiento-ultimos3.png"), fullPage: true });
});

test("a rival link changes the selected team inside /teams from the shared URL", async ({ page }) => {
  await page.goto("/teams");
  const table = page.getByRole("table", { name: "Partidos disputados" });
  const link = table.getByRole("link", { name: /^Ver equipo/ }).first();
  const href = await link.getAttribute("href");
  const name = (await link.getAttribute("aria-label"))!.replace("Ver equipo ", "");
  await link.click();
  await expect(page).toHaveURL(new RegExp(href!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));
  await expect(page.getByRole("heading", { level: 2, name })).toBeVisible();
  const group = page.getByRole("group", { name: "Equipo" });
  if (!await group.isVisible()) await page.getByRole("button", { name: "Cambiar equipo" }).click();
  await expect(group.getByRole("radio", { name })).toBeChecked();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Partidos: mobile rows fit at narrow widths and keep venue, crest, name and score distinct", async ({ page }, testInfo) => {
  await page.goto("/teams");
  const table = page.getByRole("table", { name: "Partidos disputados" });
  const widths = [320, 360, 375, 390, 430];
  for (const width of widths) {
    await page.setViewportSize({ width, height: 844 });
    expect(await noHorizontalOverflow(page), `horizontal overflow at ${width}px`).toBe(true);
    const row = table.locator("tbody tr").first();
    const condition = row.locator("td:nth-child(4) abbr[aria-label]");
    const conditionName = await condition.getAttribute("aria-label");
    expect(["Local", "Visitante"]).toContain(conditionName);
    await expect(condition).toHaveAccessibleName(conditionName!);
    await expect(condition).toContainText(conditionName === "Local" ? "L" : "V");
    const round = await row.locator("td:nth-child(1)").boundingBox();
    const date = await row.locator("td:nth-child(2)").boundingBox();
    const rival = await row.locator("td:nth-child(4)").boundingBox();
    const venue = await condition.boundingBox();
    const crest = await row.locator("td:nth-child(4) [data-crest]").boundingBox();
    const teamLink = row.getByRole("link", { name: /^Ver equipo/ });
    const team = await teamLink.boundingBox();
    const teamText = await teamLink.locator("span").boundingBox();
    const result = await row.locator("td:nth-child(5)").boundingBox();
    expect(round).not.toBeNull();
    expect(date).not.toBeNull();
    expect(rival).not.toBeNull();
    expect(venue).not.toBeNull();
    expect(crest).not.toBeNull();
    expect(team).not.toBeNull();
    expect(teamText).not.toBeNull();
    expect(result).not.toBeNull();
    expect(round!.x + round!.width).toBeLessThanOrEqual(date!.x + 1);
    expect(date!.x + date!.width).toBeLessThanOrEqual(rival!.x + 1);
    expect(venue!.x + venue!.width).toBeLessThanOrEqual(crest!.x + 1);
    expect(crest!.x + crest!.width).toBeLessThanOrEqual(team!.x + 1);
    expect(team!.x + team!.width).toBeLessThanOrEqual(result!.x + 1);
    expect(Math.abs(team!.x - teamText!.x)).toBeLessThanOrEqual(1);
    expect(Math.abs(team!.width - teamText!.width)).toBeLessThanOrEqual(1);
    expect(await row.locator("td:nth-child(4) [data-crest]").evaluate((crest) => crest.closest("a"))).toBeNull();
  }

  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(table.locator("thead th")).toHaveCount(5);
  await expect(table.getByRole("columnheader", { name: "Condición" })).toBeVisible();
  await expect(table.locator("tbody tr").first().locator("td:nth-child(4) abbr[aria-label]")).toBeHidden();
  expect(await noHorizontalOverflow(page)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("team-matches-desktop.png"), fullPage: true });

  await page.setViewportSize({ width: 320, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("team-matches-320.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("team-matches-390.png"), fullPage: true });

  const row = table.locator("tbody tr").first();
  const teamLink = row.getByRole("link", { name: /^Ver equipo/ });
  await expectKeyboardFocus(page, teamLink);
  const teamHref = await teamLink.getAttribute("href");
  await teamLink.locator("span").click();
  await expect(page).toHaveURL(new RegExp(teamHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const beforeRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  await beforeRow.scrollIntoViewIfNeeded();
  const beforeLink = beforeRow.getByRole("link", { name: /^Ver equipo/ });
  const beforeBox = await beforeLink.boundingBox();
  const beforeMatchHref = await beforeRow.locator("a[data-row-primary]").getAttribute("href");
  expect(beforeBox).not.toBeNull();
  await page.mouse.click(beforeBox!.x - 2, beforeBox!.y + beforeBox!.height / 2);
  await expect(page).toHaveURL(new RegExp(beforeMatchHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const afterRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  await afterRow.scrollIntoViewIfNeeded();
  const afterLink = afterRow.getByRole("link", { name: /^Ver equipo/ });
  const afterBox = await afterLink.boundingBox();
  const afterCell = await afterRow.locator("td:nth-child(4)").boundingBox();
  const afterMatchHref = await afterRow.locator("a[data-row-primary]").getAttribute("href");
  expect(afterBox).not.toBeNull();
  expect(afterCell).not.toBeNull();
  expect(afterBox!.x + afterBox!.width + 2).toBeLessThan(afterCell!.x + afterCell!.width);
  await page.mouse.click(afterBox!.x + afterBox!.width + 2, afterBox!.y + afterBox!.height / 2);
  await expect(page).toHaveURL(new RegExp(afterMatchHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const crestRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  const crestMatchHref = await crestRow.locator("a[data-row-primary]").getAttribute("href");
  await crestRow.locator("td:nth-child(4) [data-crest]").click();
  await expect(page).toHaveURL(new RegExp(crestMatchHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const hoverRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  if (testInfo.project.name === "desktop") {
    await expectEntityAwareRowHover(page, hoverRow, hoverRow.locator("td:nth-child(1)"), hoverRow.getByRole("link", { name: /^Ver equipo/ }));
  }

  await page.goto("/teams");
  const badgeRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  const badgeHref = await badgeRow.locator("a[data-row-primary]").getAttribute("href");
  await badgeRow.locator("td:nth-child(4) abbr[aria-label]").click();
  await expect(page).toHaveURL(new RegExp(badgeHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const freeRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  const freeMatchHref = await freeRow.locator("a[data-row-primary]").getAttribute("href");
  await freeRow.locator("td:nth-child(1)").click();
  await expect(page).toHaveURL(new RegExp(freeMatchHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const resultRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  const resultHref = await resultRow.locator("a[data-row-primary]").getAttribute("href");
  await resultRow.locator("a[data-row-primary]").click();
  await expect(page).toHaveURL(new RegExp(resultHref!.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$"));

  await page.goto("/teams");
  const keyboardRow = page.getByRole("table", { name: "Partidos disputados" }).locator("tbody tr").first();
  const matchLink = keyboardRow.getByRole("link", { name: /^Ver partido/ });
  await expectKeyboardFocus(page, matchLink);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/matches\/match-/);
});

test("Evolución: the result chips stay on one line and scroll sideways if needed", async ({
  page,
}) => {
  await page.goto("/teams");
  const sequence = page.getByRole("list", { name: "Resultados por jornada" });
  const chips = sequence.getByRole("button");
  await expect(chips).toHaveCount(13);
  const first = (await chips.first().boundingBox())!;
  const last = (await chips.last().boundingBox())!;
  expect(Math.abs(first.y - last.y)).toBeLessThan(1);
  // The latest result is in view and the detail line still follows the selection.
  await expect(chips.last()).toBeInViewport();
  await chips.first().focus();
  await expect(chips.first()).toBeInViewport();
  await expect(page.getByText("CF Peñalbar", { exact: true }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(
    true,
  );
});
