import { expect, test } from "@playwright/test";

test("opens on Inicio and moves to Equipos with the synthetic-data notice", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("region", { name: "Encuentro de preparación" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  await expect(page.getByText("Demostración · datos sintéticos", { exact: true })).toBeVisible();

  const nav = page.getByRole("navigation", { name: "Secciones" });
  await expect(nav.getByRole("link")).toHaveText(["Inicio", "Partidos", "Equipos"]);
  await nav.getByRole("link", { name: "Equipos" }).click();
  await expect(page).toHaveURL(/\/teams$/);
  await expect(page.getByRole("tab", { name: "Rendimiento del equipo" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Equipos" })).toHaveAttribute("aria-current", "page");

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  await nav.getByRole("link", { name: "Partidos" }).click();
  await expect(page.getByRole("combobox", { name: "Jornada", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: "Partidos" })).toHaveAttribute("aria-current", "page");
});
