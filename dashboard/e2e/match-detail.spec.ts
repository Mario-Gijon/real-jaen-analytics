import { expect, test } from "@playwright/test";

test("Match detail: full-row keyboard navigation and responsive summary", async ({ page }) => {
  await page.goto("/matches");
  await page.getByRole("combobox", { name: "Jornada", exact: true }).selectOption("1");
  const match = page.getByRole("link", { name: /Real Jaén CF contra.*jornada 1,/ });
  await match.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/matches\/match-r01-01$/);
  await expect(page.getByText("2 – 1", { exact: true })).toBeVisible();
  await expect(page.getByText("Demostración · datos sintéticos", { exact: true })).toBeVisible();
  await expect(page.getByRole("tabpanel", { name: "Resumen" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Cronología" })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test("Match detail: zero, missing statistics, postponed and scheduled states", async ({ page }) => {
  for (const [id, text] of [
    ["match-r02-01", "0 – 0"],
    ["match-r05-01", "Dato no recogido"],
    ["match-r11-01", "Dato no disponible"],
    ["match-r09-01", "Fecha por definir"],
    ["match-r15-01", "Programado"],
  ]) {
    await page.goto(`/matches/${id}`);
    if (text.startsWith("Dato")) {
      await expect(page.getByRole("tabpanel").getByLabel(new RegExp(text))).toHaveCount(2);
    } else await expect(page.getByText(text, { exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
});
