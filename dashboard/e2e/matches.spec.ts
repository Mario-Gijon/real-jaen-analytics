import { expect, test } from "@playwright/test";
import { expectEntityAwareRowHover } from "./interactive-row";

test("Partidos: current round, combined filters and full calendar without horizontal scroll", async ({ page }) => {
  await page.goto("/matches");
  const round = page.getByRole("combobox", { name: "Jornada", exact: true });
  const team = page.getByRole("combobox", { name: "Equipo", exact: true });
  await expect(round).toHaveValue("14");
  const list = page.getByRole("list", { name: "Partidos de la temporada" });
  await expect(list.getByRole("link")).toHaveCount(30);
  await expect(page.getByText("Demostración · datos sintéticos", { exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: "Real Jaén CF", exact: true })).toBeAttached();
  await team.selectOption("team-real-jaen");
  await expect(list.getByRole("link")).toHaveCount(3);
  await round.selectOption("all");
  await expect(list.getByRole("link")).toHaveCount(114);
  await expect(list.getByText("Finalizado", { exact: true })).toHaveCount(13);
  await expect(list.getByText("Programado", { exact: true })).toHaveCount(24);
  await expect(list.getByText("Aplazado", { exact: true })).toHaveCount(1);
  await expect(list.getByText("Jornada 9", { exact: true })).toBeVisible();
  await expect(list.getByRole("link", { name: /Ver partido/ }).last()).toHaveAccessibleName(/Fecha por definir/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const first = list.getByRole("link", { name: /Ver partido/ }).first();
  await first.focus();
  await expect(first).toBeFocused();
  await expect(first).toHaveAttribute("href", /^\/matches\//);
  expect(await first.evaluate((element) => getComputedStyle(element).outlineStyle)).not.toBe("none");
  await round.selectOption("8");
  await expect(list.getByRole("link")).toHaveCount(3);
  await expect(list.getByRole("link", { name: /Ver partido/ })).toHaveAccessibleName(/jornada 8/);
  await page.getByRole("button", { name: "Jornada siguiente", exact: true }).click();
  await expect(round).toHaveValue("9");
  await expect(list.getByText("Aplazado", { exact: true })).toBeVisible();
  await team.selectOption("all");
  await expect(list.getByRole("link")).toHaveCount(30);
  await round.selectOption("38");
  await expect(page.getByRole("button", { name: "Jornada siguiente", exact: true })).toBeDisabled();
  await expect(list.getByText("Programado", { exact: true })).toHaveCount(10);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Partidos sintéticos: the team text opens its team; nearby space, score and crest open the match", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/matches");
  const list = page.getByRole("list", { name: "Partidos de la temporada" });
  const item = list.getByRole("listitem").first();
  const row = item.locator(":scope > div");
  const teamLink = item.getByRole("link", { name: /^Ver equipo/ }).nth(1);
  await expect(teamLink.locator("[data-crest]")).toHaveCount(0);
  const teamHref = new URL((await teamLink.getAttribute("href"))!, page.url()).href;
  const teamBox = await teamLink.boundingBox();
  expect(teamBox).not.toBeNull();
  await page.mouse.click(teamBox!.x + teamBox!.width / 2, teamBox!.y + teamBox!.height / 2);
  await expect(page).toHaveURL(teamHref);
  await page.goBack();
  await expect(list).toBeVisible();

  const nameBox = await item.getByRole("link", { name: /^Ver equipo/ }).nth(1).boundingBox();
  const rowBox = await row.boundingBox();
  expect(nameBox).not.toBeNull();
  expect(rowBox).not.toBeNull();
  for (const x of [nameBox!.x - 2, nameBox!.x + nameBox!.width + 2]) {
    expect(x).toBeGreaterThan(rowBox!.x);
    expect(x).toBeLessThan(rowBox!.x + rowBox!.width);
    const matchHref = new URL((await item.getByRole("link", { name: /^Ver partido/ }).getAttribute("href"))!, page.url()).href;
    await page.mouse.click(x, nameBox!.y + nameBox!.height / 2);
    await expect(page).toHaveURL(matchHref);
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

test("Partidos sintéticos resaltan la fila en zonas libres y separan el hover del nombre", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "desktop", "Hover feedback is checked with a fine pointer.");
  await page.goto("/matches");
  const row = page.getByRole("list", { name: "Partidos de la temporada" }).getByRole("listitem").first()
    .locator(":scope > div");
  const teamLink = row.getByRole("link", { name: /^Ver equipo/ }).nth(1);
  await expectEntityAwareRowHover(page, row, row.locator(":scope > div").first(), teamLink);
});
